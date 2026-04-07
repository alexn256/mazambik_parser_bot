import pytest
from stats import _progress_bar, _total_outage_minutes, BAR_BLOCKS


class TestProgressBar:
    def test_zero_minutes(self):
        bar = _progress_bar(0)
        assert bar == "🟩" * BAR_BLOCKS

    def test_max_minutes(self):
        bar = _progress_bar(720)  # 12 hours = max
        assert bar == "🟥" * BAR_BLOCKS

    def test_half(self):
        bar = _progress_bar(360)  # 6 hours = half
        assert bar.count("🟥") == 5
        assert bar.count("🟩") == 5

    def test_length_always_ten(self):
        for minutes in [0, 100, 360, 720, 999]:
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
