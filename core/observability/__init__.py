"""Observability: log correlation, Prometheus metrics and OpenTelemetry tracing.

The three concerns are separate modules because they fail independently: a
missing metric must not break a request, and an unreachable collector must not
break a worker. Callers import the specific module they need.
"""
