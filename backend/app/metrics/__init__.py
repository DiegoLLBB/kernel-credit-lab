"""Credit metrics computed in code from SEC XBRL companyfacts.

Pipeline: :mod:`app.metrics.config` (per-company tag choices) →
:mod:`app.metrics.select` (point-in-time datapoint selection, TTM) →
:mod:`app.metrics.compute` (metric maths, missing-with-reason) →
:mod:`app.metrics.loader` (idempotent DB upsert) →
:mod:`app.metrics.refresh` (replay/live hook for new 10-Q/10-K filings).

Kernel principle: numbers come from filings and are computed here. The insight
layer (:mod:`app.insight`) may only quote numbers produced by this package.
"""

from app.metrics.compute import METRIC_IDS, MetricRow, compute_issuer
from app.metrics.config import IssuerMetricConfig, load_metric_config
from app.metrics.loader import load_metrics
from app.metrics.refresh import refresh_issuer_metrics

__all__ = [
    "METRIC_IDS",
    "IssuerMetricConfig",
    "MetricRow",
    "compute_issuer",
    "load_metric_config",
    "load_metrics",
    "refresh_issuer_metrics",
]
