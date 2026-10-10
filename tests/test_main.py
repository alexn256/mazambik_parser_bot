import asyncio

from datetime import datetime

import httpx
import pytest
import main
import sender
from main import (
    _find_next_range,
    _format_duration,
    _refresh_unchanged_day,
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


class TestRefreshUnchangedDay:
    """A re-publication that moved no range can still move the stamp or a cell."""

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
        _refresh_unchanged_day(state, {
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
        _refresh_unchanged_day(state, {
            "date": self.DATE,
            "timestamp": "08:05",
            "updated_at": "11.04.2026 08:05",
        })
        # update_count tracks announced changes, and nothing was announced
        assert state[self.DATE]["update_count"] == 3

    def test_identical_stamp_writes_nothing(self, saved):
        state = self._state()
        _refresh_unchanged_day(state, {
            "date": self.DATE,
            "timestamp": "22:18",
            "updated_at": "10.04.2026 22:18",
        })
        assert saved == []

    def test_screenshot_without_a_stamp_never_clobbers_the_site_one(self, saved):
        state = self._state()
        _refresh_unchanged_day(state, {
            "date": self.DATE,
            "timestamp": "09:41",
            "source": "telegram",
        })
        assert state[self.DATE]["updated_at"] == "10.04.2026 22:18"
        assert state[self.DATE]["source"] == "poe.pl.ua"
        assert saved == []

    def test_unknown_date_is_ignored(self, saved):
        state = self._state()
        _refresh_unchanged_day(state, {"date": "01.01.2027", "updated_at": "01.01.2027 10:00"})
        assert saved == []

    def test_repainted_cells_are_written_on_their_own(self, saved):
        # A switching half-hour can move inside a block without moving the
        # range it belongs to — same text, different picture
        state = self._state()
        state[self.DATE]["cells"] = {"1.1": "23" + "1" * 46}
        _refresh_unchanged_day(state, {
            "date": self.DATE,
            "updated_at": "10.04.2026 22:18",      # unchanged
            "cells": {"1.1": "32" + "1" * 46},
        })
        assert state[self.DATE]["cells"] == {"1.1": "32" + "1" * 46}
        assert len(saved) == 1

    def test_a_day_recorded_without_cells_is_backfilled(self, saved):
        state = self._state()
        assert "cells" not in state[self.DATE]
        _refresh_unchanged_day(state, {"date": self.DATE, "cells": {"1.1": "2" * 48}})
        assert state[self.DATE]["cells"] == {"1.1": "2" * 48}

    def test_a_screenshot_never_erases_the_painted_cells(self, saved):
        state = self._state()
        state[self.DATE]["cells"] = {"1.1": "23" + "1" * 46}
        _refresh_unchanged_day(state, {"date": self.DATE, "source": "telegram"})
        assert state[self.DATE]["cells"] == {"1.1": "23" + "1" * 46}
        assert saved == []


def delivered(messages, photos):
    """Everything a subscriber actually read: captions count as text."""
    return [t for _, t in messages] + [c for _, _, c in photos if c]


@pytest.fixture
def photos(monkeypatch):
    """Stub the photo upload; without it these tests hit the real Bot API.

    Also empties the picture cache, which lives for the life of the process and
    would otherwise carry a drawing from one test into the next.
    """
    main._pictures.clear()
    sent = []

    async def fake_photo(token, chat_id, photo, caption=None):
        sent.append((chat_id, photo, caption))
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

    def test_day_with_outages_still_gets_the_grid(self, sent, photos):
        asyncio.run(main._send_day_schedule(42, "14.09.2026", self.BUSY, "завтра"))
        # short enough to ride along as the picture's caption
        assert sent == []
        assert "08:00–10:00" in photos[0][2]


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

    def test_later_grid_reads_as_a_first_publication(self, pipeline, photos):
        asyncio.run(main.process_parsed(self._day(self.EMPTY, "14.09.2026 09:00")))
        asyncio.run(main.process_parsed(self._day(self.GRID, "14.09.2026 20:04")))
        text = delivered(pipeline, photos)[0]
        assert "Графік відключень" in text
        assert "Оновлення графіку" not in text
        assert "з'явились відключення" not in text

    def test_it_reaches_subscribers_of_untouched_queues_too(self, pipeline, photos):
        asyncio.run(main.process_parsed(self._day(self.EMPTY, "14.09.2026 09:00")))
        asyncio.run(main.process_parsed(self._day(self.GRID, "14.09.2026 20:04")))
        # 4.2 has no outages in this grid, but a first publication goes to all
        assert sorted(chat_id for chat_id, _, _ in photos) == [1, 2]

    def test_a_later_correction_is_still_an_update(self, pipeline, photos):
        asyncio.run(main.process_parsed(self._day(self.EMPTY, "14.09.2026 09:00")))
        asyncio.run(main.process_parsed(self._day(self.GRID, "14.09.2026 20:04")))
        pipeline.clear()
        photos.clear()
        longer = {**self.GRID, "1.1": [{"start": "08:00", "end": "11:00"}]}
        asyncio.run(main.process_parsed(self._day(longer, "14.09.2026 22:30")))
        assert "Оновлення графіку" in delivered(pipeline, photos)[0]


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


class TestSetSubscriberQueue:
    """Picking a queue is how most people subscribe, so it has to say so."""

    @pytest.fixture
    def path(self, tmp_path):
        return str(tmp_path / "subs.json")

    def test_an_unknown_chat_joins(self, path):
        from subscribers import load_subscribers, set_subscriber_queue
        assert set_subscriber_queue(42, "3.2", path) is True
        assert load_subscribers(path) == {42: "3.2"}

    def test_an_existing_subscriber_only_changes_queue(self, path):
        from subscribers import add_subscriber, set_subscriber_queue
        add_subscriber(42, path)
        assert set_subscriber_queue(42, "3.2", path) is False

    def test_switching_queues_is_not_joining_twice(self, path):
        from subscribers import set_subscriber_queue
        set_subscriber_queue(42, "3.2", path)
        assert set_subscriber_queue(42, "1.1", path) is False


class TestDescribeUser:
    """Every field of Telegram's `from` object is optional."""

    def test_name_and_username(self):
        assert main._describe_user(
            {"first_name": "Іван", "last_name": "Коваль", "username": "ivan"}
        ) == "Іван Коваль (@ivan)"

    def test_name_without_a_username(self):
        assert main._describe_user({"first_name": "Іван"}) == "Іван"

    def test_username_without_a_name(self):
        assert main._describe_user({"username": "ivan"}) == "@ivan"

    def test_nothing_known_says_nothing(self):
        assert main._describe_user({}) == ""
        assert main._describe_user(None) == ""


class TestAnnounceMembership:
    """The admin hears about everyone who joins or leaves — except themselves."""

    ADMIN = 777
    WHO = {"first_name": "Іван", "username": "ivan"}

    @pytest.fixture
    def admin(self, monkeypatch, tmp_path):
        sent = []

        async def fake_send(token, chat_id, text):
            sent.append((chat_id, text))

        monkeypatch.setattr(main, "USER_CHAT_ID", self.ADMIN)
        monkeypatch.setattr(main, "send_message", fake_send)
        monkeypatch.setattr(main, "SUBSCRIBERS_FILE_PATH", str(tmp_path / "subs.json"))
        return sent

    def test_tells_the_admin_who_joined_and_how_many_there_are(self, admin):
        from subscribers import add_subscriber
        for chat_id in (11, 22):
            add_subscriber(chat_id, main.SUBSCRIBERS_FILE_PATH)

        asyncio.run(main._announce_membership(22, "3.2", self.WHO, joined=True))

        chat_id, text = admin[0]
        assert chat_id == self.ADMIN
        assert text.startswith("🎉 Новий підписник")
        assert "Іван (@ivan) · id 22" in text
        assert "Черга: 3.2" in text
        assert "Всього: 2" in text

    def test_a_departure_is_announced_too(self, admin):
        # A count that only ever rises says nothing about a bad broadcast
        from subscribers import add_subscriber
        add_subscriber(11, main.SUBSCRIBERS_FILE_PATH)

        asyncio.run(main._announce_membership(22, "3.2", self.WHO, joined=False))

        text = admin[0][1]
        assert text.startswith("👋 Відписався")
        assert "Черга: 3.2" in text      # the queue they had, not one they chose
        assert "Всього: 1" in text       # already counted without them

    def test_no_queue_reads_as_all_of_them(self, admin):
        asyncio.run(main._announce_membership(22, None, self.WHO, joined=True))
        assert "Черга: всі" in admin[0][1]

    def test_the_admin_coming_or_going_is_not_news(self, admin):
        asyncio.run(main._announce_membership(self.ADMIN, None, self.WHO, joined=True))
        asyncio.run(main._announce_membership(self.ADMIN, None, self.WHO, joined=False))
        assert admin == []

    def test_a_failed_notice_is_swallowed(self, admin, monkeypatch):
        # The subscriber is already registered and answered by this point —
        # the admin losing a notice must not turn into an error for them
        async def boom(*a, **k):
            raise RuntimeError("Telegram is down")

        monkeypatch.setattr(main, "send_message", boom)
        asyncio.run(main._announce_membership(22, "3.2", self.WHO, joined=True))


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
        monkeypatch.setattr(sender, "send_message", fake_send)   # cancellations broadcast
        monkeypatch.setattr(sender, "BROADCAST_DELAY", 0)
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
        assert "Графік відключень" in photos[0][2]

    def test_short_schedule_rides_as_a_caption(self, pipeline, photos):
        # one Bot API call per subscriber instead of two
        asyncio.run(main.process_parsed(self._day(self.GRID)))
        assert pipeline == []
        assert photos[0][2].startswith("⚡ Графік відключень")

    def test_a_schedule_too_long_to_caption_follows_as_text(self, pipeline, photos, monkeypatch):
        monkeypatch.setattr(main, "CAPTION_LIMIT", 10)
        asyncio.run(main.process_parsed(self._day(self.GRID)))
        assert photos[0][2] is None
        assert "Графік відключень" in pipeline[0][1]

    def test_cancellation_carries_no_picture(self, pipeline, photos):
        asyncio.run(main.process_parsed(self._day(self.GRID)))
        photos.clear()
        asyncio.run(main.process_parsed(self._day(self.EMPTY)))
        assert photos == []

    def test_painted_cells_survive_into_the_stored_day(self, pipeline, photos):
        # A picture drawn later must put the switching half-hours where the
        # provider put them, so its cells have to outlive the broadcast
        painted = {f"{q}.{s}": "1" * 48 for q in range(1, 7) for s in (1, 2)}
        painted["1.1"] = "1" * 16 + "2223" + "1" * 28
        asyncio.run(main.process_parsed({**self._day(self.GRID), "cells": painted}))

        entry = main.load_state(main.STATE_FILE_PATH)[self.DATE]
        assert entry["cells"] == painted

        # Asked for the stored day, the cache recognises the same drawing and
        # names the file id Telegram already gave it — the cells came back whole
        assert photos[0][1].startswith(b"\x89PNG")
        photos.clear()
        asyncio.run(main._send_day_schedule(1, self.DATE, entry, "сьогодні"))
        assert photos[0][1] == "file-id-1"

    def test_a_failed_drawing_does_not_stop_the_message(self, pipeline, photos, monkeypatch):
        monkeypatch.setattr(main, "render_png", lambda *a, **k: None)
        asyncio.run(main.process_parsed(self._day(self.GRID)))
        assert photos == []
        assert "Графік відключень" in pipeline[0][1]


class TestGridPictureStamp:
    """The picture's stamp must agree with what the caption already says.

    A schedule recognised from a screenshot carries no provider stamp, only a
    bare clock time — the watermark's, or the moment it was processed. The
    caption (formatter.format_stamp) already falls back to it; the picture
    used to stop one level earlier and draw a bare date instead.
    """

    EMPTY = {f"{q}.{s}": [] for q in range(1, 7) for s in (1, 2)}
    SCHEDULE = {**EMPTY, "1.1": [{"start": "08:00", "end": "10:00"}]}

    @pytest.fixture
    def stamps(self, monkeypatch, photos):
        seen = []

        def capturing_render(schedule, stamp, intro=None, cells=None):
            seen.append(stamp)
            return b"\x89PNG-fake"

        monkeypatch.setattr(main, "render_png", capturing_render)
        return seen

    def test_provider_stamp_is_used_as_before(self, stamps):
        main._grid_picture({"date": "08.10.2026", "updated_at": "08.10.2026 15:02",
                            "schedule": self.SCHEDULE})
        assert stamps[0] == "8 жовтня 2026 15:02"

    def test_a_screenshot_without_a_provider_stamp_still_shows_the_time(self, stamps):
        main._grid_picture({"date": "08.10.2026", "updated_at": None,
                            "timestamp": "15:02", "source": "telegram",
                            "schedule": self.SCHEDULE})
        assert stamps[0] == "8 жовтня 2026 15:02"

    def test_nothing_known_at_all_falls_back_to_the_bare_date(self, stamps):
        main._grid_picture({"date": "08.10.2026", "updated_at": None,
                            "timestamp": None, "schedule": self.SCHEDULE})
        assert stamps[0] == "8 жовтня 2026"


class TestPictureCache:
    """The same drawing is neither rendered twice nor uploaded twice."""

    DAY = {"date": "15.09.2026", "updated_at": "14.09.2026 20:04", "timestamp": "20:04",
           "source": "poe.pl.ua", "intro": [],
           "schedule": {**{f"{q}.{s}": [] for q in range(1, 7) for s in (1, 2)},
                        "1.1": [{"start": "08:00", "end": "10:00"}]}}

    @pytest.fixture
    def renders(self, monkeypatch, photos):
        calls = []

        def counting_render(schedule, stamp, intro=None, cells=None):
            calls.append(stamp)
            return b"\x89PNG-fake"

        async def fake_send(token, chat_id, text):
            return True

        monkeypatch.setattr(main, "render_png", counting_render)
        monkeypatch.setattr(main, "send_message", fake_send)   # used when a photo fails
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

    def test_repainted_cells_alone_redraw(self, renders):
        # Same ranges, a switching half-hour moved: a different picture
        main._grid_picture({**self.DAY, "cells": {"1.1": "1" * 16 + "2223" + "1" * 28}})
        main._grid_picture({**self.DAY, "cells": {"1.1": "1" * 16 + "3222" + "1" * 28}})
        assert len(renders) == 2

    def test_a_new_stamp_alone_redraws(self, renders):
        # The picture prints the stamp, so a restamped day is a different picture
        main._grid_picture(self.DAY)
        main._grid_picture({**self.DAY, "updated_at": "14.09.2026 22:30"})
        assert len(renders) == 2

    def test_bytes_go_up_once_then_the_file_id_is_reused(self, renders, photos):
        picture = main._grid_picture(self.DAY)
        for chat_id in (1, 2, 3):
            asyncio.run(main._send_schedule(chat_id, picture, "текст"))
        assert photos[0][1] == b"\x89PNG-fake"      # first subscriber uploads
        assert photos[1][1] == "file-id-1"          # the rest just name it
        assert photos[2][1] == "file-id-1"

    def test_a_failed_upload_does_not_poison_the_cache(self, renders, monkeypatch):
        async def failing(token, chat_id, photo, caption=None):
            return None
        monkeypatch.setattr(main, "send_photo", failing)
        picture = main._grid_picture(self.DAY)
        asyncio.run(main._send_schedule(1, picture, "текст"))
        assert picture["file_id"] is None   # next send retries the upload

    def test_cache_does_not_grow_without_bound(self, renders):
        for i in range(main.PICTURE_CACHE_SIZE + 3):
            main._grid_picture({**self.DAY, "updated_at": f"14.09.2026 20:{i:02d}"})
        assert len(main._pictures) == main.PICTURE_CACHE_SIZE

    def test_the_day_in_use_survives_a_run_of_amendments(self, renders):
        # FIFO eviction would drop today's grid while tomorrow is being amended
        today = main._grid_picture(self.DAY)
        for i in range(main.PICTURE_CACHE_SIZE + 2):
            main._grid_picture({**self.DAY, "updated_at": f"14.09.2026 21:{i:02d}"})
            main._grid_picture(self.DAY)          # still being served to users
        assert main._grid_picture(self.DAY) is today


class TestBroadcastOverlap:
    """Two schedules must never run the pipeline at the same time."""

    def test_a_second_schedule_waits_its_turn(self, monkeypatch):
        order = []

        async def instrumented(parsed):
            order.append(f"start {parsed['tag']}")
            for _ in range(3):
                await asyncio.sleep(0)      # plenty of chances to interleave
            order.append(f"end {parsed['tag']}")
            return True

        monkeypatch.setattr(main, "_process_parsed", instrumented)

        async def run():
            await asyncio.gather(main.process_parsed({"tag": "a"}),
                                 main.process_parsed({"tag": "b"}))

        asyncio.run(run())
        assert order in (["start a", "end a", "start b", "end b"],
                         ["start b", "end b", "start a", "end a"])

    def test_the_poller_skips_a_tick_instead_of_queueing_stale_data(self, monkeypatch):
        fetched = []

        async def fake_fetch(*args, **kwargs):
            fetched.append(1)
            return []

        ticks = {"n": 0}

        async def fake_sleep(seconds):
            ticks["n"] += 1
            if ticks["n"] >= 2:
                raise asyncio.CancelledError

        monkeypatch.setattr(main, "fetch_days", fake_fetch)
        monkeypatch.setattr(main.asyncio, "sleep", fake_sleep)

        async def run():
            async with main._broadcast_lock:      # a broadcast is in flight
                with pytest.raises(asyncio.CancelledError):
                    await main.poll_site()

        asyncio.run(run())
        assert fetched == []    # the site was never asked while the lock was held

    def test_the_poller_fetches_when_nothing_is_broadcasting(self, monkeypatch):
        fetched = []

        async def fake_fetch(*args, **kwargs):
            fetched.append(1)
            return []

        async def stop(seconds):
            raise asyncio.CancelledError

        monkeypatch.setattr(main, "fetch_days", fake_fetch)
        monkeypatch.setattr(main.asyncio, "sleep", stop)

        async def run():
            with pytest.raises(asyncio.CancelledError):
                await main.poll_site()

        asyncio.run(run())
        assert fetched == [1]


class TestLogsKeepTheTokenOut:
    def test_httpx_request_logging_is_silenced(self):
        import logging
        # httpx logs "HTTP Request: GET https://api.telegram.org/bot<TOKEN>/..."
        # at INFO, which published the token to the deployment logs
        assert logging.getLogger("httpx").level >= logging.WARNING


class TestUnreachableProvider:
    """A host that cannot reach the provider must not drown its own log."""

    def _run_polls(self, monkeypatch, error, ticks):
        logged = []

        async def failing_fetch(*args, **kwargs):
            raise error

        counter = {"n": 0}

        async def fake_sleep(seconds):
            counter["n"] += 1
            if counter["n"] >= ticks:
                raise asyncio.CancelledError

        monkeypatch.setattr(main, "fetch_days", failing_fetch)
        monkeypatch.setattr(main.asyncio, "sleep", fake_sleep)
        monkeypatch.setattr(main.logger, "warning", lambda msg, *a: logged.append(("warning", msg % a)))
        monkeypatch.setattr(main.logger, "error", lambda msg, *a: logged.append(("error", msg % a)))
        monkeypatch.setattr(main.logger, "exception", lambda msg, *a: logged.append(("exception", msg % a)))

        async def run():
            with pytest.raises(asyncio.CancelledError):
                await main.poll_site()

        asyncio.run(run())
        return logged

    def test_a_connect_timeout_logs_one_line_not_a_traceback(self, monkeypatch):
        logged = self._run_polls(monkeypatch, httpx.ConnectTimeout("timed out"), ticks=1)
        assert [level for level, _ in logged] == ["warning"]
        assert "ConnectTimeout" in logged[0][1]

    def test_it_says_so_once_after_a_run_of_failures(self, monkeypatch):
        logged = self._run_polls(monkeypatch, httpx.ConnectTimeout("timed out"),
                                 ticks=main.UNREACHABLE_ALERT_AFTER + 2)
        errors = [msg for level, msg in logged if level == "error"]
        assert len(errors) == 1            # once, not on every poll
        assert "POE_PROXY" in errors[0]

    def test_an_unexpected_error_still_gets_its_traceback(self, monkeypatch):
        logged = self._run_polls(monkeypatch, ValueError("something new"), ticks=1)
        assert [level for level, _ in logged] == ["exception"]


class TestAdaptivePolling:
    """The data decides how closely to watch; nobody flips a switch."""

    QUIET = [{"schedule": {f"{q}.{s}": [] for q in range(1, 7) for s in (1, 2)}}]
    ACTIVE = [{"schedule": {"1.1": [{"start": "08:00", "end": "10:00"}]}}]

    def _at(self, hour):
        return datetime(2026, 10, 3, hour, 0, tzinfo=main.UKRAINE_TZ)

    def test_quiet_day_is_polled_rarely(self):
        assert main._next_poll_delay(self.QUIET, self._at(10)) == main.POE_QUIET_INTERVAL

    def test_published_grid_brings_back_close_watching(self):
        assert main._next_poll_delay(self.ACTIVE, self._at(10)) == main.POE_POLL_INTERVAL

    def test_evening_stays_fast_even_in_a_quiet_week(self):
        # tomorrow's grid appears here; being late by half an hour is the one
        # thing the slow interval must not cause
        assert main._next_poll_delay(self.QUIET, self._at(20)) == main.POE_POLL_INTERVAL

    def test_window_edges(self):
        assert main._next_poll_delay(self.QUIET, self._at(17)) == main.POE_QUIET_INTERVAL
        assert main._next_poll_delay(self.QUIET, self._at(18)) == main.POE_POLL_INTERVAL
        assert main._next_poll_delay(self.QUIET, self._at(22)) == main.POE_POLL_INTERVAL
        assert main._next_poll_delay(self.QUIET, self._at(23)) == main.POE_QUIET_INTERVAL

    def test_nothing_published_at_all_counts_as_quiet(self):
        assert main._next_poll_delay([], self._at(10)) == main.POE_QUIET_INTERVAL

    def test_one_busy_day_among_quiet_ones_is_enough(self):
        days = self.QUIET + self.ACTIVE
        assert main._next_poll_delay(days, self._at(10)) == main.POE_POLL_INTERVAL
