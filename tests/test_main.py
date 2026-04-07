import pytest
from main import _time_to_minutes, _format_duration, _find_next_range


class TestTimeToMinutes:
    def test_midnight(self):
        assert _time_to_minutes("00:00") == 0

    def test_one_hour(self):
        assert _time_to_minutes("01:00") == 60

    def test_noon(self):
        assert _time_to_minutes("12:00") == 720

    def test_with_minutes(self):
        assert _time_to_minutes("10:30") == 630

    def test_end_of_day(self):
        assert _time_to_minutes("23:59") == 1439


class TestFormatDuration:
    def test_only_minutes(self):
        assert _format_duration(45) == "45 хв"

    def test_only_hours(self):
        assert _format_duration(120) == "2 год"

    def test_hours_and_minutes(self):
        assert _format_duration(90) == "1 год 30 хв"

    def test_zero(self):
        assert _format_duration(0) == "0 хв"

    def test_one_hour_one_minute(self):
        assert _format_duration(61) == "1 год 1 хв"


class TestFindNextRange:
    RANGES = [
        {"start": "08:00", "end": "09:30"},
        {"start": "14:00", "end": "15:30"},
    ]

    def test_finds_next_after_early_time(self):
        result = _find_next_range(self.RANGES, after_minutes=400)  # 06:40
        assert result["start"] == "08:00"

    def test_finds_second_range(self):
        result = _find_next_range(self.RANGES, after_minutes=600)  # 10:00
        assert result["start"] == "14:00"

    def test_returns_none_when_no_next(self):
        result = _find_next_range(self.RANGES, after_minutes=900)  # 15:00
        assert result is None

    def test_empty_ranges(self):
        result = _find_next_range([], after_minutes=0)
        assert result is None
