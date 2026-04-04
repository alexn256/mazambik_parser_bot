import json
import os
import tempfile
from datetime import datetime


def _date_sort_key(date_str: str) -> datetime:
    try:
        return datetime.strptime(date_str, "%d.%m.%Y")
    except ValueError:
        return datetime.min


def load_history(path: str) -> dict:
    """Load history from JSON file. Returns empty dict if missing or corrupt.

    Format: {"DD.MM.YYYY": {"1.1": [{"start": ..., "end": ...}], ...}, ...}
    """
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {}


def save_history(history: dict, path: str) -> None:
    """Atomically save history to JSON file."""
    dir_name = os.path.dirname(os.path.abspath(path))
    fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".json.tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(history, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, path)
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


def record_day(history: dict, date: str, schedule: dict) -> dict:
    """Save (or overwrite) the schedule for a given date. Keeps at most 31 days."""
    history[date] = schedule

    if len(history) > 31:
        dates = sorted(history.keys(), key=_date_sort_key)
        for old_date in dates[:-31]:
            del history[old_date]

    return history
