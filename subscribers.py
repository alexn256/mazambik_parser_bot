import json
import os
import tempfile


def load_subscribers(path: str) -> list[int]:
    """Load subscriber chat IDs from JSON file."""
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return [int(x) for x in data]
    except (json.JSONDecodeError, IOError, ValueError):
        return []


def save_subscribers(subscribers: list[int], path: str) -> None:
    """Atomically save subscriber list to JSON file."""
    dir_name = os.path.dirname(os.path.abspath(path))
    fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".json.tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(subscribers, f)
        os.replace(tmp_path, path)
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


def add_subscriber(chat_id: int, path: str) -> bool:
    """Add a subscriber. Returns True if added, False if already subscribed."""
    subs = load_subscribers(path)
    if chat_id in subs:
        return False
    subs.append(chat_id)
    save_subscribers(subs, path)
    return True


def remove_subscriber(chat_id: int, path: str) -> bool:
    """Remove a subscriber. Returns True if removed, False if not found."""
    subs = load_subscribers(path)
    if chat_id not in subs:
        return False
    subs.remove(chat_id)
    save_subscribers(subs, path)
    return True
