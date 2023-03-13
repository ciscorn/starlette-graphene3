import json


def test_http_multipart(client, files):
    res = client.post(
        "/",
        data={
            "operations": json.dumps(
                {
                    "query": "mutation ($file: Upload!) { uploadFile(file: $file) { ok } }",
                    "variables": {"file": None},
                },
            ),
            "map": json.dumps({"0": ["variables.file"]}),
        },
        files=files,
    )
    assert res.json()["data"]["uploadFile"]["ok"] is True


def test_http_multipart_missing_file(client, files):
    res = client.post(
        "/",
        data={
            "operations": json.dumps(
                {
                    "query": "mutation ($file: Upload!) { uploadFile(file: $file) { ok } }",
                    "variables": {"file": None},
                },
            ),
            "map": json.dumps({"invalid_name": ["variables.file"]}),
        },
        files=files,
    )
    assert "errors" in res.json()


def test_http_invalid_multipart(client, files):
    res = client.post(
        "/", headers={"Content-type": "multipart/form-data"}, content="<broken>"
    )
    assert res.json().get("errors")

    valid_operations = json.dumps(
        {
            "query": "mutation ($file: Upload!) { uploadFile(file: $file) { ok } }",
            "variables": {"file": None},
        }
    )
    valid_map = json.dumps({"0": ["variables.file"]})

    res = client.post(
        "/",
        data={
            "operations": "<broken>",
            "map": valid_map,
        },
        files=files,
    )
    assert res.json().get("errors")

    res = client.post(
        "/",
        data={
            "operations": "1",
            "map": valid_map,
        },
        files=files,
    )
    assert res.json().get("errors")

    res = client.post(
        "/",
        data={"operations": valid_operations, "map": "<broken>"},
        files=files,
    )
    assert res.json().get("errors")

    res = client.post(
        "/",
        data={"operations": valid_operations, "map": 1},
        files=files,
    )
    assert res.json().get("errors")


def test_http_batching(client):
    filename = "test.txt"
    data = b"hello, world!"
    files = {
        "0": (filename, data, "text/plain"),
        "1": (filename, data, "text/plain"),
        "2": (filename, data, "text/plain"),
    }

    res = client.post(
        "/",
        data={
            "operations": json.dumps(
                [
                    {
                        "query": "mutation ($file: Upload!) { uploadFile(file: $file) { ok } }",
                        "variables": {"file": None},
                    },
                    {
                        "query": (
                            "mutation($files: [Upload!]!)"
                            "{ uploadFile(files: $files) { ok } }"
                        ),
                        "variables": {"files": [None, None]},
                    },
                ]
            ),
            "map": json.dumps(
                {
                    "0": ["0.variables.file"],
                    "1": ["1.variables.files.0"],
                    "2": ["1.variables.files.1"],
                }
            ),
        },
        files=files,
    )
    assert "errors" in res.json()
