"""Load global app-config, per-family config, and the .env from the data root.

Plain files, no database. Defaults are baked in so the system runs even before
the user has written any config.

Two scopes:
  - APP config (app-config.yaml): settings that span all families — the model,
    retention, default currency, which family is active.
  - FAMILY config (families/<id>/family.yaml): household facts, thresholds, and
    stress-test assumptions specific to one family.
"""
from __future__ import annotations

import os
from typing import Any

import yaml
from dotenv import load_dotenv

import paths

APP_DEFAULTS: dict[str, Any] = {
    "retention_days": 15,
    "default_currency": "CAD",
    # Local units per 1 USD, for USD-equivalent displays in non-CAD households.
    # CAD tracks fx_usd_cad; add other currencies here (HKD is USD-pegged ~7.8).
    "fx_local_per_usd": {"HKD": 7.8},
    "active_family": None,        # which family the CLI/app uses by default
    "extraction": {
        # Strong model by default — extraction accuracy is the whole product.
        # Downgrade to a cheaper model (e.g. claude-sonnet-4-6) once the prompt
        # is validated and you want to save cost on routine statements.
        "model": "claude-opus-4-8",
        "max_tokens": 16000,
    },
}

FAMILY_DEFAULTS: dict[str, Any] = {
    "name": None,
    "currency": "CAD",
    "household": {},
    "review_thresholds": {
        "high_amount": 1000,        # CAD
        "anomaly_multiplier": 2.0,  # flag if amount > Nx category median
    },
    "stress_test": {},
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_app_config() -> dict[str, Any]:
    cfg = APP_DEFAULTS
    if paths.APP_CONFIG_FILE.exists():
        with open(paths.APP_CONFIG_FILE) as f:
            user_cfg = yaml.safe_load(f) or {}
        cfg = _deep_merge(cfg, user_cfg)
    return cfg


def local_per_usd(currency: str) -> float | None:
    """Units of `currency` per 1 USD, for USD-equivalent displays. CAD tracks fx_usd_cad;
    other currencies come from fx_local_per_usd. None if no rate is configured."""
    cfg = load_app_config()
    if currency == "CAD":
        return cfg.get("fx_usd_cad", 1.37)
    return (cfg.get("fx_local_per_usd") or {}).get(currency)


def load_family_config(family_id: str) -> dict[str, Any]:
    cfg = FAMILY_DEFAULTS
    cfile = paths.family_config_file(family_id)
    if cfile.exists():
        with open(cfile) as f:
            user_cfg = yaml.safe_load(f) or {}
        cfg = _deep_merge(cfg, user_cfg)
    return cfg


def save_family_config(family_id: str, updates: dict[str, Any]) -> dict[str, Any]:
    """Merge `updates` into the family's on-disk config (family.yaml) and return the merged
    raw config. Only the existing file is merged (not FAMILY_DEFAULTS), so we never persist
    the full default tree into the file."""
    cfile = paths.family_config_file(family_id)
    existing: dict[str, Any] = {}
    if cfile.exists():
        with open(cfile) as f:
            existing = yaml.safe_load(f) or {}
    merged = _deep_merge(existing, updates)
    cfile.parent.mkdir(parents=True, exist_ok=True)
    with open(cfile, "w") as f:
        yaml.safe_dump(merged, f, sort_keys=False)
    return merged


def load_members(family_id: str) -> list[dict[str, Any]]:
    mfile = paths.members_file(family_id)
    if not mfile.exists():
        return []
    with open(mfile) as f:
        data = yaml.safe_load(f) or {}
    return data.get("members", [])


def save_members(family_id: str, members: list[dict[str, Any]]) -> None:
    """Overwrite this family's members.yaml with the given list."""
    mfile = paths.members_file(family_id)
    mfile.parent.mkdir(parents=True, exist_ok=True)
    with open(mfile, "w") as f:
        yaml.safe_dump({"members": members}, f, sort_keys=False, allow_unicode=True)


def resolve_family(family_arg: str | None) -> str:
    """Pick the family to operate on: explicit arg > active_family > the only one."""
    families = paths.list_families()
    if family_arg:
        return family_arg
    active = load_app_config().get("active_family")
    if active:
        return active
    if len(families) == 1:
        return families[0]
    return ""  # caller decides how to error


def load_api_key() -> str | None:
    """Read ANTHROPIC_API_KEY from the data-root .env, then the process env."""
    if paths.ENV_FILE.exists():
        load_dotenv(paths.ENV_FILE)
    return os.environ.get("ANTHROPIC_API_KEY") or None
