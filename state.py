import json
import os
import tempfile


def load_state(path: str) -> dict | None:
    """Load state from JSON file. Returns None if file doesn't exist or is corrupt."""
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return None


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


def is_new_day(state: dict | None, new_date: str | None) -> bool:
    """Check if this is the first update of the day.

    Args:
        state: Current saved state or None.
        new_date: Date string from the new schedule (e.g., "03.04.2026").
    """
    if state is None:
        return True
    if new_date is None:
        # Can't determine date — treat as new day to be safe
        return True
    return state.get("date") != new_date


def build_state(parsed: dict, prev_state: dict | None) -> dict:
    """Build a new state dict from parsed schedule data."""
    is_first = is_new_day(prev_state, parsed.get("date"))
    update_count = 1 if is_first else prev_state.get("update_count", 0) + 1

    return {
        "date": parsed["date"],
        "last_timestamp": parsed["timestamp"],
        "schedule": parsed["schedule"],
        "update_count": update_count,
    }
