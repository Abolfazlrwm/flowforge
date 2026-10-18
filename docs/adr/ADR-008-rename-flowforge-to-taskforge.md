# ADR-008: Rename FlowForge to TaskForge

**Date:** 2026-10-18
**Status:** Accepted
**Supersedes:** None

## Context

The project was originally named "FlowForge". A naming conflict audit
identified the following conflicts:

1. **github.com/gufranthakur/FlowForge** — a Java desktop application.
2. **flowforgesoft.com** — a commercial Go-based product ("mission control for
   shipping software") with a registered domain.
3. **github.com/Ganesh-parella/Flow-Forge** — a Google Cloud / ASP.NET project.

No "flowforge" package exists on PyPI or npm at the time of this audit.
However, the existence of a commercial product with a domain name creates
potential trademark confusion and makes future PyPI/npm publication
problematic.

## Decision

Rename the project from "FlowForge" to "TaskForge".

## Conflict Check for "TaskForge"

| Platform | Conflict? |
|----------|-----------|
| PyPI     | No package named "taskforge" exists |
| npm      | No package named "taskforge" exists |
| GitHub   | No exact-match "TaskForge" repository found |
| Google   | No commercial product or website found |

The name "TaskForge" is clear on all major registries.

## Rationale

- Avoids potential trademark/brand confusion with the commercial
  flowforgesoft.com product.
- Enables clean PyPI and npm publication in future phases.
- "TaskForge" communicates the project's domain (task/workflow orchestration)
  without clashing with existing products.

## Consequences

- Django project package: `flowforge/` → `taskforge/`
- PyPI package name: `flowforge` → `taskforge`
- All settings, URLs, imports, Dockerfile, compose, CI, docs updated.
- GitHub repository will need to be renamed (requires user action).
- The remote URL in `.git/config` will need updating after repo rename.
