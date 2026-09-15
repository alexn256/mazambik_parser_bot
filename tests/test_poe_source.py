import os

import pytest

from poe_source import (
    PoeParseError,
    _cells_to_ranges,
    _queue_label,
    _slot_time,
    has_outages,
    parse_response,
)

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")

ON = "light_1"
OFF = "light_2"
SWITCH = "light_3"


def fixture(name: str) -> str:
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return f.read()


@pytest.fixture
def outage_day():
    """Real response for 10.04.2026 — the day used to verify the parser by eye."""
    days = parse_response(fixture("gpv_outage_day.html"))
    assert len(days) == 1
    return days[0]


class TestSlotTime:
    def test_slot_boundaries(self):
        assert _slot_time(0) == "00:00"
        assert _slot_time(1) == "00:30"
        assert _slot_time(47) == "23:30"

    def test_last_boundary_is_midnight_not_24(self):
        # diff.py reads an end of "00:00" as midnight; "24:00" would break it
        assert _slot_time(48) == "00:00"


class TestQueueLabel:
    def test_row_order_is_subqueue_major(self):
        assert [_queue_label(i) for i in range(4)] == ["1.1", "1.2", "2.1", "2.2"]
        assert _queue_label(11) == "6.2"


class TestCellsToRanges:
    def test_all_on_means_no_outages(self):
        assert _cells_to_ranges([ON] * 48) == []

    def test_switching_cells_extend_the_outage(self):
        cells = [OFF, OFF, SWITCH] + [ON] * 45
        assert _cells_to_ranges(cells) == [{"start": "00:00", "end": "01:30"}]

    def test_leading_switch_cell_is_an_outage(self):
        # A block wrapping past midnight leaves its switching half-hour at 00:00
        cells = [SWITCH] + [ON] * 47
        assert _cells_to_ranges(cells) == [{"start": "00:00", "end": "00:30"}]

    def test_outage_running_to_end_of_day(self):
        cells = [ON] * 46 + [OFF, OFF]
        assert _cells_to_ranges(cells) == [{"start": "23:00", "end": "00:00"}]

    def test_separate_blocks_stay_separate(self):
        cells = [OFF] + [ON] * 2 + [OFF] + [ON] * 44
        assert _cells_to_ranges(cells) == [
            {"start": "00:00", "end": "00:30"},
            {"start": "01:30", "end": "02:00"},
        ]


class TestParseOutageDay:
    def test_date_and_provider_stamp(self, outage_day):
        assert outage_day["date"] == "10.04.2026"
        assert outage_day["updated_at"] == "10.04.2026 22:18"
        assert outage_day["timestamp"] == "22:18"
        assert outage_day["source"] == "poe.pl.ua"
        assert outage_day["has_grid"] is True

    def test_all_twelve_queues_present(self, outage_day):
        assert sorted(outage_day["schedule"]) == sorted(
            f"{q}.{s}" for q in range(1, 7) for s in (1, 2)
        )

    def test_ranges_match_the_published_grid(self, outage_day):
        assert outage_day["schedule"]["1.1"] == [
            {"start": "00:00", "end": "02:30"},
            {"start": "07:00", "end": "09:30"},
            {"start": "13:00", "end": "15:00"},
            {"start": "19:00", "end": "20:30"},
        ]

    def test_has_outages(self, outage_day):
        assert has_outages(outage_day["schedule"])


class TestParseQuietDays:
    def test_every_day_block_is_returned(self):
        days = parse_response(fixture("gpv_no_outages.html"))
        assert [d["date"] for d in days] == ["14.09.2026", "15.09.2026"]

    def test_stamp_may_predate_the_schedule_date(self):
        # Published the evening before — the case the Telegram post time got wrong
        day = parse_response(fixture("gpv_no_outages.html"))[0]
        assert day["updated_at"] == "13.09.2026 20:19"

    def test_quiet_day_has_empty_queues_not_missing_ones(self):
        day = parse_response(fixture("gpv_no_outages.html"))[0]
        assert day["has_grid"] is False
        assert len(day["schedule"]) == 12
        assert not has_outages(day["schedule"])


class TestParseFailures:
    def test_empty_response_is_not_an_error(self):
        # The provider returns nothing for dates it has no record of
        assert parse_response("") == []
        assert parse_response("   ") == []

    def test_unrecognised_markup_raises(self):
        with pytest.raises(PoeParseError):
            parse_response("<div class='something-else'>hello</div>")

    def test_truncated_grid_raises_instead_of_reporting_false_uptime(self):
        html = fixture("gpv_outage_day.html").replace(
            '<td class="light_2">&nbsp</td>', "", 1
        )
        with pytest.raises(PoeParseError):
            parse_response(html)

    def test_block_without_a_date_raises(self):
        with pytest.raises(PoeParseError):
            parse_response('<div class="gpvinfodetail"> no date here </div>')



class TestParseIntro:
    """The provider's "обсяг черг" preamble, kept for the picture we draw."""

    def test_lines_are_returned_in_order(self, outage_day):
        intro = outage_day["intro"]
        assert len(intro) == 7  # the date sentence plus six volume lines

    def test_bold_runs_survive(self, outage_day):
        first = outage_day["intro"][0]
        assert first[0][1] is False and "Полтавській області" in first[0][0]
        assert first[1] == ("10 квітня 2026 року", True)

    def test_volume_line_splits_into_weighted_runs(self, outage_day):
        line = outage_day["intro"][1]
        assert [t for t, _ in line] == [
            "з 00:00 по 06:00", " запроваджений ГПВ в обсязі ", "1", " черг."
        ]
        assert [b for _, b in line] == [True, False, True, False]

    def test_caption_is_not_part_of_the_intro(self, outage_day):
        # "Порядок відключення черг" is drawn by the renderer, not carried as text
        assert all("Порядок відключення" not in t
                   for line in outage_day["intro"] for t, _ in line)

    def test_quiet_day_has_no_intro(self):
        day = parse_response(fixture("gpv_no_outages.html"))[0]
        assert day["intro"] == []


class TestFormatStampUa:
    """The picture carries the provider's wording, not our compact date."""

    def test_date_and_time(self):
        from poe_source import format_stamp_ua
        assert format_stamp_ua("10.04.2026 22:18") == "10 квітня 2026 22:18"

    def test_leading_zero_is_dropped_like_the_site_does(self):
        from poe_source import format_stamp_ua
        assert format_stamp_ua("01.09.2026 08:05") == "1 вересня 2026 08:05"

    def test_date_only(self):
        from poe_source import format_stamp_ua
        assert format_stamp_ua("10.04.2026") == "10 квітня 2026"

    def test_nothing_to_format(self):
        from poe_source import format_stamp_ua
        assert format_stamp_ua(None) == ""
        assert format_stamp_ua("whenever") == "whenever"
