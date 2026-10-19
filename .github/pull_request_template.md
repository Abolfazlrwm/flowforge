## What this changes

<!-- One paragraph. What behaviour is different after this PR, and why. -->

## Phase

<!-- Which roadmap phase, or "none" for infrastructure and docs. -->

Phase:

## Checklist

- [ ] Tests cover the new behaviour (or the PR explains why none are needed)
- [ ] `make check` passes locally (ruff, ruff format, mypy strict, pytest)
- [ ] `uv run python manage.py makemigrations --check --dry-run` reports no changes
- [ ] `CHANGELOG.md` updated under `Unreleased`
- [ ] Docs updated if behaviour or the public surface changed
- [ ] No secret, credential or private hostname is introduced
- [ ] Commit messages follow Conventional Commits

## Correctness

<!--
This project cares most about what happens when things go wrong. Answer the
one that applies, or delete this section.
-->

- [ ] This change can duplicate work (at-least-once delivery is the guarantee —
      exactly-once is not claimed)
- [ ] This change affects a lease, a fencing token, or a state transition
- [ ] This change is read-only / cosmetic

## Verification

<!--
Paste the evidence, not a claim. CI run URL, command output, probe payload.
"Tests pass" is not evidence; the job name and result is.
-->

```
```

## Known limitations introduced

<!-- Anything deliberately left undone, so it does not become invisible. -->
