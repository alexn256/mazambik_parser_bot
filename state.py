import json
import os
import tempfile
from datetime import datetime


def _date_sort_key(date_str: str) -> datetime:
    """Parse DD.MM.YYYY to datetime for sorting."""
    try:
        return datetime.strptime(date_str, "%d.%m.%Y")
    except ValueError:
        return datetime.min


def load_state(path: str) -> dict:
    """Load multi-date state from JSON file. Returns empty dict if missing or corrupt.

    Automatically migrates old single-date format:
      {"date": "...", "schedule": {...}, ...}
    to new format:
      {"DD.MM.YYYY": {"last_timestamp": ..., "schedule": {...}, "update_count": N}}
    """
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        # Migrate old format
        if isinstance(data, dict) and "schedule" in data and "date" in data:
            date = data["date"]
            return {date: {
                "last_timestamp": data.get("last_timestamp"),
                "schedule": data["schedule"],
                "update_count": data.get("update_count", 1),
            }}
        return data
    except (json.JSONDecodeError, IOError):
        return {}


def save_state(state: dict, path: str) -> None:
    """Atomically save state to JSON file (write to temp, then rename)."""
    dir_name = os.path.dirname(os.path.abspath(path))
    fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".json.tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, path)
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


def is_new_day(state: dict, date: str | None) -> bool:
    """Check if this is the first update for the given date."""
    if not date:
        return True
    return date not in state


def build_state(state: dict, parsed: dict) -> dict:
    """Update state dict with new parsed schedule. Keeps at most 2 most recent dates."""
    date = parsed.get("date")
    if not date:
        return state

    existing = state.get(date, {})
    is_first = date not in state
    update_count = 1 if is_first else existing.get("update_count", 0) + 1

    state[date] = {
        "last_timestamp": parsed.get("timestamp"),
        "schedule": parsed["schedule"],
        "update_count": update_count,
    }

    # Prune: keep only 2 most recent dates
    if len(state) > 2:
        dates = sorted(state.keys(), key=_date_sort_key)
        for old_date in dates[:-2]:
            del state[old_date]

    return state


def get_latest_state(state: dict) -> tuple[str, dict] | None:
    """Get the most recent date entry. Returns (date, entry) or None."""
    if not state:
        return None
    latest_date = max(state.keys(), key=_date_sort_key)
    return latest_date, state[latest_date]
