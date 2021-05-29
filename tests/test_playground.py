from starlette.applications import Starlette
from starlette.testclient import TestClient

from starlette_graphene3 import GraphQLApp


def test_http_get_playground_enabled(schema):
    app = Starlette()
    app.mount("/", GraphQLApp(schema))
    client = TestClient(app)

    assert client.get("/").status_code == 200


def test_http_get_playground_disabled(schema):
    app = Starlette()
    app.mount("/", GraphQLApp(schema, IDE=None))
    client = TestClient(app)

    assert client.get("/").status_code == 405


def test_http_unsupported_method(client):
    res = client.put("/")
    assert res.status_code == 405

    res = client.patch("/")
    assert res.status_code == 405
