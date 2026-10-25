# Release checklist — v1.0.0 (DRAFT)

> **DRAFT.** Cutting `v1.0.0` requires the **owner's explicit approval**
> (owner decision 2026-10-22, recorded in [`DECISIONS.md`](DECISIONS.md)). Every
> other release tag is automatic on green CI; this one is not. Do not proceed past
> the gate below without that approval.

The release notes to publish are in [`RELEASE_NOTES_v1.0.0.md`](RELEASE_NOTES_v1.0.0.md).

## 0. Gate — owner approval (blocking)

- [ ] **Owner has explicitly approved tagging `v1.0.0`.** Recorded where?
      (date + approver in `DECISIONS.md`).
- [ ] The owner has confirmed the **1.0 SemVer stability commitment** is intended
      (the REST API and public contract are now promised stable). If not, cut
      `v0.12.0` instead.

## 1. Preconditions

- [ ] Phase 11 PR is **merged** to `main`.
- [ ] **Every required CI job is green on the merge commit**: `lint`, `test`
      (unit + integration on PostgreSQL), `schema`, `docs`, `sdk`, `docker`
      (including the new backup/restore drill and `tests/e2e/`), `loadtest`,
      `secrets`, `supply-chain`, `k8s`.
- [ ] `main` is clean; no open Dependabot PR is required for the release.
- [ ] Local sanity: `uv run ruff check .`, `uv run ruff format --check .`,
      `uv run mypy flowforge core api --ignore-missing-imports`,
      `uv run mkdocs build --strict`.

## 2. Finalize the changelog

- [ ] Move the `## [Unreleased]` block in `CHANGELOG.md` to
      `## [1.0.0] - <YYYY-MM-DD>` (today's date), leaving a fresh empty
      `## [Unreleased]` above it.
- [ ] Commit: `docs(release): record v1.0.0 in the changelog`. Push to `main`.
- [ ] Wait for CI green on that commit.

## 3. Tag

- [ ] Annotated tag on the release commit:
      `git tag -a v1.0.0 -m "FlowForge v1.0.0 — first stable release"`.
- [ ] `git push origin v1.0.0`.

## 4. Publish the GitHub Release

- [ ] Create the release from the tag with the body from
      `RELEASE_NOTES_v1.0.0.md` (strip the DRAFT banner).
- [ ] **`release.yml` runs on publish** and attaches `flowforge-sbom.cdx.json`.
      Confirm the asset is present on the release; if the workflow did not run or
      the asset is missing, run it by hand (Actions → Release → Run workflow →
      tag `v1.0.0`).
- [ ] Confirm the image `ghcr.io/abolfazlrwm/flowforge:v1.0.0` exists and is
      signed: `cosign verify …` (command in the release notes).

## 5. Documentation

- [ ] Confirm `pages.yml` deployed the docs site from `main` and the new
      deployment pages render (Kubernetes, HA, backup/restore, migrations). The
      site is enabled and live at <https://abolfazlrwm.github.io/flowforge/>;
      this is a verification step, not a prerequisite — nothing to switch on.

## 6. Post-release

- [ ] Update `PROGRESS.md`: Phase 11 → **RELEASED**, add the `v1.0.0` row and the
      green CI run URL, move the Phase 11 "Completed" note to released status.
- [ ] Announce (the channels the owner chooses).
- [ ] Open a follow-up issue for anything deferred, e.g. PostgreSQL RLS
      (PROGRESS limitation 6) and a multi-node HA validation (limitation 32).

## Rollback

A published release is not retracted lightly. If a defect is found:

- Ship a patch `v1.0.1` (automatic on green CI — no special approval).
- Only yank the release/tag for a security or correctness emergency, and record
  the reason in `DECISIONS.md`.
