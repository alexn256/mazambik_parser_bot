import pytest
from datetime import datetime, timezone, timedelta
from monitor import _parse_caption_date, DATE_RE
from parser import DATE_ONLY_RE

UKRAINE_TZ = timezone(timedelta(hours=3))
MSG_DATE = datetime(2026, 4, 8, 10, 0, 0, tzinfo=timezone.utc)


class TestParseCaptionDate:
    def test_standard_caption(self):
        assert _parse_caption_date("Графік на 8 квітня", MSG_DATE) == "08.04.2026"

    def test_zmin_caption(self):
        # Real caption from channel: "⚡️Зміни у графіку на 8 квітня"
        assert _parse_caption_date("⚡️Зміни у графіку на 8 квітня", MSG_DATE) == "08.04.2026"

    def test_tomorrow_caption(self):
        assert _parse_caption_date("Графік на 9 квітня", MSG_DATE) == "09.04.2026"

    def test_no_date_in_caption(self):
        assert _parse_caption_date("Просто текст без дати", MSG_DATE) is None

    def test_different_month(self):
        assert _parse_caption_date("Графік на 15 березня", MSG_DATE) == "15.03.2026"

    def test_case_insensitive(self):
        assert _parse_caption_date("ГРАФІК НА 8 КВІТНЯ", MSG_DATE) == "08.04.2026"


class TestCaptionFilter:
    """Test the 'графік' substring filter used in the channel handler."""

    def test_grafik_in_standard(self):
        assert "графік" in "Графік на 8 квітня".lower()

    def test_grafik_in_zminy(self):
        # "графіку" contains "графік"
        assert "графік" in "⚡️Зміни у графіку на 8 квітня".lower()

    def test_no_grafik(self):
        assert "графік" not in "Просто фото".lower()

    def test_grafik_uppercase(self):
        assert "графік" in "ГРАФІК НА 9 КВІТНЯ".lower()


class TestDateOnlyRegex:
    """Test DATE_ONLY_RE for watermarks without a timestamp."""

    def test_standard_date(self):
        m = DATE_ONLY_RE.search("09.04.2026")
        assert m is not None
        assert m.group(1) == "09"
        assert m.group(2) == "04"
        assert m.group(3) == "2026"

    def test_date_in_text(self):
        m = DATE_ONLY_RE.search("МОЗАМБІК.МЕДІА\n09.04.2026")
        assert m is not None
        assert m.group(1) == "09"

    def test_no_match(self):
        assert DATE_ONLY_RE.search("без дати тут") is None

    def test_single_digit_day(self):
        m = DATE_ONLY_RE.search("8.04.2026")
        assert m is not None
        assert m.group(1) == "8"
