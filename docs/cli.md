# Command Line

The `flowforge` command ships with `flowforge-client` and is a thin layer over the
[Python SDK](sdk/index.md): every subcommand maps to one client call, so the CLI
cannot do anything the SDK cannot.

```bash
pip install flowforge-client
```

## Configuration

Environment first, flags second — a CI job sets the environment once and then runs
several commands.

| Variable | Meaning |
| --- | --- |
| `FLOWFORGE_URL` | API base URL, e.g. `http://localhost:8000` |
| `FLOWFORGE_TOKEN` | A DRF token or an `ff_...` API key |

There is no default URL. A CLI that guesses a host is worse than one that refuses,
so a missing URL is a usage error (exit 2) with a message naming the variable.

## Exit codes

| Code | Meaning |
| --- | --- |
| `0` | Success |
| `1` | The command failed: an API error, a transport error, a timeout, or a run that did not succeed |
| `2` | Usage error (argparse's own code, plus the missing-URL case) |

A script can branch on the code without parsing output. `trigger` and `status`
exit `1` when the run ended `failed`, `dead` or `cancelled` — that is what makes
`flowforge trigger` usable as a CI step.

## Commands

### `flowforge health`

The readiness payload, one dependency per line. Exits `1` when the deployment is
not ready, so it works as a container health check.

```console
$ flowforge health
status: ready
postgres: ok
redis: ok
rabbitmq: ok
```

### `flowforge workflows`

Published definitions.

```console
$ flowforge workflows
NAME    VERSION  TASKS  CREATED
etl     1        3      2026-10-23 10:00:00+00:00
```

### `flowforge show <name> [--version N]`

One definition, task by task.

```console
$ flowforge show etl
etl v1
  Extract and load.
KEY      TYPE  DEPENDS ON
-------  ----  ----------
extract  echo
load     noop  extract
```

### `flowforge trigger <name> [--version N]`

Trigger a run and follow it to a terminal state. Generates an idempotency key
unless you pass one.

| Flag | Meaning |
| --- | --- |
| `--version N` | Workflow version (default 1) |
| `--idempotency-key K` | Replay instead of starting a second run |
| `--no-wait` | Return as soon as the run is accepted |
| `--wait-timeout S` | Seconds to wait (default 300) |

```console
$ flowforge trigger etl
triggered run 4f2c… (etl v1)
run 4f2c…  etl v1  [succeeded]
TASK     STATE      ATTEMPT  STARTED                    FINISHED
-------  ---------  -------  -------------------------  -------------------------
extract  succeeded  1        2026-10-23 10:00:01+00:00  2026-10-23 10:00:01+00:00
load     succeeded  1        2026-10-23 10:00:06+00:00  2026-10-23 10:00:06+00:00
```

`--no-wait` is the one to use when you are queuing work rather than watching it:

```console
$ flowforge trigger etl --no-wait
triggered run 4f2c… (etl v1)
```

### `flowforge runs`

Every run visible to the credential.

```console
$ flowforge runs
ID                                    WORKFLOW  VERSION  STATE
------------------------------------  --------  -------  ---------
4f2c…                                 etl       1        succeeded
```

### `flowforge status <run-id>`

One run, task by task. Exits `1` if the run ended badly.

### `flowforge events <run-id>`

The append-only event log — the clearest way to see what the engine did.

```console
$ flowforge events 4f2c…
WHEN                       TASK     KIND     TRANSITION
-------------------------  -------  -------  ------------------
2026-10-23 10:00:00+00:00  extract  promote  pending -> ready
2026-10-23 10:00:01+00:00  extract  transition  ready -> running
2026-10-23 10:00:01+00:00  extract  transition  running -> succeeded
```

### `flowforge requeue <task-run-id>`

Move a failed or dead task back to `ready`.

```console
$ flowforge requeue 9a1b…
task extract is now ready
```

## `--json`

Global flag: print the raw payload instead of a table. Combine with `jq` or a
short `python -c` to script against it.

```bash
flowforge --json runs | jq -r '.[0].id'
```

## A CI step

```bash
export FLOWFORGE_URL=https://flowforge.internal
export FLOWFORGE_TOKEN="$FLOWFORGE_TOKEN"

flowforge health
flowforge trigger nightly-etl --wait-timeout 900
```

The step fails if the deployment is not ready or the run does not succeed, with
the failing task printed by `trigger`'s table.

## What the CLI does not do

- **No `publish` subcommand.** Publishing a definition from a shell heredoc is a
  worse experience than doing it from Python or the API; the definition is a
  structured document, not a flag.
- **No `cancel`.** The API has no cancellation endpoint yet.
- **No config file.** Two environment variables are easier to explain than a
  config file with a precedence order.
