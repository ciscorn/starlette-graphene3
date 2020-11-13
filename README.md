# starlette_graphene3

An ASGI app for Graphene v3 (version 3). This can replace `starlette.graphql.GraphQLApp` that is made for Graphene v2.

[![codecov](https://codecov.io/gh/ciscorn/starlette-graphene3/branch/master/graph/badge.svg)](https://codecov.io/gh/ciscorn/starlette-graphene3)

It supports:

- WebSockets (Subscriptions)
- File uploading (https://github.com/jaydenseric/graphql-multipart-request-spec)
- GraphQL Playground

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
app.mount("/", GraphQLApp(schema))
```
