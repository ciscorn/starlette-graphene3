import asyncio
import json
import logging
from inspect import isawaitable
from typing import (
    Any,
    AsyncGenerator,
    Awaitable,
    Callable,
    Dict,
    List,
    Optional,
    Type,
    Union,
    cast,
)

import graphene
from graphql import (
    ExecutionContext,
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

ContextValue = Union[Any, Callable[[HTTPConnection], Any]]
RootValue = Any


def make_graphiql_handler():
    def handler(request: Request) -> Response:
        return HTMLResponse(_GRAPHIQL_HTML)

    return handler


def make_playground_handler(playground_options=None):
    playground_options_str = json.dumps(playground_options or {})
    content = _PLAYGROUND_HTML.replace("PLAYGROUND_OPTIONS", playground_options_str)

    def handler(request: Request) -> Response:
        return HTMLResponse(content)

    return handler


class GraphQLApp:
    def __init__(
        self,
        schema: graphene.Schema,
        *,
        on_get: Optional[
            Callable[[Request], Union[Response, Awaitable[Response]]]
        ] = None,
        context_value: ContextValue = None,
        root_value: RootValue = None,
        middleware: Optional[Middleware] = None,
        error_formatter: Callable[[GraphQLError], Dict[str, Any]] = format_error,
        logger_name: Optional[str] = None,
        playground: bool = False,  # deprecating
        execution_context_class: Optional[Type[ExecutionContext]] = None,
    ):
        self.schema = schema
        self.on_get = on_get
        self.context_value = context_value
        self.root_value = root_value
        self.error_formatter = error_formatter
        self.middleware = middleware
        self.execution_context_class = execution_context_class
        self.logger = logging.getLogger(logger_name or __name__)

        if playground and self.on_get is None:
            self.on_get = make_playground_handler()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            request = Request(scope=scope, receive=receive)
            response: Optional[Response] = None
            if request.method == "POST":
                response = await self._handle_http_request(request)
            elif request.method == "GET":
                response = await self._get_on_get(request)

            if not response:
                response = Response(status_code=405)
            await response(scope, receive, send)

        elif scope["type"] == "websocket":
            websocket = WebSocket(scope=scope, receive=receive, send=send)
            await self._run_websocket_server(websocket)

        else:
            raise ValueError(f"Unsupported scope type: ${scope['type']}")

    async def _get_on_get(self, request: Request) -> Optional[Response]:
        handler = self.on_get

        if handler is None:
            return None

        response = handler(request)
        if isawaitable(response):
            return await cast(Awaitable, response)
        else:
            return cast(Response, response)

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
        except ValueError as e:
            return JSONResponse({"errors": [e.args[0]]}, status_code=400)

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
            execution_context_class=self.execution_context_class,
        )

        response: Dict[str, Any] = {"data": result.data}
        if result.errors:
            for error in result.errors:
                if error.original_error:
                    self.logger.error(
                        "An exception occurred in resolvers",
                        exc_info=error.original_error,
                    )
            response["errors"] = [
                self.error_formatter(error) for error in result.errors
            ]

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
                    "payload": self.error_formatter(errors[0]),
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
            execution_context_class=self.execution_context_class,
        )

        if isinstance(result, ExecutionResult) and result.errors:
            return result.errors

        if isawaitable(result):
            result = await cast(Awaitable[ExecutionResult], result)

        result = cast(ExecutionResult, result)

        payload: Dict[str, Any] = {}
        payload["data"] = result.data
        if result.errors:
            for error in result.errors:
                if error.original_error:
                    self.logger.error(
                        "An exception occurred in resolvers",
                        exc_info=error.original_error,
                    )
            payload["errors"] = [self.error_formatter(error) for error in result.errors]

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
                payload = {"data": result.data}
                await websocket.send_json(
                    {"type": GQL_DATA, "id": operation_id, "payload": payload}
                )
        except Exception as error:
            if not isinstance(error, GraphQLError):
                self.logger.error("An exception occurred in resolvers", exc_info=error)
                error = GraphQLError(str(error), original_error=error)
            await websocket.send_json(
                {
                    "type": GQL_DATA,
                    "id": operation_id,
                    "payload": {"errors": [self.error_formatter(error)]},
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


_PLAYGROUND_HTML = """
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

_GRAPHIQL_HTML = """
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
    #graphiql {
      height: 100vh;
    }
  </style>
  <link href="//unpkg.com/graphiql/graphiql.css" rel="stylesheet"/>
  <script src="//unpkg.com/react@16/umd/react.production.min.js"></script>
  <script src="//unpkg.com/react-dom@16/umd/react-dom.production.min.js"></script>
  <script src="//unpkg.com/subscriptions-transport-ws@0.7.0/browser/client.js"></script>
  <script src="//unpkg.com/graphiql-subscriptions-fetcher@0.0.2/browser/client.js"></script>
</head>
<body>
  <script src="//unpkg.com/graphiql/graphiql.min.js"></script>
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
    var graphqlParamNames = {
      query: true,
      variables: true,
      operationName: true
    };
    var otherParams = {};
    for (var k in parameters) {
      if (parameters.hasOwnProperty(k) && graphqlParamNames[k] !== true) {
        otherParams[k] = parameters[k];
      }
    }
    var fetchURL = '?' + Object.keys(otherParams).map(function (key) {
      return encodeURIComponent(key) + '=' +
          encodeURIComponent(otherParams[key]);
      }
    ).join('&');

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

    // if variables was provided, try to format it.
    if (parameters.variables) {
      try {
        parameters.variables =
          JSON.stringify(JSON.parse(parameters.variables), null, 2);
      } catch (e) {
        // Do nothing, we want to display the invalid JSON as a string, rather
        // than present an error.
      }
    }

    // When the query and variables string is edited, update the URL bar so
    // that it can be easily shared
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
    var subscriptionsEndpoint = (location.protocol === 'http:' ? 'ws' : 'wss') + '://' + location.host + location.pathname;
    var subscriptionsClient = new window.SubscriptionsTransportWs.SubscriptionClient(subscriptionsEndpoint, {
      reconnect: true
    });
    var fetcher = window.GraphiQLSubscriptionsFetcher.graphQLFetcher(subscriptionsClient, graphQLFetcher);

    // Render <GraphiQL /> into the body.
    ReactDOM.render(
      React.createElement(GraphiQL, {
        fetcher: fetcher,
        query: parameters.query,
        variables: parameters.variables,
        operationName: parameters.operationName,
        onEditQuery: onEditQuery,
        onEditVariables: onEditVariables,
        onEditOperationName: onEditOperationName,
      }),
      document.body
    );
  </script>
</body>
</html>
""".strip()  # noqa: B950
