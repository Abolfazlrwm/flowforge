# Examples

Runnable programs against a FlowForge deployment. Each one states its own
environment requirements at the top.

| Example | What it shows |
| --- | --- |
| [`01_publish_and_run.py`](01_publish_and_run.py) | The smallest complete program: publish a three-task pipeline, trigger it, wait, report. |
| [`02_watch_events.py`](02_watch_events.py) | Following a run's event log — the clearest way to tell "slow" from "stuck". |
| [`03_requeue_failed.py`](03_requeue_failed.py) | The operator workflow for a failed run: list eligible tasks, then requeue them. Dry-run by default. |
| [`04_cli_walkthrough.sh`](04_cli_walkthrough.sh) | The `flowforge` CLI end to end, with an exit-code assertion at every step. |

## Prerequisites

```bash
pip install flowforge-client
docker compose up -d --build
docker compose exec -T web python manage.py e2e_token

export FLOWFORGE_URL=http://localhost:8000
export FLOWFORGE_TOKEN="$(docker compose exec -T web cat /tmp/e2e_token | tr -d '\r\n')"
```

Then:

```bash
python examples/01_publish_and_run.py
```

`01` publishes its own workflow, so it is the one to run first — the others
assume a run exists. `04` uses the `sample-e2e` definition that
`manage.py sample_run` publishes, so run that once before it:

```bash
docker compose exec -T web python manage.py sample_run
```

## Why these examples and not more

They cover the three things a client actually does — submit work, observe it, and
intervene when it fails — rather than cataloguing every endpoint. The full
surface is in the [SDK guide](../docs/sdk/index.md) and the
[API reference](../docs/api/reference.md).
