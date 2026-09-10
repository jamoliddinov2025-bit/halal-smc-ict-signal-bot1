"""Typed failures for the read-only monitoring layer.

Monitoring is an observer: it consumes records that other layers have already
published and returns monitoring values of its own. It never generates, gates,
vetoes, reinterprets, or modifies a signal, a halal classification, an
eligibility decision, a governance decision, or a delivery.

These errors exist so that a misconfigured or misused monitor fails loudly at its
own boundary instead of being silently tolerated. They are deliberately
independent of the analysis and delivery error hierarchies: a monitoring failure
is not an analysis failure, and no upstream layer is expected to catch it.
"""


class MonitoringError(Exception):
    """Base exception for the monitoring layer."""


class MonitoringConfigurationError(MonitoringError, ValueError):
    """Monitoring configuration is invalid or unsupported."""


class MonitoringInputError(MonitoringError, ValueError):
    """A value handed to the monitoring layer violates its contract."""
