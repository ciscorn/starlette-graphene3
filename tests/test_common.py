def test_custom_context(client_with_context):
    res = client_with_context.post("/", json={"query": r"query { customContextValue }"})
    assert res.status_code == 200
    result = res.json()
    assert result["data"]["customContextValue"] == 123
    assert "errors" not in result
