# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 0.1.x   | :white_check_mark: |

## Reporting a Vulnerability

If you discover a security vulnerability in FlowForge, please do not open a public issue.

Instead, report it privately via GitHub Security Advisories or email the maintainers directly.

We will acknowledge receipt within 48 hours and provide a timeline for remediation.

## Security Practices

- Secrets are never hardcoded; use environment variables.
- Production settings enforce HTTPS, HSTS, and secure cookies.
- Cross-tenant access is explicitly prevented.
- Arbitrary code execution is not supported in initial releases.
