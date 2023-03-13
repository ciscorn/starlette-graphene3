def test_http_json(client):
    res = client.post("/", json={"query": r"query { me { name } }"})
    assert res.status_code == 200
    result = res.json()
    assert result["data"]["me"]["name"] == "John"
    assert "errors" not in result


def test_http_json_arg(client):
    res = client.post("/", json={"query": r'query { user(id: "alice") { name } }'})
    assert res.status_code == 200
    result = res.json()
    assert result["data"]["user"]["name"] == "Alice"
    assert "errors" not in result


def test_http_json_arg_async(client):
    res = client.post("/", json={"query": r'query { userAsync(id: "alice") { name } }'})
    assert res.status_code == 200
    result = res.json()
    assert result["data"]["userAsync"]["name"] == "Alice"
    assert "errors" not in result


def test_http_json_error(client):
    res = client.post("/", json={"query": r'query { userError(id: "alice") { name } }'})
    assert res.status_code == 200
    result = res.json()
    assert "errors" in result


def test_http_json_invalid_query(client):
    res = client.post("/", json={"query": r"query { user { name } }"})
    assert res.status_code == 200
    result = res.json()
    assert "errors" in result


def test_http_json_invalid_mimtype(client):
    res = client.post(
        "/",
        json={"query": r"query { me { name } }"},
        headers={"Content-Type": "text/plain"},
    )
    assert res.status_code == 400


def test_http_json_invalid_json(client):
    res = client.post(
        "/",
        content=r'+++{"query": "query { me { name } }"}+++',
        headers={"Content-Type": "application/json"},
    )
    assert res.status_code == 400


def test_http_json_variables(client):
    res = client.post(
        "/",
        json={
            "query": r"query($id: ID!) { user(id: $id) { name } }",
            "variables": {"id": "bob"},
        },
    )
    assert res.status_code == 200
    result = res.json()
    assert result["data"]["user"]["name"] == "Bob"
    assert "errors" not in result


def test_http_json_variables_and_opname(client):
    res = client.post(
        "/",
        json={
            "query": (
                r"query getUser($id: ID!) { user(id: $id) { name } }"
                r"query me { me { name } }"
            ),
            "variables": {"id": "bob"},
            "operationName": "getUser",
        },
    )
    assert res.status_code == 200
    result = res.json()
    assert result["data"]["user"]["name"] == "Bob"
    assert "errors" not in result
