import io

import pytest
from PIL import Image

import cards_image
from cards_image import render, render_png

EMPTY = {f"{q}.{s}": [] for q in range(1, 7) for s in (1, 2)}
ONE_RANGE = {**EMPTY, "1.1": [{"start": "08:00", "end": "10:00"}]}
TWO_RANGES = {**EMPTY, "1.1": [{"start": "08:00", "end": "10:00"},
                               {"start": "14:00", "end": "16:00"}]}


class TestLayout:
    def test_a_taller_card_in_a_row_sets_that_rows_height(self):
        # 1.1 and 1.2 share row 0 — more ranges in one should push both down
        more = {**EMPTY,
                "1.1": [{"start": "08:00", "end": "10:00"}],
                "1.2": [{"start": "08:00", "end": "10:00"},
                        {"start": "14:00", "end": "16:00"},
                        {"start": "20:00", "end": "22:00"}]}
        one_line = render(ONE_RANGE, "08.11.2026")
        three_lines = render(more, "08.11.2026")
        assert three_lines.height > one_line.height

    def test_more_ranges_for_one_queue_grow_the_picture(self):
        one = render(ONE_RANGE, "08.11.2026")
        two = render(TWO_RANGES, "08.11.2026")
        assert two.height > one.height

    def test_width_is_fixed(self):
        assert render(ONE_RANGE, "08.11.2026").width == cards_image.WIDTH

    def test_header_bar_is_orange_the_full_width(self):
        img = render(ONE_RANGE, "08.11.2026").convert("RGB")
        assert img.getpixel((0, 10)) == cards_image.ORANGE
        assert img.getpixel((cards_image.WIDTH - 1, 10)) == cards_image.ORANGE

    def test_below_the_header_is_plain_white(self):
        img = render(ONE_RANGE, "08.11.2026").convert("RGB")
        assert img.getpixel((5, cards_image.HEADER_H + 5)) == cards_image.WHITE


class TestCardHeight:
    def test_more_lines_need_more_height(self):
        assert cards_image._card_height(5) > cards_image._card_height(1)

    def test_an_empty_queue_still_gets_one_lines_worth_of_room(self):
        assert cards_image._card_height(0) == cards_image._card_height(1)


class TestRenderPng:
    def test_returns_a_png(self):
        png = render_png(ONE_RANGE, "08.11.2026")
        assert png.startswith(b"\x89PNG\r\n")
        assert Image.open(io.BytesIO(png)).format == "PNG"

    def test_missing_fonts_do_not_raise(self, monkeypatch):
        monkeypatch.setattr(cards_image, "FONT_DIRS", ("/nonexistent",))
        cards_image._font.cache_clear()
        assert render_png(ONE_RANGE, "08.11.2026") is None
        cards_image._font.cache_clear()

    def test_an_entirely_empty_schedule_still_renders(self):
        # Every queue "Без відключень" — the caller decides whether this is
        # worth sending at all; the drawing itself must not choke on it
        png = render_png(EMPTY, "08.11.2026")
        assert png is not None
