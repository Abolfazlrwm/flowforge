"""Settings package for FlowForge.

Select an environment explicitly via ``DJANGO_SETTINGS_MODULE``:

- ``flowforge.settings.dev``  — local development
- ``flowforge.settings.test`` — automated tests
- ``flowforge.settings.prod`` — production

This package intentionally defines no settings itself so that a missing
``DJANGO_SETTINGS_MODULE`` submodule fails loudly instead of silently
falling back to development defaults.
"""
