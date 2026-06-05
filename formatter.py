from stats import _progress_bar, _total_outage_minutes

QUEUE_EMOJI = {
    "1": "\U0001f7e1",  # 🟡
    "2": "\U0001f7e2",  # 🟢
    "3": "\U0001f7e0",  # 🟠
    "4": "\U0001f535",  # 🔵
    "5": "\U0001f7e4",  # 🟤
    "6": "\U0001f7e3",  # 🟣
}

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


def format_schedule(
    parsed: dict,
    diff: list[dict] | None,
    is_first: bool,
    queue_filter: str | None = None,
) -> str:
    lines = []

    date_str = parsed.get("date") or "невідома дата"
    time_str = parsed.get("timestamp") or "?"

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
        lines.append(f"  {queue_filter} · {_fmt_ranges(ranges)}")

        minutes_off = _total_outage_minutes(ranges)
        minutes_on = 24 * 60 - minutes_off
        bar = _progress_bar(minutes_off)
        lines.append(f"\n{bar}  {minutes_off / 60:.1f} год без світла · {minutes_on / 60:.1f} год зі світлом")
    else:
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

    return "\n".join(lines)
