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
    "pretax",
    "depreciation",
    "interest",
    "ocf",
    "capex",
    "mat_next12",
    "mat_year2",
)

TagValue = str | tuple[str, ...] | None


@dataclass(frozen=True)
class IssuerMetricConfig:
    """Tag choices for one issuer; ``usable`` is False when xbrl is null."""

    issuer_key: str
    cik: str
    usable: bool
    unusable_reason: str | None = None
    tags: dict[str, TagValue] = field(default_factory=dict)
    debt_extra: tuple[str, ...] = ()
    reasons: dict[str, str] = field(default_factory=dict)
    # opinc_plus_da | pretax_plus_interest_plus_da | None
    ebitda_compose: str | None = None
    history_years: int = 3
    wall_tolerance_pct: float = 2.0

    def tag(self, field_name: str) -> TagValue:
        return self.tags.get(field_name)

    def tag_list(self, field_name: str) -> list[str]:
        raw = self.tag(field_name)
        if raw is None:
            return []
        if isinstance(raw, str):
            return [raw] if raw else []
        return [t for t in raw if t]

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


_KNOWN_ENTRY_KEYS = set(TAG_FIELDS) | {
    "cik",
    "xbrl",
    "xbrl_reason",
    "debt_extra",
    "revolver",
    "revolver_reason",
    "ebitda_compose",
}


def _normalize_tag(value: Any) -> TagValue:
    if value is None:
        return None
    if isinstance(value, str):
        return value or None
    if isinstance(value, (list, tuple)):
        parts = tuple(str(v) for v in value if v)
        return parts or None
    return str(value)


def entry_to_issuer_config(
    key: str,
    entry: dict[str, Any],
    *,
    history_years: int = 3,
    wall_tolerance_pct: float = 2.0,
) -> IssuerMetricConfig:
    """Build an IssuerMetricConfig from a yaml-shaped dict (seed or onboard)."""
    entry = dict(entry or {})
    # Onboard stores diagnostic keys that are not tag fields.
    entry.pop("_missing", None)
    unknown = set(entry) - _KNOWN_ENTRY_KEYS - {f"{f}_reason" for f in TAG_FIELDS}
    if unknown:
        raise ValueError(f"xbrl config {key}: unknown keys {sorted(unknown)}")
    usable = bool(entry.get("xbrl", True))
    tags = {f: _normalize_tag(entry.get(f)) for f in TAG_FIELDS}
    reasons = {f: entry[f"{f}_reason"] for f in TAG_FIELDS if entry.get(f"{f}_reason")}
    # pretax is an onboard fallback field; seed issuers omit it — record why.
    if tags.get("pretax") is None and "pretax" not in reasons:
        if tags.get("opinc"):
            reasons["pretax"] = "Not used; operating income tag is configured."
        else:
            reasons["pretax"] = "No pretax tag configured."
    if entry.get("revolver_reason"):
        reasons["revolver"] = entry["revolver_reason"]
    debt_extra = tuple(entry.get("debt_extra") or ())
    compose = entry.get("ebitda_compose")
    return IssuerMetricConfig(
        issuer_key=key,
        cik=str(entry.get("cik") or ""),
        usable=usable,
        unusable_reason=entry.get("xbrl_reason"),
        tags=tags,
        debt_extra=debt_extra,
        reasons=reasons,
        ebitda_compose=str(compose) if compose else None,
        history_years=history_years,
        wall_tolerance_pct=wall_tolerance_pct,
    )


def load_metric_config(path: Path | str | None = None) -> MetricsConfig:
    """Load and validate the XBRL metrics config (raises on unknown keys)."""
    resolved = _config_path(path)
    raw: dict[str, Any] = yaml.safe_load(resolved.read_text())
    defaults = raw.get("defaults", {})
    history_years = int(defaults.get("history_years", 3))
    wall_tolerance_pct = float(defaults.get("wall_tolerance_pct", 2.0))
    issuers: dict[str, IssuerMetricConfig] = {}
    for key, entry in (raw.get("issuers") or {}).items():
        issuers[key] = entry_to_issuer_config(
            key,
            entry or {},
            history_years=history_years,
            wall_tolerance_pct=wall_tolerance_pct,
        )
    return MetricsConfig(
        issuers=issuers,
        history_years=history_years,
        wall_tolerance_pct=wall_tolerance_pct,
    )


def merge_db_configs(session: Any, base: MetricsConfig | None = None) -> MetricsConfig:
    """Overlay Issuer.xbrl_config rows (ticker-onboarded) onto the YAML config."""
    from sqlalchemy import select

    from app.models import Issuer

    cfg = base or load_metric_config()
    issuers = dict(cfg.issuers)
    rows = session.scalars(
        select(Issuer).where(Issuer.xbrl_config.is_not(None), Issuer.key.is_not(None))
    ).all()
    for iss in rows:
        if not iss.key or not isinstance(iss.xbrl_config, dict):
            continue
        # YAML wins for hand-verified seed issuers; DB only fills gaps.
        if iss.key in issuers and iss.onboard_source != "ticker":
            continue
        if iss.key in issuers and iss.onboard_source == "ticker":
            # Prefer DB for ticker-onboarded (may refresh tags on re-onboard).
            pass
        elif iss.key in issuers:
            continue
        try:
            issuers[iss.key] = entry_to_issuer_config(
                iss.key,
                iss.xbrl_config,
                history_years=cfg.history_years,
                wall_tolerance_pct=cfg.wall_tolerance_pct,
            )
        except ValueError:
            continue
    return MetricsConfig(
        issuers=issuers,
        history_years=cfg.history_years,
        wall_tolerance_pct=cfg.wall_tolerance_pct,
    )
