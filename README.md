# starlette-graphene3

A simple ASGI app for using [Graphene](https://github.com/graphql-python/graphene) v3 with [Starlette](https://github.com/encode/starlette).

![Test](https://github.com/ciscorn/starlette-graphene3/actions/workflows/test.yml/badge.svg?branch=master)
[![codecov](https://codecov.io/gh/ciscorn/starlette-graphene3/branch/master/graph/badge.svg)](https://codecov.io/gh/ciscorn/starlette-graphene3)

It supports:

- Queries and Mutations (over HTTP or WebSocket)
- Subscriptions (over WebSocket)
- File uploading (https://github.com/jaydenseric/graphql-multipart-request-spec)
- GraphQL Playground


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
from starlette_graphene3 import GraphQLApp


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
app.mount("/", GraphQLApp(schema, IDE="playground")) # playground IDE
# app.mount("/", GraphQLApp(schema, IDE="graphiql")) # graphiql IDE
# app.mount("/", GraphQLApp(schema, IDE=None)) # no IDE
```

## GraphQLApp

`GraphQLApp(schema, [options...])`

- (required) `schema`: graphene.Schema
- (optional) `IDE` (default: `playground`): choses the GraphQL IDE. It can be `graphiql`, `playground`, or `None` (disables IDE).
- (optional) `context_value` (default: `None`)
- (optional) `root_value` (default: `None`)
- (optional) `middleware` (default: `None`)
- (optional) `playground_options` (default: `None`)
