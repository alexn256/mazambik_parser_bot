import asyncio

from datetime import datetime

import pytest
import main
import sender
from main import (
    _find_next_range,
    _format_duration,
    _refresh_stamp,
    _restoration_hint,
    _time_to_minutes,
)


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


class TestRefreshStamp:
    """A re-publication that changed nothing still moves the provider's stamp."""

    DATE = "10.04.2026"

    @pytest.fixture
    def saved(self, monkeypatch):
        """Capture save_state instead of writing over the real state file."""
        calls = []
        monkeypatch.setattr(main, "save_state", lambda state, path: calls.append(state))
        return calls

    def _state(self):
        return {self.DATE: {
            "last_timestamp": "22:18",
            "updated_at": "10.04.2026 22:18",
            "source": "poe.pl.ua",
            "schedule": {"1.1": []},
            "update_count": 3,
        }}

    def test_newer_stamp_is_written(self, saved):
        state = self._state()
        _refresh_stamp(state, {
            "date": self.DATE,
            "timestamp": "08:05",
            "updated_at": "11.04.2026 08:05",
            "source": "poe.pl.ua",
        })
        assert state[self.DATE]["updated_at"] == "11.04.2026 08:05"
        assert state[self.DATE]["last_timestamp"] == "08:05"
        assert len(saved) == 1

    def test_update_count_is_untouched(self, saved):
        state = self._state()
        _refresh_stamp(state, {
            "date": self.DATE,
            "timestamp": "08:05",
            "updated_at": "11.04.2026 08:05",
        })
        # update_count tracks announced changes, and nothing was announced
        assert state[self.DATE]["update_count"] == 3

    def test_identical_stamp_writes_nothing(self, saved):
        state = self._state()
        _refresh_stamp(state, {
            "date": self.DATE,
            "timestamp": "22:18",
            "updated_at": "10.04.2026 22:18",
        })
        assert saved == []

    def test_screenshot_without_a_stamp_never_clobbers_the_site_one(self, saved):
        state = self._state()
        _refresh_stamp(state, {
            "date": self.DATE,
            "timestamp": "09:41",
            "source": "telegram",
        })
        assert state[self.DATE]["updated_at"] == "10.04.2026 22:18"
        assert state[self.DATE]["source"] == "poe.pl.ua"
        assert saved == []

    def test_unknown_date_is_ignored(self, saved):
        state = self._state()
        _refresh_stamp(state, {"date": "01.01.2027", "updated_at": "01.01.2027 10:00"})
        assert saved == []


@pytest.fixture
def photos(monkeypatch):
    """Stub the photo upload; without it these tests hit the real Bot API.

    Also empties the picture cache, which lives for the life of the process and
    would otherwise carry a drawing from one test into the next.
    """
    main._pictures.clear()
    sent = []

    async def fake_photo(token, chat_id, photo, caption=None):
        sent.append((chat_id, photo))
        # Telegram answers an upload with the id it filed the photo under
        return photo if isinstance(photo, str) else "file-id-1"

    monkeypatch.setattr(main, "send_photo", fake_photo)
    return sent


class TestSendDaySchedule:
    """A day the provider announced as quiet is a fact, not a missing schedule."""

    @pytest.fixture
    def sent(self, monkeypatch, photos):
        messages = []
        async def fake_send(token, chat_id, text):
            messages.append(text)
        monkeypatch.setattr(main, "send_message", fake_send)
        monkeypatch.setattr(main, "load_subscribers", lambda path: {42: "1.1"})
        return messages

    QUIET = {
        "last_timestamp": "20:19",
        "updated_at": "13.09.2026 20:19",
        "schedule": {f"{q}.{s}": [] for q in range(1, 7) for s in (1, 2)},
    }
    BUSY = {
        "last_timestamp": "22:18",
        "updated_at": "14.09.2026 22:18",
        "schedule": {"1.1": [{"start": "08:00", "end": "10:00"}]},
    }

    def test_quiet_day_states_it_plainly(self, sent):
        asyncio.run(main._send_day_schedule(42, "14.09.2026", self.QUIET, "сьогодні"))
        assert "не прогнозується" in sent[0]
        assert "немає відключень" not in sent[0]

    def test_quiet_day_cites_the_provider_stamp(self, sent):
        asyncio.run(main._send_day_schedule(42, "14.09.2026", self.QUIET, "сьогодні"))
        assert "13.09.2026 20:19" in sent[0]

    def test_quiet_day_without_a_stamp_omits_the_source_line(self, sent):
        entry = {**self.QUIET, "updated_at": None, "last_timestamp": None}
        asyncio.run(main._send_day_schedule(42, "14.09.2026", entry, "сьогодні"))
        assert "станом на" not in sent[0]

    def test_day_with_outages_still_gets_the_grid(self, sent):
        asyncio.run(main._send_day_schedule(42, "14.09.2026", self.BUSY, "завтра"))
        assert "08:00–10:00" in sent[0]


class TestRestorationHint:
    """The printed end of an outage is the worst case, not a promise."""

    def test_names_the_earliest_restoration_time(self):
        outage = {"start": "13:00", "end": "15:00"}
        assert _restoration_hint(outage, now_minutes=13 * 60 + 40) == (
            "💡 Світло може з'явитись раніше — з 14:30"
        )

    def test_inside_the_window_it_stops_naming_a_time(self):
        outage = {"start": "13:00", "end": "15:00"}
        assert _restoration_hint(outage, now_minutes=14 * 60 + 40) == (
            "💡 Світло має з'явитись найближчим часом"
        )

    def test_outage_ending_at_midnight(self):
        outage = {"start": "22:00", "end": "00:00"}
        assert "23:30" in _restoration_hint(outage, now_minutes=22 * 60)

    def test_outage_no_longer_than_the_window_says_nothing(self):
        # A lone switching cell is all edge — there is no "earlier" to promise
        assert _restoration_hint({"start": "00:00", "end": "00:30"}, 0) is None


class TestQuietForecastThenSchedule:
    """A grid arriving after a quiet forecast is a first publication."""

    DATE = "15.09.2026"
    EMPTY = {f"{q}.{s}": [] for q in range(1, 7) for s in (1, 2)}
    GRID = {**EMPTY, "1.1": [{"start": "08:00", "end": "10:00"}]}

    @pytest.fixture
    def pipeline(self, monkeypatch, tmp_path, photos):
        sent = []
        async def fake_send(token, chat_id, text):
            sent.append((chat_id, text))
        monkeypatch.setattr(main, "send_message", fake_send)
        monkeypatch.setattr(main, "load_subscribers", lambda path: {1: "1.1", 2: "4.2"})
        monkeypatch.setattr(main, "STATE_FILE_PATH", str(tmp_path / "state.json"))
        monkeypatch.setattr(main, "HISTORY_FILE_PATH", str(tmp_path / "history.json"))
        return sent

    def _day(self, schedule, stamp):
        return {"date": self.DATE, "schedule": schedule, "updated_at": stamp,
                "timestamp": stamp.split(" ")[1], "source": "poe.pl.ua"}

    def test_quiet_forecast_is_recorded_silently(self, pipeline):
        asyncio.run(main.process_parsed(self._day(self.EMPTY, "14.09.2026 09:00")))
        assert pipeline == []

    def test_later_grid_reads_as_a_first_publication(self, pipeline):
        asyncio.run(main.process_parsed(self._day(self.EMPTY, "14.09.2026 09:00")))
        asyncio.run(main.process_parsed(self._day(self.GRID, "14.09.2026 20:04")))
        text = pipeline[0][1]
        assert "Графік відключень" in text
        assert "Оновлення графіку" not in text
        assert "з'явились відключення" not in text

    def test_it_reaches_subscribers_of_untouched_queues_too(self, pipeline):
        asyncio.run(main.process_parsed(self._day(self.EMPTY, "14.09.2026 09:00")))
        asyncio.run(main.process_parsed(self._day(self.GRID, "14.09.2026 20:04")))
        # 4.2 has no outages in this grid, but a first publication goes to all
        assert sorted(chat_id for chat_id, _ in pipeline) == [1, 2]

    def test_a_later_correction_is_still_an_update(self, pipeline):
        asyncio.run(main.process_parsed(self._day(self.EMPTY, "14.09.2026 09:00")))
        asyncio.run(main.process_parsed(self._day(self.GRID, "14.09.2026 20:04")))
        pipeline.clear()
        longer = {**self.GRID, "1.1": [{"start": "08:00", "end": "11:00"}]}
        asyncio.run(main.process_parsed(self._day(longer, "14.09.2026 22:30")))
        assert "Оновлення графіку" in pipeline[0][1]


class TestCancellation:
    """A published schedule called off is one short fact, not a grid of blanks."""

    DATE = "15.09.2026"
    EMPTY = {f"{q}.{s}": [] for q in range(1, 7) for s in (1, 2)}
    GRID = {**EMPTY, "1.1": [{"start": "08:00", "end": "10:00"}]}

    @pytest.fixture
    def pipeline(self, monkeypatch, tmp_path, photos):
        sent = []
        async def fake_send(token, chat_id, text):
            sent.append((chat_id, text))
            return True
        monkeypatch.setattr(main, "send_message", fake_send)
        monkeypatch.setattr(sender, "send_message", fake_send)
        monkeypatch.setattr(sender, "BROADCAST_DELAY", 0)
        monkeypatch.setattr(main, "load_subscribers", lambda path: {1: "1.1", 2: "4.2"})
        monkeypatch.setattr(main, "STATE_FILE_PATH", str(tmp_path / "state.json"))
        monkeypatch.setattr(main, "HISTORY_FILE_PATH", str(tmp_path / "history.json"))
        return sent

    def _day(self, schedule, stamp):
        return {"date": self.DATE, "schedule": schedule, "updated_at": stamp,
                "timestamp": stamp.split(" ")[1], "source": "poe.pl.ua"}

    def _publish_then_cancel(self, pipeline):
        asyncio.run(main.process_parsed(self._day(self.GRID, "14.09.2026 20:04")))
        pipeline.clear()
        asyncio.run(main.process_parsed(self._day(self.EMPTY, "14.09.2026 23:15")))

    def test_says_cancelled_without_listing_queues(self, pipeline):
        self._publish_then_cancel(pipeline)
        text = pipeline[0][1]
        assert "скасовано" in text
        assert "тепер немає відключень" not in text
        assert "1 черга" not in text

    def test_cites_the_provider_stamp(self, pipeline):
        self._publish_then_cancel(pipeline)
        assert "14.09.2026 23:15" in pipeline[0][1]

    def test_reaches_every_subscriber(self, pipeline):
        # 4.2 had no outages in that grid, but everyone planned around the day
        self._publish_then_cancel(pipeline)
        assert sorted(chat_id for chat_id, _ in pipeline) == [1, 2]

    def test_a_quiet_day_that_was_never_scheduled_stays_silent(self, pipeline):
        asyncio.run(main.process_parsed(self._day(self.EMPTY, "14.09.2026 09:00")))
        assert pipeline == []


class TestProviderStampLine:
    def test_omitted_when_there_is_no_stamp_to_cite(self):
        assert main._provider_stamp_line({"date": "15.09.2026"}) is None

    def test_same_day_stamp_reads_as_a_bare_time(self):
        line = main._provider_stamp_line(
            {"date": "15.09.2026", "updated_at": "15.09.2026 08:30"})
        assert line.endswith("станом на 08:30.")


class TestIsStale:
    """Only today and tomorrow exist; a past date can only be a stale reply."""

    def _offset(self, days):
        from datetime import timedelta
        return (datetime.now(main.UKRAINE_TZ) + timedelta(days=days)).strftime("%d.%m.%Y")

    def test_today_is_current(self):
        assert main._is_stale(self._offset(0)) is False

    def test_tomorrow_is_current(self):
        assert main._is_stale(self._offset(1)) is False

    def test_yesterday_is_stale(self):
        # State keeps two days, so yesterday may be pruned — reprocessing it
        # would announce finished outages as news
        assert main._is_stale(self._offset(-1)) is True

    def test_unparseable_date_is_not_dropped(self):
        # Better to let the pipeline judge it than to silently swallow a day
        assert main._is_stale(None) is False
        assert main._is_stale("not a date") is False


class TestStatePruning:
    """State holds exactly the two days the provider publishes."""

    def test_rollover_drops_the_day_before_yesterday(self):
        from state import build_state
        state = {}
        for date in ("14.09.2026", "15.09.2026", "16.09.2026"):
            state = build_state(state, {"date": date, "schedule": {}, "timestamp": "20:00"})
        assert sorted(state) == ["15.09.2026", "16.09.2026"]


class TestSchedulePicture:
    """The provider's table travels with the message, drawn from parsed data."""

    DATE = "15.09.2026"
    EMPTY = {f"{q}.{s}": [] for q in range(1, 7) for s in (1, 2)}
    GRID = {**EMPTY, "1.1": [{"start": "08:00", "end": "10:00"}]}

    @pytest.fixture
    def pipeline(self, monkeypatch, tmp_path, photos):
        sent = []
        async def fake_send(token, chat_id, text):
            sent.append((chat_id, text))
        monkeypatch.setattr(main, "send_message", fake_send)
        monkeypatch.setattr(main, "load_subscribers", lambda path: {1: "1.1"})
        monkeypatch.setattr(main, "STATE_FILE_PATH", str(tmp_path / "state.json"))
        monkeypatch.setattr(main, "HISTORY_FILE_PATH", str(tmp_path / "history.json"))
        return sent

    def _day(self, schedule):
        return {"date": self.DATE, "schedule": schedule, "updated_at": "14.09.2026 20:04",
                "timestamp": "20:04", "source": "poe.pl.ua", "intro": []}

    def test_published_schedule_is_sent_as_a_picture_too(self, pipeline, photos):
        asyncio.run(main.process_parsed(self._day(self.GRID)))
        assert len(photos) == 1
        assert photos[0][1].startswith(b"\x89PNG")

    def test_picture_comes_before_the_text(self, pipeline, photos):
        asyncio.run(main.process_parsed(self._day(self.GRID)))
        # both went to the same chat; the photo was awaited first
        assert photos[0][0] == pipeline[0][0]

    def test_cancellation_carries_no_picture(self, pipeline, photos):
        asyncio.run(main.process_parsed(self._day(self.GRID)))
        photos.clear()
        asyncio.run(main.process_parsed(self._day(self.EMPTY)))
        assert photos == []

    def test_a_failed_drawing_does_not_stop_the_message(self, pipeline, photos, monkeypatch):
        monkeypatch.setattr(main, "render_png", lambda *a, **k: None)
        asyncio.run(main.process_parsed(self._day(self.GRID)))
        assert photos == []
        assert "Графік відключень" in pipeline[0][1]


class TestPictureCache:
    """The same drawing is neither rendered twice nor uploaded twice."""

    DAY = {"date": "15.09.2026", "updated_at": "14.09.2026 20:04", "timestamp": "20:04",
           "source": "poe.pl.ua", "intro": [],
           "schedule": {**{f"{q}.{s}": [] for q in range(1, 7) for s in (1, 2)},
                        "1.1": [{"start": "08:00", "end": "10:00"}]}}

    @pytest.fixture
    def renders(self, monkeypatch, photos):
        calls = []
        def counting_render(schedule, stamp, intro=None):
            calls.append(stamp)
            return b"\x89PNG-fake"
        monkeypatch.setattr(main, "render_png", counting_render)
        return calls

    def test_second_request_reuses_the_drawing(self, renders):
        main._grid_picture(self.DAY)
        main._grid_picture(self.DAY)
        assert len(renders) == 1

    def test_an_amended_schedule_gets_its_own_drawing(self, renders):
        main._grid_picture(self.DAY)
        amended = {**self.DAY, "schedule": {**self.DAY["schedule"],
                                            "1.1": [{"start": "08:00", "end": "11:00"}]}}
        main._grid_picture(amended)
        assert len(renders) == 2

    def test_a_new_stamp_alone_redraws(self, renders):
        # The picture prints the stamp, so a restamped day is a different picture
        main._grid_picture(self.DAY)
        main._grid_picture({**self.DAY, "updated_at": "14.09.2026 22:30"})
        assert len(renders) == 2

    def test_bytes_go_up_once_then_the_file_id_is_reused(self, renders, photos):
        picture = main._grid_picture(self.DAY)
        asyncio.run(main._send_grid(1, picture))
        asyncio.run(main._send_grid(2, picture))
        asyncio.run(main._send_grid(3, picture))
        assert photos[0][1] == b"\x89PNG-fake"      # first subscriber uploads
        assert photos[1][1] == "file-id-1"          # the rest just name it
        assert photos[2][1] == "file-id-1"

    def test_a_failed_upload_does_not_poison_the_cache(self, renders, monkeypatch):
        async def failing(token, chat_id, photo, caption=None):
            return None
        monkeypatch.setattr(main, "send_photo", failing)
        picture = main._grid_picture(self.DAY)
        asyncio.run(main._send_grid(1, picture))
        assert picture["file_id"] is None   # next send retries the upload

    def test_cache_does_not_grow_without_bound(self, renders):
        for i in range(main.PICTURE_CACHE_SIZE + 3):
            main._grid_picture({**self.DAY, "updated_at": f"14.09.2026 20:{i:02d}"})
        assert len(main._pictures) == main.PICTURE_CACHE_SIZE
