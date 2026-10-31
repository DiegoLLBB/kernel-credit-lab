"""Per-company XBRL tag configuration (seed/xbrl_metrics.yaml).

Each issuer maps metric inputs to verified us-gaap tags. A ``null`` tag means the
metric is intentionally missing; the adjacent ``<field>_reason`` documents why
for the UI ("missing with the reason, never estimated"). An issuer with
``xbrl: null`` has no usable companyfacts at all (see duke_progress: the
subsidiary's document restates parent comparatives, so every metric is missing).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

CONFIG_RELATIVE = Path("seed") / "xbrl_metrics.yaml"

# Config keys that name a tag (or list of tags) per metric input.
TAG_FIELDS = (
    "debt_current",
    "debt_noncurrent",
    "debt_total",
    "cash",
    "st_investments",
    "opinc",
    "depreciation",
    "interest",
    "ocf",
    "capex",
    "mat_next12",
    "mat_year2",
)


@dataclass(frozen=True)
class IssuerMetricConfig:
    """Tag choices for one issuer; ``usable`` is False when xbrl is null."""

    issuer_key: str
    cik: str
    usable: bool
    unusable_reason: str | None = None
    tags: dict[str, str | None] = field(default_factory=dict)
    debt_extra: tuple[str, ...] = ()
    reasons: dict[str, str] = field(default_factory=dict)
    history_years: int = 3
    wall_tolerance_pct: float = 2.0

    def tag(self, field_name: str) -> str | None:
        return self.tags.get(field_name)

    def reason(self, field_name: str) -> str | None:
        return self.reasons.get(field_name)


@dataclass(frozen=True)
class MetricsConfig:
    issuers: dict[str, IssuerMetricConfig]
    history_years: int = 3
    wall_tolerance_pct: float = 2.0

    def for_issuer(self, issuer_key: str) -> IssuerMetricConfig | None:
        return self.issuers.get(issuer_key)


def _config_path(explicit: Path | str | None) -> Path:
    if explicit is not None:
        return Path(explicit)
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / CONFIG_RELATIVE
        if candidate.exists():
            return candidate
    return here.parents[2] / CONFIG_RELATIVE


def load_metric_config(path: Path | str | None = None) -> MetricsConfig:
    """Load and validate the XBRL metrics config (raises on unknown keys)."""
    resolved = _config_path(path)
    raw: dict[str, Any] = yaml.safe_load(resolved.read_text())
    defaults = raw.get("defaults", {})
    history_years = int(defaults.get("history_years", 3))
    wall_tolerance_pct = float(defaults.get("wall_tolerance_pct", 2.0))
    issuers: dict[str, IssuerMetricConfig] = {}
    known = set(TAG_FIELDS) | {
        "cik",
        "xbrl",
        "xbrl_reason",
        "debt_extra",
        "revolver",
        "revolver_reason",
    }
    for key, entry in (raw.get("issuers") or {}).items():
        entry = entry or {}
        unknown = set(entry) - known - {f"{f}_reason" for f in TAG_FIELDS}
        if unknown:
            raise ValueError(f"xbrl_metrics.yaml {key}: unknown keys {sorted(unknown)}")
        usable = bool(entry.get("xbrl", True))
        tags = {f: entry.get(f) for f in TAG_FIELDS}
        reasons = {f: entry[f"{f}_reason"] for f in TAG_FIELDS if entry.get(f"{f}_reason")}
        if entry.get("revolver_reason"):
            reasons["revolver"] = entry["revolver_reason"]
        debt_extra = tuple(entry.get("debt_extra") or ())
        issuers[key] = IssuerMetricConfig(
            issuer_key=key,
            cik=str(entry.get("cik") or ""),
            usable=usable,
            unusable_reason=entry.get("xbrl_reason"),
            tags=tags,
            debt_extra=debt_extra,
            reasons=reasons,
            history_years=history_years,
            wall_tolerance_pct=wall_tolerance_pct,
        )
    return MetricsConfig(
        issuers=issuers,
        history_years=history_years,
        wall_tolerance_pct=wall_tolerance_pct,
    )
