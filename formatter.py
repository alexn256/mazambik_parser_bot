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
    "removed": "\u274c",       # ❌
    "added": "\u2795",         # ➕
    "shortened": "\u23f1",     # ⏱
    "extended": "\u23f0",      # ⏰
    "shifted": "\U0001f504",   # 🔄
    "no_outages": "\u274c",    # ❌
    "outages_appeared": "\u26a0\ufe0f",  # ⚠️
}

# Width of one time cell: "00:00–02:30" = 11 chars
_CELL = 11
_SEP = "  "  # 2 spaces between columns


def _fmt_range(r: dict) -> str:
    """Format a time range as "HH:MM–HH:MM" (no spaces around en-dash)."""
    return f"{r['start']}\u2013{r['end']}"


def _queue_block(q_num: int, schedule: dict) -> str:
    """Format one queue group (X.1 and X.2) as a symmetric two-column <pre> block."""
    emoji = QUEUE_EMOJI[str(q_num)]
    label1 = f"{q_num}.1"
    label2 = f"{q_num}.2"
    ranges1 = schedule.get(label1, [])
    ranges2 = schedule.get(label2, [])

    header = f"{emoji * 6} {q_num} черга {emoji * 6}"

    # Subheader: label1 at left edge, label2 at right edge (space-between)
    total_w = _CELL * 2 + len(_SEP)
    subheader = label1 + " " * (total_w - len(label1) - len(label2)) + label2

    if not ranges1 and not ranges2:
        total_w = _CELL * 2 + len(_SEP)
        pre_body = subheader + "\n" + "немає відключень".center(total_w)
    else:
        max_rows = max(len(ranges1), len(ranges2))
        rows = []
        for i in range(max_rows):
            cell1 = _fmt_range(ranges1[i]) if i < len(ranges1) else " " * _CELL
            cell2 = _fmt_range(ranges2[i]) if i < len(ranges2) else ""
            rows.append((cell1 + _SEP + cell2).rstrip())
        pre_body = subheader + "\n" + "\n".join(rows)

    return f"{header}\n<pre>{pre_body}</pre>"


def format_schedule(
    parsed: dict,
    diff: list[dict] | None,
    is_first: bool,
    queue_filter: str | None = None,
) -> str:
    """Format parsed schedule (and optional diff) into a Telegram message."""
    lines = []

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

        minutes_off = _total_outage_minutes(ranges)
        minutes_on = 24 * 60 - minutes_off
        bar = _progress_bar(minutes_off)
        lines.append(f"\n{bar}  {minutes_off / 60:.1f} год без світла \u00b7 {minutes_on / 60:.1f} год зі світлом")
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
