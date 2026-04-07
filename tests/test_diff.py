import pytest
from diff import compute_diff


RANGE_A = {"start": "10:00", "end": "11:30"}
RANGE_B = {"start": "14:00", "end": "15:30"}
RANGE_A_SHORTER = {"start": "10:00", "end": "11:00"}
RANGE_A_EXTENDED = {"start": "10:00", "end": "12:00"}
RANGE_A_SHIFTED = {"start": "10:30", "end": "11:30"}  # same end, different start


class TestComputeDiff:
    def test_no_changes(self):
        schedule = {"1.1": [RANGE_A]}
        assert compute_diff(schedule, schedule) == []

    def test_range_removed(self):
        diff = compute_diff({"1.1": [RANGE_A]}, {"1.1": []})
        assert len(diff) == 1
        assert diff[0]["type"] == "no_outages"
        assert diff[0]["queue"] == "1.1"

    def test_range_appeared(self):
        diff = compute_diff({"1.1": []}, {"1.1": [RANGE_A]})
        assert len(diff) == 1
        assert diff[0]["type"] == "outages_appeared"
        assert diff[0]["queue"] == "1.1"

    def test_range_shortened(self):
        diff = compute_diff({"1.1": [RANGE_A]}, {"1.1": [RANGE_A_SHORTER]})
        assert len(diff) == 1
        assert diff[0]["type"] == "shortened"

    def test_range_extended(self):
        diff = compute_diff({"1.1": [RANGE_A]}, {"1.1": [RANGE_A_EXTENDED]})
        assert len(diff) == 1
        assert diff[0]["type"] == "extended"

    def test_range_shifted(self):
        diff = compute_diff({"1.1": [RANGE_A]}, {"1.1": [RANGE_A_SHIFTED]})
        assert len(diff) == 1
        assert diff[0]["type"] == "shifted"

    def test_multiple_queues_only_changed_returned(self):
        old = {"1.1": [RANGE_A], "1.2": [RANGE_B]}
        new = {"1.1": [RANGE_A_SHORTER], "1.2": [RANGE_B]}
        diff = compute_diff(old, new)
        assert len(diff) == 1
        assert diff[0]["queue"] == "1.1"

    def test_empty_schedules(self):
        assert compute_diff({}, {}) == []
