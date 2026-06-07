"""Filesystem layout for Kiviat Lab.

Code and data live together in one project folder. The DATA ROOT (families,
statements, events, config) is the `data/` subfolder, which is gitignored so it
never reaches the repo. Override the location with the KIVIAT_DATA_ROOT env var.

    kiviat-lab/                  # this project (the CODE)
      extract.py, families.py, ...
      data/                      # the DATA ROOT (gitignored)
        app-config.yaml          # global settings (retention, model, active family)
        .env                     # ANTHROPIC_API_KEY (gitignored)
        families/
          <family-id>/
            family.yaml          # this family's name, household facts, thresholds
            members.yaml         # the people in this family
            inbox/               # raw uploads, auto-deleted later
            staging/             # extracted events awaiting review
            events/              # committed append-only log
            notes/               # freeform markdown
"""
import os
from pathlib import Path

# Where this code lives (for loading the prompt, etc.)
CODE_ROOT = Path(__file__).resolve().parent
PROMPT_FILE = CODE_ROOT / "prompts" / "extraction_prompt.md"

# Where the data lives: the project's data/ subfolder. Override with KIVIAT_DATA_ROOT.
DATA_ROOT = Path(os.environ.get("KIVIAT_DATA_ROOT", CODE_ROOT / "data"))

FAMILIES_DIR = DATA_ROOT / "families"
APP_CONFIG_FILE = DATA_ROOT / "app-config.yaml"
ENV_FILE = DATA_ROOT / ".env"

# Per-family subfolders.
_FAMILY_SUBDIRS = ("inbox", "staging", "events", "notes")


def family_dir(family_id: str) -> Path:
    return FAMILIES_DIR / family_id


def family_config_file(family_id: str) -> Path:
    return family_dir(family_id) / "family.yaml"


def members_file(family_id: str) -> Path:
    return family_dir(family_id) / "members.yaml"


def inbox(family_id: str) -> Path:
    return family_dir(family_id) / "inbox"


def staging(family_id: str) -> Path:
    return family_dir(family_id) / "staging"


def events(family_id: str) -> Path:
    return family_dir(family_id) / "events"


def notes(family_id: str) -> Path:
    return family_dir(family_id) / "notes"


def family_exists(family_id: str) -> bool:
    return family_config_file(family_id).exists()


def list_families() -> list[str]:
    """Family ids that have a family.yaml, sorted."""
    if not FAMILIES_DIR.exists():
        return []
    return sorted(
        p.name for p in FAMILIES_DIR.iterdir()
        if p.is_dir() and (p / "family.yaml").exists()
    )


def ensure_family_dirs(family_id: str) -> None:
    """Create a family's subfolders if missing (idempotent)."""
    for sub in _FAMILY_SUBDIRS:
        (family_dir(family_id) / sub).mkdir(parents=True, exist_ok=True)
