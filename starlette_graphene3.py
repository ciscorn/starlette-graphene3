import asyncio
import json
from inspect import isawaitable
from typing import (
    Any,
    AsyncGenerator,
    Awaitable,
    Callable,
    Dict,
    List,
    Optional,
    Union,
    cast,
)

import graphene
from graphql import (
    ExecutionResult,
    GraphQLError,
    Middleware,
    OperationType,
    execute,
    format_error,
    graphql,
    parse,
    subscribe,
    validate,
)
from graphql.language.ast import DocumentNode, OperationDefinitionNode
from graphql.utilities import get_operation_ast
from starlette.background import BackgroundTasks
from starlette.datastructures import UploadFile
from starlette.requests import HTTPConnection, Request
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.types import Receive, Scope, Send
from starlette.websockets import WebSocket, WebSocketDisconnect, WebSocketState

GQL_CONNECTION_ACK = "connection_ack"
GQL_CONNECTION_ERROR = "connection_error"
GQL_CONNECTION_INIT = "connection_init"
GQL_CONNECTION_TERMINATE = "connection_terminate"
GQL_COMPLETE = "complete"
GQL_DATA = "data"
GQL_ERROR = "error"
GQL_START = "start"
GQL_STOP = "stop"

ContextValue = Union[Any, Callable[[Any], Any]]
RootValue = Any


class GraphQLApp:
    def __init__(
        self,
        schema: graphene.Schema,
        IDE: str = "playground",
        context_value: ContextValue = None,
        root_value: RootValue = None,
        middleware: Optional[Middleware] = None,
        playground_options: Optional[Dict[str, Any]] = None,
    ):
        self.schema = schema
        self.IDE = IDE
        self.context_value = context_value
        self.root_value = root_value
        self.middleware = middleware
        self.playground_options_str = json.dumps(playground_options or {})

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            request = Request(scope=scope, receive=receive)
            response: Response
            if request.method == "GET" and self.IDE == "graphiql":
                body = GRAPHIQL_HTML
                response = HTMLResponse(body)
            elif request.method == "GET" and self.IDE == "playground":
                body = PLAYGROUND_HTML.replace(
                    "PLAYGROUND_OPTIONS", self.playground_options_str
                )
                response = HTMLResponse(body)
            elif request.method == "POST":
                response = await self._handle_http_request(request)
            else:
                response = Response(status_code=405)
            await response(scope, receive, send)
        elif scope["type"] == "websocket":
            print("WS")
            websocket = WebSocket(scope=scope, receive=receive, send=send)
            await self._run_websocket_server(websocket)
        else:
            raise ValueError(f"Unsupported scope type: ${scope['type']}")

    async def _get_context_value(self, request: HTTPConnection) -> Any:
        if callable(self.context_value):
            context = self.context_value(request)
            if isawaitable(context):
                context = await context
            return context
        else:
            return self.context_value or {
                "request": request,
                "background": BackgroundTasks(),
            }

    async def _handle_http_request(self, request: Request) -> JSONResponse:
        try:
            operations = await _get_operation_from_request(request)
        except ValueError as error:
            return JSONResponse({"errors": [error.args[0]]}, status_code=400)

        if isinstance(operations, list):
            return JSONResponse(
                {"errors": ["This server does not support batching"]}, status_code=400
            )
        else:
            operation = operations

        query = operation["query"]
        variable_values = operation.get("variables")
        operation_name = operation.get("operationName")
        context_value = await self._get_context_value(request)

        result = await graphql(
            self.schema.graphql_schema,
            source=query,
            context_value=context_value,
            root_value=self.root_value,
            middleware=self.middleware,
            variable_values=variable_values,
            operation_name=operation_name,
        )

        response: Dict[str, Any] = {"data": result.data}
        if result.errors:
            response["errors"] = [format_error(error) for error in result.errors]

        return JSONResponse(
            response,
            status_code=200,
            background=context_value.get("background"),
        )

    async def _run_websocket_server(self, websocket: WebSocket) -> None:
        subscriptions: Dict[str, AsyncGenerator] = {}
        await websocket.accept("graphql-ws")
        try:
            while (
                websocket.client_state != WebSocketState.DISCONNECTED
                and websocket.application_state != WebSocketState.DISCONNECTED
            ):
                message = await websocket.receive_json()
                await self._handle_websocket_message(message, websocket, subscriptions)
        except WebSocketDisconnect:
            pass
        finally:
            if subscriptions:
                await asyncio.gather(
                    *(
                        subscriptions[operation_id].aclose()
                        for operation_id in subscriptions
                    )
                )

    async def _handle_websocket_message(
        self,
        message: dict,
        websocket: WebSocket,
        subscriptions: Dict[str, AsyncGenerator],
    ):
        operation_id = cast(str, message.get("id"))
        message_type = cast(str, message.get("type"))

        if message_type == GQL_CONNECTION_INIT:
            websocket.scope["connection_params"] = message.get("payload")
            await websocket.send_json({"type": GQL_CONNECTION_ACK})
        elif message_type == GQL_CONNECTION_TERMINATE:
            await websocket.close()
        elif message_type == GQL_START:
            await self._ws_on_start(
                message.get("payload"), operation_id, websocket, subscriptions
            )
        elif message_type == GQL_STOP:
            if operation_id in subscriptions:
                await subscriptions[operation_id].aclose()
                del subscriptions[operation_id]

    async def _ws_on_start(
        self,
        data: Any,
        operation_id: str,
        websocket: WebSocket,
        subscriptions: Dict[str, AsyncGenerator],
    ):
        query = data["query"]
        variable_values = data.get("variables")
        operation_name = data.get("operationName")
        context_value = await self._get_context_value(websocket)
        errors: List[GraphQLError] = []
        operation: Optional[OperationDefinitionNode] = None
        document: Optional[DocumentNode] = None

        try:
            document = parse(query)
            operation = get_operation_ast(document, operation_name)
            errors = validate(self.schema.graphql_schema, document)
        except GraphQLError as e:
            errors = [e]

        if not errors:
            if operation and operation.operation == OperationType.SUBSCRIPTION:
                errors = await self._start_subscription(
                    websocket,
                    operation_id,
                    subscriptions,
                    document,
                    context_value,
                    variable_values,
                    operation_name,
                )
            else:
                errors = await self._handle_query_over_ws(
                    websocket,
                    operation_id,
                    subscriptions,
                    document,
                    context_value,
                    variable_values,
                    operation_name,
                )

        if errors:
            await websocket.send_json(
                {
                    "type": GQL_ERROR,
                    "id": operation_id,
                    "payload": format_error(errors[0]),
                }
            )

    async def _handle_query_over_ws(
        self,
        websocket,
        operation_id,
        subscriptions,
        document,
        context_value,
        variable_values,
        operation_name,
    ) -> List[GraphQLError]:
        result = execute(
            self.schema.graphql_schema,
            document,
            root_value=self.root_value,
            context_value=context_value,
            variable_values=variable_values,
            operation_name=operation_name,
            middleware=self.middleware,
        )

        if isinstance(result, ExecutionResult) and result.errors:
            return result.errors

        if isawaitable(result):
            result = await cast(Awaitable[ExecutionResult], result)

        result = cast(ExecutionResult, result)

        payload: Dict[str, Any] = {}
        payload["data"] = result.data
        if result.errors:
            payload["errors"] = [format_error(error) for error in result.errors]

        await websocket.send_json(
            {"type": GQL_DATA, "id": operation_id, "payload": payload}
        )
        return []

    async def _start_subscription(
        self,
        websocket,
        operation_id,
        subscriptions,
        document,
        context_value,
        variable_values,
        operation_name,
    ) -> List[GraphQLError]:
        result = await subscribe(
            self.schema.graphql_schema,
            document,
            context_value=context_value,
            root_value=self.root_value,
            variable_values=variable_values,
            operation_name=operation_name,
        )

        if isinstance(result, ExecutionResult) and result.errors:
            return result.errors

        asyncgen = cast(AsyncGenerator, result)
        subscriptions[operation_id] = asyncgen
        asyncio.create_task(
            self._observe_subscription(asyncgen, operation_id, websocket)
        )
        return []

    async def _observe_subscription(
        self, asyncgen: AsyncGenerator, operation_id: str, websocket: WebSocket
    ) -> None:
        try:
            async for result in asyncgen:
                payload = {}
                payload["data"] = result.data
                await websocket.send_json(
                    {"type": GQL_DATA, "id": operation_id, "payload": payload}
                )
        except Exception as error:
            if not isinstance(error, GraphQLError):
                error = GraphQLError(str(error), original_error=error)
            await websocket.send_json(
                {
                    "type": GQL_DATA,
                    "id": operation_id,
                    "payload": {"errors": [format_error(error)]},
                }
            )

        if (
            websocket.client_state != WebSocketState.DISCONNECTED
            and websocket.application_state != WebSocketState.DISCONNECTED
        ):
            await websocket.send_json({"type": GQL_COMPLETE, "id": operation_id})


async def _get_operation_from_request(request: Request):
    content_type = request.headers.get("Content-Type", "").split(";")[0]
    if content_type == "application/json":
        try:
            return await request.json()
        except (TypeError, ValueError):
            raise ValueError("Request body is not a valid JSON")
    elif content_type == "multipart/form-data":
        return await _get_operation_from_multipart(request)
    else:
        raise ValueError("Content-type must be application/json or multipart/form-data")


async def _get_operation_from_multipart(request: Request):
    try:
        request_body = await request.form()
    except Exception:
        raise ValueError("Request body is not a valid multipart/form-data")

    try:
        operations = json.loads(request_body.get("operations"))
    except (TypeError, ValueError):
        raise ValueError("'operations' must be a valid JSON")
    if not isinstance(operations, (dict, list)):
        raise ValueError("'operations' field must be an Object or an Array")

    try:
        name_path_map = json.loads(request_body.get("map"))
    except (TypeError, ValueError):
        raise ValueError("'map' field must be a valid JSON")
    if not isinstance(name_path_map, dict):
        raise ValueError("'map' field must be an Object")

    files = {k: v for (k, v) in request_body.items() if isinstance(v, UploadFile)}
    for (name, paths) in name_path_map.items():
        for path in paths:
            path = tuple(path.split("."))
            _inject_file_to_operations(operations, files[name], path)

    return operations


def _inject_file_to_operations(ops_tree, _file, path):
    key = path[0]
    try:
        key = int(key)
    except ValueError:
        pass
    if len(path) == 1:
        if ops_tree[key] is None:
            ops_tree[key] = _file
    else:
        _inject_file_to_operations(ops_tree[key], _file, path[1:])


PLAYGROUND_HTML = """
<!DOCTYPE html>
<html>
<head>
  <meta charset=utf-8/>
  <meta name="viewport" content="user-scalable=no, initial-scale=1.0, minimum-scale=1.0, maximum-scale=1.0, minimal-ui">
  <title>GraphQL Playground</title>
  <link rel="stylesheet" href="//cdn.jsdelivr.net/npm/graphql-playground-react/build/static/css/index.css" />
  <link rel="shortcut icon" href="//cdn.jsdelivr.net/npm/graphql-playground-react/build/favicon.png" />
  <script src="//cdn.jsdelivr.net/npm/graphql-playground-react/build/static/js/middleware.js"></script>
</head>
<body>
  <div id="root">
    <style>
      body {
        background-color: rgb(23, 42, 58);
        font-family: Open Sans, sans-serif;
        height: 90vh;
      }
      #root {
        height: 100%;
        width: 100%;
        display: flex;
        align-items: center;
        justify-content: center;
      }
      .loading {
        font-size: 32px;
        font-weight: 200;
        color: rgba(255, 255, 255, .6);
        margin-left: 20px;
      }
      img {
        width: 78px;
        height: 78px;
      }
      .title {
        font-weight: 400;
      }
    </style>
    <img src='//cdn.jsdelivr.net/npm/graphql-playground-react/build/logo.png' alt=''>
    <div class="loading"> Loading
      <span class="title">GraphQL Playground</span>
    </div>
  </div>
  <script>window.addEventListener('load', function (event) {
      GraphQLPlayground.init(document.getElementById('root'), PLAYGROUND_OPTIONS)
    })</script>
</body>
</html>
""".strip()  # noqa: B950

GRAPHIQL_HTML = """
<!--
The request to this GraphQL server provided the header "Accept: text/html"
and as a result has been presented GraphiQL - an in-browser IDE for
exploring GraphQL.
If you wish to receive JSON, provide the header "Accept: application/json" or
add "&raw" to the end of the URL within a browser.
-->
<!DOCTYPE html>
<html>
<head>
  <style>
    html, body {
      height: 100%;
      margin: 0;
      overflow: hidden;
      width: 100%;
    }
  </style>
  <link href="//cdn.jsdelivr.net/npm/graphiql@0.12.0/graphiql.css" rel="stylesheet"/>
  <script src="//cdn.jsdelivr.net/npm/whatwg-fetch@2.0.3/fetch.min.js"></script>
  <script src="//cdn.jsdelivr.net/npm/react@16.2.0/umd/react.production.min.js"></script>
  <script src="//cdn.jsdelivr.net/npm/react-dom@16.2.0/umd/react-dom.production.min.js"></script>
  <script src="//cdn.jsdelivr.net/npm/graphiql@0.12.0/graphiql.min.js"></script>
  <script src="//unpkg.com/subscriptions-transport-ws@0.7.0/browser/client.js"></script>
  <script src="//unpkg.com/graphiql-subscriptions-fetcher@0.0.2/browser/client.js"></script>
</head>
<body>
  <script>
    // Parse the cookie value for a CSRF token
    var csrftoken;
    var cookies = ('; ' + document.cookie).split('; csrftoken=');
    if (cookies.length == 2)
      csrftoken = cookies.pop().split(';').shift();

    // Collect the URL parameters
    var parameters = {};
    window.location.search.substr(1).split('&').forEach(function (entry) {
      var eq = entry.indexOf('=');
      if (eq >= 0) {
        parameters[decodeURIComponent(entry.slice(0, eq))] =
          decodeURIComponent(entry.slice(eq + 1));
      }
    });
    // Produce a Location query string from a parameter object.
    function locationQuery(params) {
      return '?' + Object.keys(params).map(function (key) {
        return encodeURIComponent(key) + '=' +
          encodeURIComponent(params[key]);
      }).join('&');
    }
    // Derive a fetch URL from the current URL, sans the GraphQL parameters.
    var graphqlParamNames = {
      query: true,
      variables: true,
      operationName: true
    };
    var otherParams = '';
    for (var k in parameters) {
      if (parameters.hasOwnProperty(k) && graphqlParamNames[k] !== true) {
        otherParams[k] = parameters[k];
      }
    }
    var fetchURL = locationQuery(otherParams);
    // Defines a GraphQL fetcher using the fetch API.
    function graphQLFetcher(graphQLParams) {
      var headers = {
        'Accept': 'application/json',
        'Content-Type': 'application/json'
      };
      if (csrftoken) {
        headers['X-CSRFToken'] = csrftoken;
      }
      return fetch(fetchURL, {
        method: 'post',
        headers: headers,
        body: JSON.stringify(graphQLParams),
        credentials: 'include',
      }).then(function (response) {
        return response.text();
      }).then(function (responseBody) {
        try {
          return JSON.parse(responseBody);
        } catch (error) {
          return responseBody;
        }
      });
    }
    // When the query and variables string is edited, update the URL bar so
    // that it can be easily shared.
    function onEditQuery(newQuery) {
      parameters.query = newQuery;
      updateURL();
    }
    function onEditVariables(newVariables) {
      parameters.variables = newVariables;
      updateURL();
    }
    function onEditOperationName(newOperationName) {
      parameters.operationName = newOperationName;
      updateURL();
    }
    function updateURL() {
      history.replaceState(null, null, locationQuery(parameters));
    }
    var fetcher;
    if (true) {
      var subscriptionsEndpoint = (location.protocol === 'http:' ? 'ws' : 'wss') + '://' + location.host + location.pathname;
      var subscriptionsClient = new window.SubscriptionsTransportWs.SubscriptionClient(subscriptionsEndpoint, {
        reconnect: true
      });
      fetcher = window.GraphiQLSubscriptionsFetcher.graphQLFetcher(subscriptionsClient, graphQLFetcher);
    } else {
      fetcher = graphQLFetcher;
    }
    // Render <GraphiQL /> into the body.
    ReactDOM.render(
      React.createElement(GraphiQL, {
        fetcher: fetcher,
        onEditQuery: onEditQuery,
        onEditVariables: onEditVariables,
        onEditOperationName: onEditOperationName,
        query: '',
        response: '',
      }),
      document.body
    );
  </script>
</body>
</html>
""".strip()  # noqa: B950
