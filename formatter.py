QUEUE_EMOJI = {
    "1": "\U0001f7e1",  # 🟡
    "2": "\U0001f7e2",  # 🟢
    "3": "\U0001f7e0",  # 🟠
    "4": "\U0001f535",  # 🔵
    "5": "\U0001f7e4",  # 🟤
    "6": "\U0001f7e3",  # 🟣
}

CHANGE_EMOJI = {
    "removed": "\u274c",       # ❌
    "added": "\u2795",         # ➕
    "shortened": "\u23f1",     # ⏱
    "extended": "\u23f0",      # ⏰
    "shifted": "\U0001f504",   # 🔄
    "no_outages": "\u274c",    # ❌
    "outages_appeared": "\u26a0\ufe0f",  # ⚠️
}


def format_schedule(
    parsed: dict,
    diff: list[dict] | None,
    is_first: bool,
    queue_filter: str | None = None,
) -> str:
    """Format parsed schedule (and optional diff) into a Telegram message.

    If queue_filter is set (e.g. "3.2"), only that subqueue is shown and
    the diff is filtered to changes relevant to that queue.
    """
    lines = []

    # Header
    date_str = parsed.get("date") or "невідома дата"
    time_str = parsed.get("timestamp") or "?"

    if is_first:
        lines.append(f"\u26a1 Графік відключень на {date_str} (станом на {time_str})")
    else:
        lines.append(f"\U0001f504 Оновлення графіку на {date_str} (станом на {time_str})")

    lines.append("")

    schedule = parsed["schedule"]

    if queue_filter:
        q_num = queue_filter.split(".")[0]
        emoji = QUEUE_EMOJI[q_num]
        lines.append(f"{emoji} {q_num} черга")
        ranges = schedule.get(queue_filter, [])
        if ranges:
            times = ", ".join(f'{r["start"]} \u2013 {r["end"]}' for r in ranges)
            lines.append(f"{queue_filter} \u2192 {times}")
        else:
            lines.append(f"{queue_filter} \u2192 немає відключень")
    else:
        for q_num in range(1, 7):
            emoji = QUEUE_EMOJI[str(q_num)]
            lines.append(f"{emoji} {q_num} черга")
            for sub in ["1", "2"]:
                label = f"{q_num}.{sub}"
                ranges = schedule.get(label, [])
                if ranges:
                    times = ", ".join(f'{r["start"]} \u2013 {r["end"]}' for r in ranges)
                    lines.append(f"{label} \u2192 {times}")
                else:
                    lines.append(f"{label} \u2192 немає відключень")

    # Diff section (filtered by queue if needed)
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
