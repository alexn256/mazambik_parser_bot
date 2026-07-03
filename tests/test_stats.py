import pytest
from stats import _progress_bar, _total_outage_minutes, BAR_BLOCKS, BAR_MAX_MINUTES


class TestProgressBar:
    def test_zero_minutes(self):
        bar = _progress_bar(0)
        assert bar == "🟩" * BAR_BLOCKS

    def test_max_minutes(self):
        # bar is proportional to a full 24-hour day
        bar = _progress_bar(BAR_MAX_MINUTES)
        assert bar == "🟥" * BAR_BLOCKS

    def test_half(self):
        bar = _progress_bar(BAR_MAX_MINUTES // 2)  # 12 hours = half the day
        assert bar.count("🟥") == BAR_BLOCKS // 2
        assert bar.count("🟩") == BAR_BLOCKS // 2

    def test_proportional_to_full_day(self):
        bar = _progress_bar(6 * 60)  # 6h out of 24h = a quarter
        assert bar.count("🟥") == 3

    def test_length_always_full(self):
        for minutes in [0, 100, 360, 720, 1440, 9999]:
            bar = _progress_bar(minutes)
            total = bar.count("🟥") + bar.count("🟩")
            assert total == BAR_BLOCKS


class TestTotalOutageMinutes:
    def test_empty(self):
        assert _total_outage_minutes([]) == 0

    def test_single_range(self):
        assert _total_outage_minutes([{"start": "10:00", "end": "11:30"}]) == 90

    def test_multiple_ranges(self):
        ranges = [
            {"start": "08:00", "end": "09:00"},
            {"start": "14:00", "end": "15:30"},
        ]
        assert _total_outage_minutes(ranges) == 150
