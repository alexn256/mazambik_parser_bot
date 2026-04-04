from datetime import datetime


MINUTES_IN_DAY = 24 * 60
BAR_BLOCKS = 10
# Scale: full bar = 12 hours (realistic max outage)
BAR_MAX_MINUTES = 12 * 60


def _date_sort_key(date_str: str) -> datetime:
    try:
        return datetime.strptime(date_str, "%d.%m.%Y")
    except ValueError:
        return datetime.min


def _total_outage_minutes(ranges: list[dict]) -> int:
    """Sum up total outage duration in minutes from a list of time ranges."""
    total = 0
    for r in ranges:
        try:
            h1, m1 = map(int, r["start"].split(":"))
            h2, m2 = map(int, r["end"].split(":"))
            total += (h2 * 60 + m2) - (h1 * 60 + m1)
        except (ValueError, KeyError):
            pass
    return max(total, 0)


def _progress_bar(minutes: int) -> str:
    filled = min(round(minutes / BAR_MAX_MINUTES * BAR_BLOCKS), BAR_BLOCKS)
    return "█" * filled + "░" * (BAR_BLOCKS - filled)


def compute_stats(history: dict, queue: str | None, days: int) -> str:
    """Build a statistics message for a given queue over the last N days.

    If queue is None, prompts the user to select a queue.
    """
    if not queue:
        return (
            "ℹ️ Для перегляду статистики спочатку оберіть свою чергу.\n"
            "Натисніть «⚙️ Моя черга» у меню /start."
        )

    if not history:
        return "ℹ️ Історія ще порожня. Статистика з'явиться після перших публікацій."

    sorted_dates = sorted(history.keys(), key=_date_sort_key)[-days:]

    if not sorted_dates:
        return f"ℹ️ Немає даних за останні {days} днів."

    lines = [f"📊 Статистика за {days} днів — черга {queue}\n"]

    total_minutes = 0
    for date in sorted_dates:
        schedule = history[date]
        ranges = schedule.get(queue, [])
        minutes = _total_outage_minutes(ranges)
        total_minutes += minutes

        hours = minutes / 60
        pct = round(minutes / MINUTES_IN_DAY * 100)
        bar = _progress_bar(minutes)

        if minutes == 0:
            lines.append(f"{date}  {bar}  світло весь день")
        else:
            lines.append(f"{date}  {bar}  {hours:.1f} год ({pct}%)")

    avg_minutes = total_minutes / len(sorted_dates)
    avg_hours = avg_minutes / 60
    avg_pct = round(avg_minutes / MINUTES_IN_DAY * 100)
    lines.append(f"\nСереднє: {avg_hours:.1f} год/день ({avg_pct}%)")

    return "\n".join(lines)
