import os
from datetime import datetime

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


class TestPersonalViewHeader:
    """A subscriber who only ever sees their own queue should not see its
    number said twice — once as "1 черга", again as "1.2 · ...ranges"."""

    def test_queue_and_subqueue_appear_together_once(self, outage_day):
        text = format_schedule(outage_day, diff=None, is_first=True, queue_filter="1.2")
        assert "Черга 1.2</b> · " in text
        assert text.count("1.2") == 1
        assert "1 черга" not in text

    def test_matches_how_the_rest_of_the_bot_names_a_queue(self, outage_day):
        # send_current_status / compute_stats both say "черга 1.2" as one
        # phrase — the personal view should read the same way, not split it
        # into a parent queue header over a sub-queue line
        text = format_schedule(outage_day, diff=None, is_first=True, queue_filter="1.2")
        assert "<b>\n" not in text
        assert "черга</b>\n" not in text


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


class TestPastRangesAreTrimmed:
    """A reader at 15:30 lived through whatever already finished — repeating
    it in an update tells them nothing, only what is ongoing or upcoming does.

    1.1 on the fixture day (10.04.2026): 00:00–02:30, 07:00–09:30, 13:00–15:00,
    19:00–20:30.
    """

    def _at(self, hour, minute, date=10):
        return datetime(2026, 4, date, hour, minute)

    def test_finished_ranges_vanish_from_the_full_schedule(self, outage_day):
        text = format_schedule(outage_day, diff=None, is_first=False,
                               now=self._at(15, 30))
        assert "00:00–02:30" not in text
        assert "07:00–09:30" not in text
        assert "13:00–15:00" not in text
        assert "19:00–20:30" in text        # still ahead

    def test_an_ongoing_range_is_kept_not_just_future_ones(self, outage_day):
        # 14:00 sits inside 13:00-15:00 — it has not finished yet
        text = format_schedule(outage_day, diff=None, is_first=False,
                               now=self._at(14, 0))
        assert "13:00–15:00" in text
        assert "07:00–09:30" not in text

    def test_personal_view_drops_the_same_way(self, outage_day):
        text = format_schedule(outage_day, diff=None, is_first=False,
                               queue_filter="1.1", now=self._at(15, 30))
        assert "00:00–02:30" not in text
        assert "19:00–20:30" in text

    def test_hour_totals_and_the_bar_still_describe_the_whole_day(self, outage_day):
        # 1.1's four ranges add up to 8.5h out of 24 — that total must not
        # shrink just because the printed list above it got trimmed to what
        # is still ahead, or the bar would look like the outage picture
        # itself changed when only the display did
        text = format_schedule(outage_day, diff=None, is_first=False,
                               queue_filter="1.1", now=self._at(15, 30))
        assert "8.5 год без світла" in text
        assert "15.5 год зі світлом" in text

    def test_hour_totals_do_not_move_as_the_day_goes_on(self, outage_day):
        at_dawn = format_schedule(outage_day, diff=None, is_first=False,
                                  queue_filter="1.1", now=self._at(1, 0))
        at_night = format_schedule(outage_day, diff=None, is_first=False,
                                   queue_filter="1.1", now=self._at(23, 0))
        assert "8.5 год без світла" in at_dawn
        assert "8.5 год без світла" in at_night

    def test_a_queue_with_nothing_left_today_reads_as_quiet(self, outage_day):
        # Nothing left to wait for, so the note about switching windows has
        # nothing left to caveat either — but the day still had 8.5h of
        # outage, and the bar/hours keep saying so
        text = format_schedule(outage_day, diff=None, is_first=False,
                               queue_filter="1.1", now=self._at(23, 0))
        assert "Черга 1.1</b> · немає відключень" in text
        assert SWITCHING_NOTE not in text
        assert "8.5 год без світла" in text

    def test_tomorrows_schedule_is_never_trimmed(self, outage_day):
        # Published the evening before — none of it is "in the past" yet
        tomorrow = {**outage_day, "date": "11.04.2026"}
        text = format_schedule(tomorrow, diff=None, is_first=True,
                               queue_filter="1.1", now=self._at(23, 0))
        assert "00:00–02:30, 07:00–09:30, 13:00–15:00, 19:00–20:30" in text

    def test_a_schedule_with_no_date_is_never_trimmed(self):
        schedule = {**EMPTY_SCHEDULE, "1.1": [{"start": "08:00", "end": "10:00"}]}
        text = format_schedule({"schedule": schedule}, diff=None, is_first=True,
                               queue_filter="1.1", now=self._at(15, 30))
        assert "08:00–10:00" in text

    def test_without_an_explicit_now_it_uses_the_real_clock(self, outage_day):
        # Just a smoke test that the default path does not crash and still
        # returns a schedule — real-clock behaviour is exercised by every
        # other call in the suite that omits `now` entirely.
        text = format_schedule(outage_day, diff=None, is_first=False)
        assert "Графік" in text or "Оновлення" in text
