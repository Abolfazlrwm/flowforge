# flowforge-client

A dependency-free Python client and CLI for the [FlowForge](../README.md) REST
API.

```bash
pip install flowforge-client
```

```python
from flowforge_client import FlowForge, new_idempotency_key

with FlowForge("http://localhost:8000", token="ff_...") as client:
    run = client.trigger("etl", version=1, idempotency_key=new_idempotency_key())
    finished = client.wait(run.id, timeout=120)
    print(finished.state)
```

## Why no dependencies

A client library is installed *next to* an application, and every dependency it
adds is a version that application cannot choose freely. This package uses
`urllib.request` from the standard library, so installing it cannot change a
resolution anywhere else.

## What it does not do

It does not import Django, the server models or the domain code. It talks HTTP,
which is why it can be installed in a service that has nothing to do with the
server.

## Documentation

- [SDK guide](https://Abolfazlrwm.github.io/flowforge/sdk/)
- [CLI reference](https://Abolfazlrwm.github.io/flowforge/cli/)
- [API reference](https://Abolfazlrwm.github.io/flowforge/api/reference/)

## Development

```bash
cd sdk
uv sync --extra dev
uv run pytest
uv run ruff check .
uv run mypy flowforge_client
```
