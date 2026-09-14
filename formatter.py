from stats import _progress_bar, _total_outage_minutes

QUEUE_EMOJI = {
    "1": "\U0001f7e1",  # 🟡
    "2": "\U0001f7e2",  # 🟢
    "3": "\U0001f7e0",  # 🟠
    "4": "\U0001f535",  # 🔵
    "5": "\U0001f7e4",  # 🟤
    "6": "\U0001f7e3",  # 🟣
}

# Both edges of a published range are soft by half an hour: the provider cuts
# power within 30 min of the start and restores it during the last 30 min.
SWITCH_WINDOW_MINUTES = 30

SWITCHING_NOTE = (
    "⏱ Вимикають упродовж 30 хв після початку,\n"
    "   вмикають в останні 30 хв інтервалу."
)

CHANGE_EMOJI = {
    "removed": "❌",       # ❌
    "added": "➕",         # ➕
    "shortened": "⏱",     # ⏱
    "extended": "⏰",      # ⏰
    "shifted": "\U0001f504",   # 🔄
    "no_outages": "❌",    # ❌
    "outages_appeared": "⚠️",  # ⚠️
}


def _fmt_range(r: dict) -> str:
    return f"{r['start']}–{r['end']}"


def _fmt_ranges(ranges: list) -> str:
    if not ranges:
        return "немає відключень"
    return ", ".join(_fmt_range(r) for r in ranges)


def _queue_block(q_num: int, schedule: dict) -> str:
    emoji = QUEUE_EMOJI[str(q_num)]
    label1 = f"{q_num}.1"
    label2 = f"{q_num}.2"
    ranges1 = schedule.get(label1, [])
    ranges2 = schedule.get(label2, [])
    return (
        f"{emoji} <b>{q_num} черга</b>\n"
        f"  {label1} · {_fmt_ranges(ranges1)}\n"
        f"  {label2} · {_fmt_ranges(ranges2)}"
    )


def format_stamp(parsed: dict) -> str:
    """When the schedule was published or last changed.

    Prefers the provider's own stamp. Its date is dropped when it matches the
    schedule's own date, so same-day updates read as a plain time; a schedule
    published the evening before keeps its date, because that difference is
    exactly what a reader needs to see.
    """
    updated_at = parsed.get("updated_at")
    if not updated_at:
        return parsed.get("timestamp") or "?"
    stamp_date, _, stamp_time = updated_at.partition(" ")
    if stamp_time and stamp_date == parsed.get("date"):
        return stamp_time
    return updated_at


def format_schedule(
    parsed: dict,
    diff: list[dict] | None,
    is_first: bool,
    queue_filter: str | None = None,
) -> str:
    lines = []

    date_str = parsed.get("date") or "невідома дата"
    time_str = format_stamp(parsed)

    if is_first:
        lines.append(f"⚡ Графік відключень на {date_str} (станом на {time_str})")
    else:
        lines.append(f"\U0001f504 Оновлення графіку на {date_str} (станом на {time_str})")

    lines.append("")

    schedule = parsed["schedule"]

    if queue_filter:
        q_num = queue_filter.split(".")[0]
        emoji = QUEUE_EMOJI[q_num]
        lines.append(f"{emoji} <b>{q_num} черга</b>")
        ranges = schedule.get(queue_filter, [])
        has_ranges = bool(ranges)
        lines.append(f"  {queue_filter} · {_fmt_ranges(ranges)}")

        minutes_off = _total_outage_minutes(ranges)
        minutes_on = 24 * 60 - minutes_off
        bar = _progress_bar(minutes_off)
        lines.append(f"\n{bar}\n")
        lines.append(f"🕯️ {minutes_off / 60:.1f} год без світла")
        lines.append(f"💡 {minutes_on / 60:.1f} год зі світлом")
    else:
        has_ranges = any(schedule.values())
        for q_num in range(1, 7):
            lines.append(_queue_block(q_num, schedule))

    display_diff = (
        [c for c in diff if c["queue"] == queue_filter]
        if (diff and queue_filter)
        else diff
    )
    if display_diff:
        lines.append("")
        lines.append("\U0001f4cb Зміни:")
        for change in display_diff:
            emoji = CHANGE_EMOJI.get(change["type"], "\U0001f539")
            lines.append(f"{emoji} {change['detail']}")

    # The boundaries of every range are soft by the same half hour, so this is
    # said once at the bottom rather than repeated beside each of the ~48 times
    # a full schedule prints. Pointless when nothing is switched off at all.
    if has_ranges:
        lines.append("")
        lines.append(SWITCHING_NOTE)

    return "\n".join(lines)
