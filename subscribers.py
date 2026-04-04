import json
import os
import tempfile


def load_subscribers(path: str) -> dict[int, str | None]:
    """Load subscribers from JSON file.

    Returns a dict mapping chat_id → queue (e.g. "3.2") or None (all queues).

    Automatically migrates old list format [id1, id2] to new dict format.
    """
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        # Migrate old list format
        if isinstance(data, list):
            return {int(x): None for x in data}
        return {int(k): v for k, v in data.items()}
    except (json.JSONDecodeError, IOError, ValueError):
        return {}


def save_subscribers(subscribers: dict[int, str | None], path: str) -> None:
    """Atomically save subscribers dict to JSON file."""
    dir_name = os.path.dirname(os.path.abspath(path))
    fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".json.tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({str(k): v for k, v in subscribers.items()}, f)
        os.replace(tmp_path, path)
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


def add_subscriber(chat_id: int, path: str) -> bool:
    """Add a subscriber with no queue filter. Returns True if added, False if already exists."""
    subs = load_subscribers(path)
    if chat_id in subs:
        return False
    subs[chat_id] = None
    save_subscribers(subs, path)
    return True


def remove_subscriber(chat_id: int, path: str) -> bool:
    """Remove a subscriber. Returns True if removed, False if not found."""
    subs = load_subscribers(path)
    if chat_id not in subs:
        return False
    del subs[chat_id]
    save_subscribers(subs, path)
    return True


def set_subscriber_queue(chat_id: int, queue: str | None, path: str) -> None:
    """Set queue filter for a subscriber. queue=None means all queues."""
    subs = load_subscribers(path)
    subs[chat_id] = queue
    save_subscribers(subs, path)
