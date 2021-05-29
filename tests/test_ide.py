from starlette.applications import Starlette
from starlette.testclient import TestClient

from starlette_graphene3 import (
    GraphQLApp,
    make_graphiql_handler,
    make_playground_handler,
)


def test_http_get_graphiql_enabled(schema):
    app = Starlette()
    app.mount("/", GraphQLApp(schema, on_get=make_graphiql_handler()))
    client = TestClient(app)
    assert client.get("/").status_code == 200


def test_http_get_playground_enabled(schema):
    app = Starlette()
    app.mount("/", GraphQLApp(schema, on_get=make_playground_handler()))
    client = TestClient(app)
    assert client.get("/").status_code == 200

    app = Starlette()
    app.mount("/", GraphQLApp(schema, playground=True))
    client = TestClient(app)
    assert client.get("/").status_code == 200


def test_http_get_ide_disabled(schema):
    app = Starlette()
    app.mount("/", GraphQLApp(schema, on_get=None))
    client = TestClient(app)
    assert client.get("/").status_code == 405


def test_http_unsupported_method(client):
    res = client.put("/")
    assert res.status_code == 405

    res = client.patch("/")
    assert res.status_code == 405
