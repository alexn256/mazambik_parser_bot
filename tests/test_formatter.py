import os

import pytest

from formatter import SWITCHING_NOTE, format_schedule, format_stamp
from poe_source import parse_response

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")

EMPTY_SCHEDULE = {f"{q}.{s}": [] for q in range(1, 7) for s in (1, 2)}


@pytest.fixture
def outage_day():
    with open(os.path.join(FIXTURES, "gpv_outage_day.html"), encoding="utf-8") as f:
        return parse_response(f.read())[0]


class TestFormatStamp:
    def test_same_day_stamp_shows_only_the_time(self):
        assert format_stamp({"date": "10.04.2026",
                             "updated_at": "10.04.2026 22:18"}) == "22:18"

    def test_stamp_from_another_day_keeps_its_date(self):
        # Published the evening before — hiding the date would mislead
        assert format_stamp({"date": "15.09.2026",
                             "updated_at": "14.09.2026 20:04"}) == "14.09.2026 20:04"

    def test_falls_back_to_the_bare_timestamp(self):
        assert format_stamp({"date": "10.04.2026", "timestamp": "09:41"}) == "09:41"

    def test_no_stamp_at_all(self):
        assert format_stamp({"date": "10.04.2026"}) == "?"


class TestSwitchingNote:
    """The soft half hour is stated once per message, not beside every range."""

    def test_note_appears_once_in_a_full_schedule(self, outage_day):
        text = format_schedule(outage_day, diff=None, is_first=True)
        assert text.count(SWITCHING_NOTE) == 1

    def test_note_appears_for_a_single_queue(self, outage_day):
        text = format_schedule(outage_day, diff=None, is_first=True, queue_filter="1.1")
        assert SWITCHING_NOTE in text

    def test_ranges_themselves_stay_unannotated(self, outage_day):
        text = format_schedule(outage_day, diff=None, is_first=True, queue_filter="1.1")
        assert "00:00–02:30" in text
        assert "(30)" not in text

    def test_no_note_when_the_queue_has_no_outages(self, outage_day):
        quiet = {**outage_day, "schedule": {**outage_day["schedule"], "1.1": []}}
        text = format_schedule(quiet, diff=None, is_first=True, queue_filter="1.1")
        assert SWITCHING_NOTE not in text

    def test_no_note_when_nothing_is_switched_off_at_all(self):
        text = format_schedule({"date": "14.09.2026", "schedule": EMPTY_SCHEDULE},
                               diff=None, is_first=True)
        assert SWITCHING_NOTE not in text

    def test_note_sits_below_the_changes_list(self, outage_day):
        diff = [{"queue": "1.1", "type": "added", "detail": "Черга 1.1: додали 22:00–23:30"}]
        text = format_schedule(outage_day, diff=diff, is_first=False, queue_filter="1.1")
        assert text.index("Зміни:") < text.index(SWITCHING_NOTE)
