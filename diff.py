def compute_diff(old_schedule: dict, new_schedule: dict) -> list[dict]:
    """Compare two schedules and return a list of changes.

    Each change is a dict:
        {
            "queue": "1.1",
            "type": "removed" | "added" | "shortened" | "extended" | "no_outages" | "outages_appeared",
            "detail": "human-readable description"
        }
    """
    changes = []
    all_queues = sorted(set(list(old_schedule.keys()) + list(new_schedule.keys())))

    for queue in all_queues:
        old_ranges = old_schedule.get(queue, [])
        new_ranges = new_schedule.get(queue, [])

        old_set = {_range_key(r) for r in old_ranges}
        new_set = {_range_key(r) for r in new_ranges}

        if old_set == new_set:
            continue

        # Check if queue went from having outages to none
        if old_ranges and not new_ranges:
            changes.append({
                "queue": queue,
                "type": "no_outages",
                "detail": f"Черга {queue}: тепер немає відключень",
            })
            continue

        # Check if queue went from no outages to having some
        if not old_ranges and new_ranges:
            times = ", ".join(f'{r["start"]}–{r["end"]}' for r in new_ranges)
            changes.append({
                "queue": queue,
                "type": "outages_appeared",
                "detail": f"Черга {queue}: з'явились відключення {times}",
            })
            continue

        # Find removed ranges (in old but not in new)
        removed = old_set - new_set
        added = new_set - old_set

        # Try to match overlapping ranges for shortened/extended detection
        matched_old = set()
        matched_new = set()

        for old_key in sorted(removed):
            old_start, old_end = _parse_range_key(old_key)
            for new_key in sorted(added):
                if new_key in matched_new:
                    continue
                new_start, new_end = _parse_range_key(new_key)
                # Check overlap
                if old_start <= new_end and new_start <= old_end:
                    # These ranges overlap — it's a modification
                    if _to_minutes(new_end) < _to_minutes(old_end):
                        changes.append({
                            "queue": queue,
                            "type": "shortened",
                            "detail": (
                                f"Черга {queue}: скоротили "
                                f"(було {old_key} → стало {new_key})"
                            ),
                        })
                    elif _to_minutes(new_end) > _to_minutes(old_end):
                        changes.append({
                            "queue": queue,
                            "type": "extended",
                            "detail": (
                                f"Черга {queue}: розширили "
                                f"(було {old_key} → стало {new_key})"
                            ),
                        })
                    else:
                        changes.append({
                            "queue": queue,
                            "type": "shifted",
                            "detail": (
                                f"Черга {queue}: змінили час "
                                f"(було {old_key} → стало {new_key})"
                            ),
                        })
                    matched_old.add(old_key)
                    matched_new.add(new_key)
                    break

        # Remaining unmatched removals
        for old_key in sorted(removed - matched_old):
            changes.append({
                "queue": queue,
                "type": "removed",
                "detail": f"Черга {queue}: прибрали {old_key}",
            })

        # Remaining unmatched additions
        for new_key in sorted(added - matched_new):
            changes.append({
                "queue": queue,
                "type": "added",
                "detail": f"Черга {queue}: додали {new_key}",
            })

    return changes


def _range_key(r: dict) -> str:
    """Convert a range dict to a string key for comparison."""
    return f'{r["start"]}–{r["end"]}'


def _parse_range_key(key: str) -> tuple[str, str]:
    """Parse a range key back to (start, end) strings."""
    parts = key.split("–")
    return parts[0].strip(), parts[1].strip()


def _to_minutes(time_str: str) -> int:
    """Convert HH:MM string to minutes since midnight."""
    h, m = time_str.split(":")
    return int(h) * 60 + int(m)
