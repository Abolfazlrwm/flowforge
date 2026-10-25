# Security Policy

## Supported Versions

FlowForge is pre-1.0 and moves quickly. Security fixes land on the **latest
release line**; there is no long-term-support branch yet. Once `1.0.0` ships,
this table will state exactly which minor lines receive backported fixes.

| Version | Supported |
|---------|-----------|
| 0.11.x (latest) | :white_check_mark: |
| 1.0.x (once released) | :white_check_mark: |
| older lines | :x: |

## Reporting a Vulnerability

Please **do not open a public issue** for a security problem.

Report it privately through
[GitHub Private Vulnerability Reporting](https://github.com/Abolfazlrwm/flowforge/security/advisories/new)
on this repository. That channel keeps the report visible only to the
maintainers until it is ready to disclose.

**What to expect:**

- **Acknowledgement within 48 hours** of the report.
- An initial assessment and a remediation timeline within 7 days.
- Credit in the advisory and the release notes, unless you prefer to stay
  anonymous.
- Coordinated disclosure: we will agree a public disclosure date with you and
  publish a GitHub Security Advisory with the fix.

If you cannot use GitHub's private reporting, open a discussion and ask a
maintainer for a private channel; do not paste exploit details into a public
thread.

## What FlowForge does and does not protect against

An honest scope matters more than a reassuring list.

**In scope.** The multi-tenant API: cross-tenant data access, authentication and
API-key handling, injection through the definition/expression surfaces, and the
supply chain of the shipped container image.

**Explicitly out of scope.** FlowForge does **not** execute arbitrary
user-supplied code. Task handlers are a fixed, trusted set (`echo`, `add`,
`noop`, …) resolved server-side; a task definition is data, not code. This is a
deliberate non-goal — see the design guarantees in the README — and it removes
the largest class of workflow-engine vulnerabilities (remote code execution via
a task body). If that changes, this policy changes with it.

## Security practices in place

These are implemented and, where automatable, enforced in CI — not aspirations.

- **Multi-tenancy by construction.** Every model is scoped to an organization and
  every queryset is filtered at the manager level (ADR-014), so a caller cannot
  reach another tenant's rows by construction. A dedicated tenant-isolation
  suite proves it, and a cross-tenant read is a `404`, not a `403`, so a
  malicious caller cannot even learn that a row exists.
- **Authentication and API keys.** Token and JWT authentication; API keys are
  stored hashed, are scope-limited, expire, and are rotatable, and are shown
  once at creation.
- **Append-only audit log.** `core_auditlog` is protected by a database trigger
  that rejects `UPDATE` and `DELETE`, so the audit trail cannot be tampered with
  from the application.
- **Secret redaction.** Secrets are redacted from logs and error payloads.
- **Threat model.** A STRIDE analysis lives at
  [`docs/security/threat-model.md`](docs/security/threat-model.md).
- **Container hardening.** The production image runs as a non-root user with a
  read-only root filesystem, dropped capabilities and a `RuntimeDefault`
  seccomp profile; the base image is pinned by digest.
- **Supply chain.** Every image is scanned with Trivy (a `CRITICAL` finding
  fails the build), a CycloneDX SBOM is generated and attached to each release,
  and the image is signed keylessly with cosign (recorded in the Rekor
  transparency log) with a build-provenance attestation.
- **Secrets and dependencies.** gitleaks scans the full git history with push
  protection enabled; `bandit` scans first-party code and `pip-audit` gates the
  locked dependency set on every run.

## Verifying a release image

A released image is signed; you can verify it without being given a key, because
the signature's identity is this repository's GitHub Actions workflow, recorded
in Rekor:

```bash
cosign verify \
  --certificate-identity-regexp 'https://github.com/Abolfazlrwm/flowforge/.github/workflows/ci.yml' \
  --certificate-oidc-issuer-regexp 'https://token.actions.githubusercontent.com' \
  ghcr.io/abolfazlrwm/flowforge:<tag>
```

The release also carries a CycloneDX SBOM (`flowforge-sbom.cdx.json`) listing
every component in the image.
