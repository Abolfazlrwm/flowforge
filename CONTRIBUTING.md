# Contributing to FlowForge

Thank you for your interest in contributing.

This project is built phase by phase against an explicit roadmap
(`docs/development/ROADMAP.md`). A contribution that argues a planned design is
wrong is more valuable than one that adds a feature the roadmap has not reached
yet — but please open an issue before writing a large patch.

## Getting Started

Prerequisites: Python 3.13, [`uv`](https://docs.astral.sh/uv/), and Docker with
Compose v2.20 or newer if you want to run the full stack.

```bash
git clone https://github.com/Abolfazlrwm/flowforge.git
cd flowforge

# Installs the exact dependency graph from uv.lock. Do not use pip here:
# uv.lock is the source of truth for CI and for the container image.
uv sync --locked --extra dev

pre-commit install
cp .env.example .env   # then fill in the values
```

## Development Workflow

1. Create a branch from `main`:
   `feat/short-slug`, `fix/short-slug`, `docs/short-slug` or `chore/short-slug`.
2. Make the change, with tests.
3. Run everything CI runs:

   ```bash
   make check          # ruff, ruff format --check, mypy strict, pytest
   ```

4. Commit with [Conventional Commits](https://www.conventionalcommits.org/).
   The body should say *why*, not restate the diff.
5. Push and open a pull request.

## Branch protection on `main`

`main` is protected. Direct pushes are rejected; every change goes through a
pull request that must satisfy:

| Requirement | Value |
|-------------|-------|
| Required status checks | `lint`, `test`, `docker` |
| Branch must be up to date | yes (`strict`) |
| Required approving reviews | 0 — this is a single-maintainer project, and GitHub does not allow self-approval |
| Force pushes | blocked |
| Branch deletion | blocked |
| Conversation resolution | required |

Administrators are not exempt from the status checks but can bypass protection
in an emergency. If you had to, say so in the pull request.

## Code Standards

- **Linting and formatting:** Ruff, 100-character lines, double quotes, Google
  docstrings. `make format` fixes both.
- **Typing:** mypy in strict mode. `# type: ignore` needs a reason on the line.
- **Testing:** pytest with pytest-django. Coverage gate is 80%.
- **Migrations:** `python manage.py makemigrations --check --dry-run` must
  report no changes — commit the migration, not just the model.

## What a reviewable pull request looks like

- One concern. If the description needs the word "also", split it.
- Tests that would fail if the behaviour regressed. A test that passes against
  the broken code is not a test.
- Evidence, not claims. Paste the command output or the CI run URL. "Tests
  pass" is not evidence; `test PASS in 43s` with a link is.
- Anything deliberately left undone is written down, so it does not become
  invisible later.

## Correctness expectations

FlowForge claims **at-least-once** delivery. It does not claim exactly-once, and
a pull request that quietly assumes otherwise will be rejected. If your change
touches leases, fencing tokens, state transitions or the outbox, say in the pull
request what happens when a worker dies between two statements.

## Reporting Issues

Use the issue templates. Include steps to reproduce, expected versus actual
behaviour, and your versions. Check `docs/development/PROGRESS.md` first — the
behaviour may be a known limitation rather than a defect.

## Security Issues

Do not open a public issue for a security vulnerability. See
[SECURITY.md](SECURITY.md).
