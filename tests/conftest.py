import asyncio

import graphene
import pytest
from graphene_file_upload.scalars import Upload
from starlette.applications import Starlette
from starlette.testclient import TestClient

from starlette_graphene3 import GraphQLApp


class User(graphene.ObjectType):
    id = graphene.ID()
    name = graphene.String()


class Query(graphene.ObjectType):
    me = graphene.Field(User)
    user = graphene.Field(User, id=graphene.ID(required=True))
    user_async = graphene.Field(User, id=graphene.ID(required=True))
    user_error = graphene.Field(User, id=graphene.ID(required=True))
    user_async_error = graphene.Field(User, id=graphene.ID(required=True))
    show_connection_params = graphene.Field(graphene.String)
    custom_context_value = graphene.Int()

    def resolve_me(root, info):
        return {"id": "john", "name": "John"}

    def resolve_user(root, info, id):
        return {"id": id, "name": id.capitalize()}

    async def resolve_user_async(root, info, id):
        return {"id": id, "name": id.capitalize()}

    def resolve_user_error(root, info, id):
        raise ValueError("error")

    async def resolve_user_async_error(root, info, id):
        raise ValueError("error")

    async def resolve_custom_context_value(root, info):
        return info.context["my"]

    def resolve_show_connection_params(root, info):
        return str(info.context["request"].scope["connection_params"])


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
    raiseError1 = graphene.Int()
    raiseError2 = graphene.Int()
    broken = graphene.Int()

    async def subscribe_count(root, info, upto=3):
        for i in range(upto):
            yield i
            await asyncio.sleep(0.01)

    async def subscribe_raiseError1(root, info):
        raise ValueError

    async def subscribe_raiseError2(root, info):
        yield 0
        raise ValueError

    async def subscribe_broken(root, info, extra_param):
        raise RuntimeError
        yield 0


@pytest.fixture
def schema():
    return graphene.Schema(query=Query, mutation=Mutation, subscription=Subscription)


@pytest.fixture
def client(schema):
    app = Starlette()
    app.mount("/", GraphQLApp(schema))
    return TestClient(app)


@pytest.fixture
def client_with_context(schema):
    app = Starlette()

    async def context(request):
        return {"request": request, "my": 123}

    app.mount("/", GraphQLApp(schema, context_value=context))
    return TestClient(app)


@pytest.fixture
def files(schema):
    filename = "test.txt"
    data = b"hello, world!"
    files = {
        "0": (filename, data, "text/plain"),
        "1": (filename, data, "text/plain"),
        "2": (filename, data, "text/plain"),
    }
    return files
