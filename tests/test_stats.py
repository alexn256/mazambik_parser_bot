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

    def test_a_shorter_window_rescales_the_bar(self):
        # 90 outage minutes is a sliver of a 24h day but all of a 90-min window
        assert _progress_bar(90, total_minutes=1440).count("🟥") == 1
        assert _progress_bar(90, total_minutes=90) == "🟥" * BAR_BLOCKS


class TestTotalOutageMinutes:
    def test_empty(self):
        assert _total_outage_minutes([]) == 0

    def test_single_range(self):
        assert _total_outage_minutes([{"start": "10:00", "end": "11:30"}]) == 90

    def test_midnight_wrap_end(self):
        # end "00:00" means midnight, not day start (23:00–00:00 = 60 min)
        assert _total_outage_minutes([{"start": "23:00", "end": "00:00"}]) == 60

    def test_midnight_wrap_does_not_eat_other_ranges(self):
        ranges = [
            {"start": "10:00", "end": "12:00"},
            {"start": "23:00", "end": "00:00"},
        ]
        assert _total_outage_minutes(ranges) == 180

    def test_multiple_ranges(self):
        ranges = [
            {"start": "08:00", "end": "09:00"},
            {"start": "14:00", "end": "15:30"},
        ]
        assert _total_outage_minutes(ranges) == 150
