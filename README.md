# starlette-graphene3

A simple ASGI app for using [Graphene](https://github.com/graphql-python/graphene) v3 with [Starlette](https://github.com/encode/starlette).

![Test](https://github.com/ciscorn/starlette-graphene3/actions/workflows/test.yml/badge.svg?branch=master)
[![codecov](https://codecov.io/gh/ciscorn/starlette-graphene3/branch/master/graph/badge.svg)](https://codecov.io/gh/ciscorn/starlette-graphene3)
[![pypi package](https://img.shields.io/pypi/v/starlette-graphene3?color=%2334D058&label=pypi%20package)](https://pypi.org/project/starlette-graphene3)

It supports:

- Queries and Mutations (over HTTP or WebSocket)
- Subscriptions (over WebSocket)
- File uploading (https://github.com/jaydenseric/graphql-multipart-request-spec)
- GraphiQL / GraphQL Playground


## Installation

```bash
pip3 install -U starlette-graphene3
```


## Example

```python
import asyncio

import graphene
from graphene_file_upload.scalars import Upload

from starlette.applications import Starlette
from starlette_graphene3 import GraphQLApp, make_graphiql_handler


class User(graphene.ObjectType):
    id = graphene.ID()
    name = graphene.String()


class Query(graphene.ObjectType):
    me = graphene.Field(User)

    def resolve_me(root, info):
        return {"id": "john", "name": "John"}


class FileUploadMutation(graphene.Mutation):
    class Arguments:
        file = Upload(required=True)

    ok = graphene.Boolean()

    def mutate(self, info, file, **kwargs):
        return FileUploadMutation(ok=True)


class Mutation(graphene.ObjectType):
    upload_file = FileUploadMutation.Field()


class Subscription(graphene.ObjectType):
    count = graphene.Int(upto=graphene.Int())

    async def subscribe_count(root, info, upto=3):
        for i in range(upto):
            yield i
            await asyncio.sleep(1)


app = Starlette()
schema = graphene.Schema(query=Query, mutation=Mutation, subscription=Subscription)

app.mount("/", GraphQLApp(schema, on_get=make_graphiql_handler()))  # Graphiql IDE

# app.mount("/", GraphQLApp(schema, on_get=make_playground_handler()))  # Playground IDE
# app.mount("/", GraphQLApp(schema)) # no IDE
```

## GraphQLApp

`GraphQLApp(schema, [options...])`

```python
class GraphQLApp:
    def __init__(
        self,
        schema: graphene.Schema,  # Requied
        *,
        # Optional keyword parameters
        on_get: Optional[
            Callable[[Request], Union[Response, Awaitable[Response]]]
        ] = None,  # optional HTTP handler for GET requests
        context_value: ContextValue = None,
        root_value: RootValue = None,
        middleware: Optional[Middleware] = None,
        error_formatter: Callable[[GraphQLError], Dict[str, Any]] = format_error,
        logger_name: Optional[str] = None,
        playground: bool = False,  # deprecating
        execution_context_class: Optional[Type[ExecutionContext]] = None,
    ):
```
