"""Repository and data path helpers."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
RAW_TRAINING_DIR = DATA_DIR / "raw" / "training"
PLANS_DIR = DATA_DIR / "plans"
NORMALIZED_DIR = DATA_DIR / "normalized"
DB_PATH = DATA_DIR / "training.db"
SYNC_STATE_PATH = DATA_DIR / "sync_state.json"
ENV_PATH = ROOT / ".env"


def raw_training_path(datestr: str) -> Path:
    """Return path for a day's raw training JSON: data/raw/training/YYYY/YYYY-MM-DD.json."""
    year = datestr[:4]
    return RAW_TRAINING_DIR / year / f"{datestr}.json"


def plan_path(plan_ref: str) -> Path:
    """Safe filename for a plan_ref like platform:155."""
    safe = plan_ref.replace(":", "_").replace("/", "_")
    return PLANS_DIR / f"{safe}.json"


def ensure_data_dirs() -> None:
    RAW_TRAINING_DIR.mkdir(parents=True, exist_ok=True)
    PLANS_DIR.mkdir(parents=True, exist_ok=True)
    NORMALIZED_DIR.mkdir(parents=True, exist_ok=True)
