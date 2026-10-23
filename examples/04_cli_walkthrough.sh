#!/usr/bin/env bash
# A CLI smoke test against a running stack.
#
#   docker compose up -d --build
#   docker compose exec -T web python manage.py e2e_token
#   export FLOWFORGE_URL=http://localhost:8000
#   export FLOWFORGE_TOKEN="$(docker compose exec -T web cat /tmp/e2e_token | tr -d '\r\n')"
#   ./examples/04_cli_walkthrough.sh
#
# Every step asserts an exit code, so the script fails at the first thing that is
# wrong rather than printing a wall of output that looks plausible.
set -euo pipefail

: "${FLOWFORGE_URL:?set FLOWFORGE_URL (e.g. http://localhost:8000)}"
: "${FLOWFORGE_TOKEN:?set FLOWFORGE_TOKEN (see `manage.py e2e_token`)}"

step() { printf '\n=== %s ===\n' "$1"; }

step "readiness"
flowforge health

step "publish a workflow"
# The CLI has no publish subcommand: publishing a definition from a shell heredoc
# is a worse experience than doing it from Python or the API. The stack's sample
# workflow is published by `manage.py sample_run`, so this walkthrough uses the
# definition the compose e2e already created.
flowforge workflows

step "show it"
flowforge show sample-e2e

step "trigger it and wait"
# `trigger` waits by default and exits non-zero if the run does not succeed, which
# is what makes it usable as a CI step.
flowforge trigger sample-e2e

step "list runs"
flowforge runs

step "the last run's detail"
run_id="$(flowforge --json runs | python3 -c 'import json,sys; print(json.load(sys.stdin)[0]["id"])')"
flowforge status "$run_id"

step "its event log"
flowforge events "$run_id"

printf '\nall steps passed\n'
