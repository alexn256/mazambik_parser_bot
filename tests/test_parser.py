import pytest
from parser import _parse_time_ranges


class TestParseTimeRanges:
    def test_valid_range(self):
        assert _parse_time_ranges("11:00 - 12:30") == [{"start": "11:00", "end": "12:30"}]

    def test_multiple_valid_ranges(self):
        result = _parse_time_ranges("07:00 - 08:30\n13:00 - 14:00")
        assert result == [
            {"start": "07:00", "end": "08:30"},
            {"start": "13:00", "end": "14:00"},
        ]

    def test_discards_start_greater_than_end(self):
        # OCR misread: 23:30 - 22:30 (start > end)
        assert _parse_time_ranges("23:30 - 22:30") == []

    def test_discards_start_equal_to_end(self):
        assert _parse_time_ranges("12:00 - 12:00") == []

    def test_ocr_artifact_01_instead_of_11(self):
        # "01:00 - 12:30" should be kept since 01 < 12 — parser can't know it's wrong
        # but "21:30 - 22:30" is valid, "23:30 - 22:30" is not
        assert _parse_time_ranges("23:30 - 22:30") == []

    def test_valid_range_with_dash_variants(self):
        # em-dash variant
        assert _parse_time_ranges("11:00 – 12:30") == [{"start": "11:00", "end": "12:30"}]

    def test_empty_text(self):
        assert _parse_time_ranges("") == []

    def test_no_time_in_text(self):
        assert _parse_time_ranges("немає відключень") == []

    def test_keeps_valid_filters_invalid(self):
        text = "11:00 - 12:30\n23:30 - 22:30\n19:00 - 20:00"
        result = _parse_time_ranges(text)
        assert result == [
            {"start": "11:00", "end": "12:30"},
            {"start": "19:00", "end": "20:00"},
        ]

    def test_midnight_wrap_range(self):
        # 23:00 – 00:00 is a valid range (midnight next day)
        assert _parse_time_ranges("23:00 - 00:00") == [{"start": "23:00", "end": "00:00"}]

    def test_midnight_wrap_half_hour(self):
        assert _parse_time_ranges("23:30 - 00:00") == [{"start": "23:30", "end": "00:00"}]

    def test_midnight_wrap_with_other_ranges(self):
        text = "05:00 - 07:30\n11:00 - 12:30\n17:00 - 19:00\n23:00 - 00:00"
        result = _parse_time_ranges(text)
        assert result == [
            {"start": "05:00", "end": "07:30"},
            {"start": "11:00", "end": "12:30"},
            {"start": "17:00", "end": "19:00"},
            {"start": "23:00", "end": "00:00"},
        ]
