QUEUE_EMOJI = {
    "1": "\U0001f7e1",  # 🟡
    "2": "\U0001f7e2",  # 🟢
    "3": "\U0001f7e0",  # 🟠
    "4": "\U0001f535",  # 🔵
    "5": "\U0001f338",  # 🟤
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


def format_schedule(parsed: dict, diff: list[dict] | None, is_first: bool) -> str:
    """Format parsed schedule (and optional diff) into a Telegram message."""
    lines = []

    # Header
    date_str = parsed.get("date") or "невідома дата"
    time_str = parsed.get("timestamp") or "?"

    if is_first:
        lines.append(f"\u26a1 Графік відключень на {date_str} (станом на {time_str})")
    else:
        lines.append(f"\U0001f504 Оновлення графіку на {date_str} (станом на {time_str})")

    lines.append("")

    # Schedule body grouped by queue number
    schedule = parsed["schedule"]
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

    # Diff section
    if diff:
        lines.append("")
        lines.append("\U0001f4cb Зміни:")
        for change in diff:
            emoji = CHANGE_EMOJI.get(change["type"], "\U0001f539")
            lines.append(f"{emoji} {change['detail']}")

    return "\n".join(lines)
