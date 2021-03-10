from starlette_graphene3 import (
    GQL_COMPLETE,
    GQL_CONNECTION_ACK,
    GQL_CONNECTION_INIT,
    GQL_CONNECTION_TERMINATE,
    GQL_DATA,
    GQL_ERROR,
    GQL_START,
    GQL_STOP,
)


def test_single_subscription(client):
    with client.websocket_connect("/", "graphql-ws") as ws:
        ws.send_json({"type": GQL_CONNECTION_INIT})
        msg = ws.receive_json()
        assert msg["type"] == GQL_CONNECTION_ACK
        ws.send_json(
            {
                "type": GQL_START,
                "id": "q1",
                "payload": {
                    "query": r"subscription($upto: Int) { count(upto: $upto) }",
                    "variables": {"upto": 3},
                    "operationName": None,
                },
            }
        )
        for i in range(3):
            msg = ws.receive_json()
            assert msg["type"] == GQL_DATA
            assert msg["id"] == "q1"
            assert msg["payload"]["data"]["count"] == i

        ws.send_json({"type": GQL_STOP, "id": "q1"})
        msg = ws.receive_json()
        assert msg["type"] == GQL_COMPLETE
        assert msg["id"] == "q1"
        ws.send_json({"type": GQL_CONNECTION_TERMINATE})


def test_single_subscription_invalid_query(client):
    with client.websocket_connect("/", "graphql-ws") as ws:
        ws.send_json({"type": GQL_CONNECTION_INIT})
        msg = ws.receive_json()
        assert msg["type"] == GQL_CONNECTION_ACK
        ws.send_json(
            {
                "type": GQL_START,
                "id": "q1",
                "payload": {"query": r"subscription { <invalid query> }"},
            }
        )
        msg = ws.receive_json()
        assert msg["type"] == GQL_ERROR
        assert "message" in msg["payload"]
        assert "locations" in msg["payload"]


def test_single_subscription_error1(client):
    with client.websocket_connect("/", "graphql-ws") as ws:
        ws.send_json({"type": GQL_CONNECTION_INIT})
        msg = ws.receive_json()
        assert msg["type"] == GQL_CONNECTION_ACK
        ws.send_json(
            {
                "type": GQL_START,
                "id": "q1",
                "payload": {"query": r"subscription { raiseError1 }"},
            }
        )
        msg = ws.receive_json()
        assert msg["type"] == GQL_ERROR
        assert "message" in msg["payload"]
        assert "locations" in msg["payload"]


def test_single_subscription_error2(client):
    with client.websocket_connect("/", "graphql-ws") as ws:
        ws.send_json({"type": GQL_CONNECTION_INIT})
        msg = ws.receive_json()
        assert msg["type"] == GQL_CONNECTION_ACK
        ws.send_json(
            {
                "type": GQL_START,
                "id": "q1",
                "payload": {"query": r"subscription { raiseError2 }"},
            }
        )
        msg = ws.receive_json()
        assert msg["type"] == GQL_DATA
        assert msg["id"] == "q1"
        assert msg["payload"]["data"]["raiseError2"] == 0
        msg = ws.receive_json()
        assert msg["type"] == GQL_DATA
        assert "errors" in msg["payload"]


def test_single_subscription_broken(client):
    with client.websocket_connect("/", "graphql-ws") as ws:
        ws.send_json({"type": GQL_CONNECTION_INIT})
        msg = ws.receive_json()
        assert msg["type"] == GQL_CONNECTION_ACK
        ws.send_json(
            {
                "type": GQL_START,
                "id": "q1",
                "payload": {"query": r"subscription { broken }"},
            }
        )
        msg = ws.receive_json()
        assert msg["type"] == GQL_ERROR


def test_named_subscription(client):
    with client.websocket_connect("/", "graphql-ws") as ws:
        ws.send_json({"type": GQL_CONNECTION_INIT})
        msg = ws.receive_json()
        assert msg["type"] == GQL_CONNECTION_ACK
        ws.send_json(
            {
                "type": GQL_START,
                "id": "q1",
                "payload": {
                    "query": r"subscription counter($upto: Int) { count(upto: $upto) }",
                    "variables": {"upto": 3},
                    "operationName": "counter",
                },
            }
        )
        for i in range(3):
            msg = ws.receive_json()
            assert msg["type"] == GQL_DATA
            assert msg["id"] == "q1"
            assert msg["payload"]["data"]["count"] == i

        ws.send_json({"type": GQL_STOP, "id": "q1"})
        msg = ws.receive_json()
        assert msg["type"] == GQL_COMPLETE
        assert msg["id"] == "q1"
        ws.send_json({"type": GQL_CONNECTION_TERMINATE})


def test_immediate_disconnect(client):
    with client.websocket_connect("/", "graphql-ws") as ws:
        ws.send_json({"type": GQL_CONNECTION_INIT})
        response = ws.receive_json()
        assert response["type"] == GQL_CONNECTION_ACK
        ws.send_json({"type": GQL_CONNECTION_TERMINATE})


def test_subscribe_then_disconnect(client):
    with client.websocket_connect("/", "graphql-ws") as ws:
        ws.send_json({"type": GQL_CONNECTION_INIT})
        response = ws.receive_json()
        assert response["type"] == GQL_CONNECTION_ACK
        ws.send_json(
            {
                "type": GQL_START,
                "id": "q1",
                "payload": {
                    "query": r"subscription counter($upto: Int) { count(upto: $upto) }",
                    "variables": {"upto": 3},
                    "operationName": "counter",
                },
            }
        )


def test_stop(client):
    with client.websocket_connect("/", "graphql-ws") as ws:
        ws.send_json({"type": GQL_CONNECTION_INIT})
        msg = ws.receive_json()
        assert msg["type"] == GQL_CONNECTION_ACK
        ws.send_json(
            {
                "type": GQL_START,
                "id": "q1",
                "payload": {
                    "query": r"subscription($upto: Int) { count(upto: $upto) }",
                    "variables": {"upto": 5},
                },
            }
        )
        for i in range(2):
            msg = ws.receive_json()
            assert msg["type"] == GQL_DATA
            assert msg["id"] == "q1"
            assert msg["payload"]["data"]["count"] == i

        ws.send_json({"type": GQL_STOP, "id": "q1"})
        msg = ws.receive_json()
        assert msg["type"] == GQL_COMPLETE or msg["type"] == GQL_DATA
        assert msg["id"] == "q1"
        if msg["type"] == GQL_DATA:
            msg = ws.receive_json()
            assert msg["type"] == GQL_COMPLETE or msg["type"] == GQL_DATA
            assert msg["id"] == "q1"
        ws.send_json({"type": GQL_CONNECTION_TERMINATE})


def test_invalid_value(client):
    with client.websocket_connect("/", "graphql-ws") as ws:
        ws.send_json({"type": GQL_CONNECTION_INIT})
        msg = ws.receive_json()
        assert msg["type"] == GQL_CONNECTION_ACK
        ws.send_json(
            {
                "type": GQL_START,
                "id": "q1",
                "payload": {
                    "query": r"subscription($upto: Int) { count(upto: $upto) }",
                    "variables": {"upto": "INVALIDVALUE"},
                },
            }
        )
        msg = ws.receive_json()
        assert msg["type"] == GQL_ERROR
        assert msg["id"] == "q1"
        ws.send_json({"type": GQL_CONNECTION_TERMINATE})


def test_query_over_ws(client):
    with client.websocket_connect("/", "graphql-ws") as ws:
        ws.send_json({"type": GQL_CONNECTION_INIT})
        msg = ws.receive_json()
        assert msg["type"] == GQL_CONNECTION_ACK
        ws.send_json(
            {
                "type": GQL_START,
                "id": "q1",
                "payload": {
                    "query": r'query { userAsync(id: "alice") { name } }',
                    "operationName": None,
                },
            }
        )
        msg = ws.receive_json()
        assert msg["type"] == GQL_DATA
        assert msg["id"] == "q1"
        assert msg["payload"]["data"]["userAsync"]["name"] == "Alice"
        ws.send_json({"type": GQL_CONNECTION_TERMINATE})


def test_query_over_ws_error(client):
    with client.websocket_connect("/", "graphql-ws") as ws:
        ws.send_json({"type": GQL_CONNECTION_INIT})
        msg = ws.receive_json()
        assert msg["type"] == GQL_CONNECTION_ACK
        ws.send_json(
            {
                "type": GQL_START,
                "id": "q1",
                "payload": {
                    "query": r'query { userError(id: "alice") { name } }',
                    "operationName": None,
                },
            }
        )
        msg = ws.receive_json()
        assert msg["type"] == GQL_ERROR
        ws.send_json({"type": GQL_CONNECTION_TERMINATE})

    with client.websocket_connect("/", "graphql-ws") as ws:
        ws.send_json({"type": GQL_CONNECTION_INIT})
        msg = ws.receive_json()
        assert msg["type"] == GQL_CONNECTION_ACK
        ws.send_json(
            {
                "type": GQL_START,
                "id": "q1",
                "payload": {
                    "query": r"query { userAsync(id: NOT_STRING) { name } }",
                    "operationName": None,
                },
            }
        )
        msg = ws.receive_json()
        assert msg["type"] == GQL_ERROR
        ws.send_json({"type": GQL_CONNECTION_TERMINATE})

    with client.websocket_connect("/", "graphql-ws") as ws:
        ws.send_json({"type": GQL_CONNECTION_INIT})
        msg = ws.receive_json()
        assert msg["type"] == GQL_CONNECTION_ACK
        ws.send_json(
            {
                "type": GQL_START,
                "id": "q1",
                "payload": {
                    "query": r'query { userAsyncError(id: "alice") { name } }',
                    "operationName": None,
                },
            }
        )
        msg = ws.receive_json()
        assert msg["type"] == GQL_DATA
        assert msg["payload"].get("errors")
        ws.send_json({"type": GQL_CONNECTION_TERMINATE})


def test_query_over_ws_with_variables_and_opname(client):
    with client.websocket_connect("/", "graphql-ws") as ws:
        ws.send_json({"type": GQL_CONNECTION_INIT})
        msg = ws.receive_json()
        assert msg["type"] == GQL_CONNECTION_ACK
        ws.send_json(
            {
                "type": GQL_START,
                "id": "q1",
                "payload": {
                    "query": (
                        r"query getUser($id: ID!) { user(id: $id) { name } }"
                        r"query me { me { name } }"
                    ),
                    "variables": {"id": "bob"},
                    "operationName": "getUser",
                },
            }
        )
        msg = ws.receive_json()
        assert msg["type"] == GQL_DATA
        assert msg["id"] == "q1"
        assert msg["payload"]["data"]["user"]["name"] == "Bob"
        ws.send_json({"type": GQL_CONNECTION_TERMINATE})


def test_connection_params(client):
    with client.websocket_connect("/", "graphql-ws") as ws:
        ws.send_json({"type": GQL_CONNECTION_INIT, "payload": {"authToken": "dummy"}})
        msg = ws.receive_json()
        assert msg["type"] == GQL_CONNECTION_ACK
        ws.send_json(
            {
                "type": GQL_START,
                "id": "q1",
                "payload": {
                    "query": r"query { showConnectionParams }",
                    "operationName": None,
                },
            }
        )
        msg = ws.receive_json()
        assert msg["type"] == GQL_DATA
        assert msg["id"] == "q1"
        assert (
            msg["payload"]["data"]["showConnectionParams"] == "{'authToken': 'dummy'}"
        )
        ws.send_json({"type": GQL_CONNECTION_TERMINATE})
