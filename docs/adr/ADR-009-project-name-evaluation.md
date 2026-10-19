# ADR-009: Project Name — Evaluation of Alternatives (owner-delegated)

**Date:** 2026-10-19
**Status:** Accepted — owner-delegated decision
**Decision owner:** repository owner, delegated via the autonomous-mode brief
**Supersedes:** none

## Context

The project is currently named **FlowForge**. A previous audit established that
the name is heavily conflicted:

- npm `flowforge` is reserved by another account (published then unpublished in
  2021), so publishing is blocked.
- PyPI `flow-forge` is taken (MIT, "Development Status :: 1 - Planning").
- 12+ GitHub repositories use the name, three of them above 100 stars.
- A venture-funded company named FlowForge (founded 2021-04-02, US, $2.7M seed)
  built a Node-RED DevOps platform and later rebranded to **FlowFuse**.
- `flowforgesoft.com` is an active commercial product.

The owner delegated the naming decision with an explicit rule: evaluate up to
five *distinctive* candidates (explicitly excluding generic "X-Forge" and
"Flow-X" patterns); adopt one only if it is **clearly clean on PyPI + GitHub +
web**; otherwise keep FlowForge.

## Options evaluated

Five distinctive, non-generic candidates were chosen for their thematic fit
(controlled flow, fleets of workers, load-bearing connections):

| Candidate | Rationale for shortlisting |
|-----------|----------------------------|
| Penstock | A penstock is the conduit that regulates water flow into a turbine — precise metaphor for controlled task dispatch |
| Bowline | A knot that holds under load yet unties cleanly — resilience and graceful shutdown |
| Flotilla | A group of small vessels operating together — a fleet of workers |
| Weir | A low dam that regulates flow over a channel — scheduling and back-pressure |
| Halyard | The rope used to raise and lower a sail — coordination of distributed parts |

## Measurements (2026-10-19)

Registry results, queried directly from the PyPI JSON API, the npm registry and
the crates.io API:

| Candidate | PyPI | npm | crates.io |
|-----------|------|-----|-----------|
| penstock | **TAKEN** 0.1.0 (Josh Puglielli) | **TAKEN** (9 versions) | **TAKEN** 0.1.1, 41 downloads |
| bowline | **TAKEN** 0.2.2 (Marco Gancitano) | **TAKEN** (4 versions) | free |
| flotilla | **TAKEN** 0.3.2 (olgabot) | **TAKEN** (13 versions) | **TAKEN** 0.1.0, 729 downloads |
| weir | **TAKEN** 0.4.0 (Stephen Drake) | **TAKEN** (11 versions) | **TAKEN** 0.0.0, 25 downloads |
| halyard | **TAKEN** 0.2.12 | **TAKEN** (0 versions) | **TAKEN** 0.1.0, 41 downloads |

GitHub repository-name matches (`gh api search/repositories?q=<name>+in:name`,
sorted by stars):

| Candidate | Repos matching name | Top result |
|-----------|--------------------:|------------|
| penstock | 33 | `athoune/go-penstock` (3★) |
| bowline | 55 | `maccman/bowline` (632★) |
| flotilla | 404 | `tylertread/Flotilla` (238★), `stitchfix/flotilla-os` (195★) |
| weir | 7 978 | `GrrrDog/weird_proxies` (1 891★) — substring matches dominate |
| halyard | 220 | `spinnaker/halyard` (316★), a real Spinnaker configuration tool |

## Decision

**Keep the name FlowForge.**

Every candidate is already registered on PyPI. The owner's rule requires a
candidate to be clearly clean on PyPI **and** GitHub **and** the web; none
qualifies, so the fallback branch of the rule applies.

## Consequences

- The Python distribution name remains `flowforge`; the import package remains
  `flowforge`.
- Publishing to npm under the name `flowforge` remains **impossible** without
  acquiring the name from its current holder.
- Publishing to PyPI as `flowforge` remains possible (the slot is free), but
  `flow-forge` is already occupied by an unrelated project.
- The GitHub repository stays `Abolfazlrwm/flowforge`.
- The naming risk is documented in `docs/development/NAMING_ANALYSIS.md` and is
  listed as a known limitation in `docs/development/PROGRESS.md`.

## Alternatives considered

- **Adopt a candidate anyway.** Rejected: each is taken on PyPI, which is the
  distribution channel that matters most for a Python library, and several also
  collide with well-known GitHub projects (Spinnaker Halyard, Flotilla OS).
- **Coined name (e.g. a nonsense word).** Not evaluated: the owner restricted
  the search to five candidates and required a distinctive-but-meaningful name.
  A coined name remains the strongest option if the project ever needs a
  trademark.
- **Rename to TaskForge.** Rejected earlier: the previous audit renamed without
  owner approval, and that rename was reverted in `1352e22`.
