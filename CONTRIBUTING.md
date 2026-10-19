# Contributing to FlowForge

Thank you for your interest in contributing!

## Getting Started

1. Fork the repository.
2. Clone your fork.
3. Create a feature branch: `git checkout -b feat/my-feature`.
4. Install development dependencies: `pip install -e ".[dev]"`.
5. Set up pre-commit hooks: `pre-commit install`.

## Development Workflow

- Follow [Conventional Commits](https://www.conventionalcommits.org/).
- Keep changes small and focused.
- Add tests for new functionality.
- Update documentation as needed.
- Ensure CI passes before requesting review.

## Code Standards

- **Linting:** Ruff
- **Formatting:** Ruff (black-compatible)
- **Type checking:** mypy (strict mode)
- **Testing:** pytest with pytest-django

## Reporting Issues

Please use GitHub Issues and include:
- A clear description.
- Steps to reproduce.
- Expected vs. actual behavior.
- Environment details.

## Security Issues

Do not open public issues for security vulnerabilities. See [SECURITY.md](SECURITY.md).
