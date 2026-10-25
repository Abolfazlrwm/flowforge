# Naming Analysis: "FlowForge"

!!! warning "Historical snapshot as of 2026-10-19"

    Registry availability and trademark exposure below were measured on
    2026-10-19 and are a point-in-time record, not a live status. The conclusion
    has not changed: the owner kept the name, and nothing is published under it
    on PyPI or npm. See [PROGRESS.md](PROGRESS.md) for the current state.

**Date:** 2026-10-19
**Status:** Chosen name retained by owner decision. Conflicts documented below.

> This document records measured evidence, not an opinion. The name is **not
> clear** on any registry except PyPI's `flowforge` slot. If this project is
> ever published to npm, or if trademark exposure matters, the name must be
> revisited.

## Summary table

| Registry / channel | Result | Evidence |
|--------------------|--------|----------|
| PyPI `flowforge` | **Available** | `GET https://pypi.org/pypi/flowforge/json` → `{"message": "Not Found"}` |
| PyPI `flow-forge` | **Taken** | `GET https://pypi.org/pypi/flow-forge/json` → returns package metadata (author: Kyle Oliver, MIT, `Development Status :: 1 - Planning`) |
| npm `flowforge` | **Reserved / blocked** | `GET https://registry.npmjs.org/flowforge` → package exists, `versions: []`, `dist-tags: None`; version `1.0.0` published 2021-12-07 and unpublished 57 seconds later. Name is held by another account. |
| npm `flow-forge` | Available | `GET https://registry.npmjs.org/flow-forge` → HTTP 404 |
| GitHub repositories | **Many collisions** | `gh search repos FlowForge` → 12+ results, see below |
| Commercial product | **Active** | `flowforgesoft.com` — "mission control for shipping software", single Go binary |
| Prior funded company | **Yes** | FlowForge (founded 2021-04-02, US) raised a $2.7M seed round to build a Node-RED low-code platform; the product was later rebranded **FlowFuse** (`github.com/FlowFuse/flowfuse`) |

## GitHub repository collisions

Measured with `gh search repos FlowForge --limit 12` on 2026-10-19:

| Repository | Language | Stars | Description |
|-----------|----------|-------|-------------|
| `relaticle/flowforge` | PHP | 422 | Laravel Kanban boards for Filament/Livewire |
| `jialuechen/flowforge` | Python | 140 | Algo library for order flow inference and TCA |
| `gufranthakur/FlowForge` | Java | 137 | Visual programming tool inspired by UE5 blueprints |
| `wentong2022-arch/flowforge-skill` | Python | 139 | draw.io diagram generation skill |
| `FlowFuse/docker-compose` | Dockerfile | 23 | Docker Compose file to run FlowForge |
| `0xDaniiel/flowforge-ai` | TypeScript | 33 | Visual AI workflow builder |
| `genkinsforge/FlowForge` | Python | 21 | Draw.io ↔ Mermaid conversion |
| `kagura-chen/flowforge` | TypeScript | 8 | Workflow engine with gates |
| `Shriii19/FlowForge` | TypeScript | 7 | Developer collaboration platform |
| `gggamil/mazebreak-flowforge` | HTML | 68 | Trello automation Power-Up |
| `ozynautetu-create/FlowForge-Optima` | HTML | 56 | LLM app optimizer |
| `devadigapratham/flowforge` | TypeScript | 0 | (no description) |

At least three unrelated projects carry the name with >100 stars, and one of
them (`relaticle/flowforge`) is a PHP package published to Packagist under the
same name.

## Prior company (highest-risk finding)

FlowForge was an incorporated company founded 2021-04-02 in the United States
that raised a $2.7M seed round for an open-source Node-RED DevOps platform.
That product is now **FlowFuse**. Sources:

- Qichacha company profile: "FlowForge … 开源低代码开发平台 … 成立日期: 2021-04-02 … 融资总额: 270万美元"
- `github.com/FlowFuse/flowfuse` — "Connect, Collect, Build, and Scale with FlowFuse"
- Community posts still refer to the installer as `flowforge.bat`

This means the name has been used commercially by a venture-funded entity.
Trademark risk is non-trivial and is not something a code audit can clear.

## What this means for the project

| Goal | Impact |
|------|--------|
| Local development / internal use | No practical problem |
| Public GitHub repo named `flowforge` | Confusing but legal; already done |
| Publishing to PyPI as `flowforge` | Possible — slot is free |
| Publishing to npm as `flowforge` | **Blocked** — name reserved by another account |
| Trademark / brand distinctiveness | **At risk** — prior funded company + active commercial product |

## Recommendation (requires owner approval — not acted on)

If any of the following become true, rename before they do:

1. Publishing a JavaScript/TypeScript client to npm.
2. Applying for a trademark or forming a company around the project.
3. Receiving a takedown or cease-and-desist.

Candidate names and their measured status at the time of writing:

| Candidate | PyPI | npm | Notes |
|-----------|------|-----|-------|
| TaskForge | free | free | No GitHub exact match found in earlier check |
| ForgeFlow | free | free | No exact GitHub match found |
| DagForge | free | free | Emphasises DAG execution |

No rename has been performed. The project remains **FlowForge** per owner
decision.
