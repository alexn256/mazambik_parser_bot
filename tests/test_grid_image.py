import io
import os

import pytest
from PIL import Image

import grid_image
from grid_image import GRID_X, OFF, ON, PITCH_X, PITCH_Y, SW, render, render_png
from poe_source import parse_response

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")

EMPTY = {f"{q}.{s}": [] for q in range(1, 7) for s in (1, 2)}
ONE_OUTAGE = {**EMPTY, "1.1": [{"start": "08:00", "end": "10:00"}]}


def cell_colour(img, row, slot, table_top=5):
    """Colour at the middle of one half-hour cell."""
    grid_y = table_top + 114
    x = GRID_X + slot * PITCH_X + PITCH_X // 2
    y = grid_y + row * PITCH_Y + PITCH_Y // 2
    return img.convert("RGB").getpixel((x, y))


class TestCellColours:
    def test_outage_body_is_red(self):
        img = render(ONE_OUTAGE, "10.04.2026 22:18")
        assert cell_colour(img, 0, 16) == OFF   # 08:00

    def test_last_half_hour_is_the_switching_colour(self):
        img = render(ONE_OUTAGE, "10.04.2026 22:18")
        assert cell_colour(img, 0, 19) == SW    # 09:30, power comes back inside it

    def test_everything_else_is_green(self):
        img = render(ONE_OUTAGE, "10.04.2026 22:18")
        assert cell_colour(img, 0, 0) == ON
        assert cell_colour(img, 0, 20) == ON
        assert cell_colour(img, 5, 16) == ON     # another queue, same hour

    def test_range_ending_at_midnight_fills_the_last_slot(self):
        img = render({**EMPTY, "2.1": [{"start": "23:00", "end": "00:00"}]}, "x")
        assert cell_colour(img, 2, 46) == OFF
        assert cell_colour(img, 2, 47) == SW


class TestLayout:
    def test_every_cell_is_separated_by_a_rule(self):
        img = render(ONE_OUTAGE, "x").convert("RGB")
        grid_y = 5 + 114
        # the pixel column just before a cell start belongs to the rule, not a cell
        assert img.getpixel((GRID_X + PITCH_X - 1, grid_y + 5)) == grid_image.RULE
        assert img.getpixel((GRID_X + 5, grid_y + PITCH_Y - 1)) == grid_image.RULE

    def test_intro_pushes_the_table_down_one_line_at_a_time(self):
        two = render(ONE_OUTAGE, "x", intro=[[("a", False)], [("b", True)]])
        three = render(ONE_OUTAGE, "x", intro=[[("a", False)], [("b", True)], [("c", False)]])
        assert three.height - two.height == grid_image.INTRO_PITCH

    def test_without_an_intro_the_table_starts_at_the_top(self):
        bare = render(ONE_OUTAGE, "x")
        with_intro = render(ONE_OUTAGE, "x", intro=[[("a", False)]])
        assert with_intro.height > bare.height
        assert cell_colour(bare, 0, 16) == OFF


class TestRenderPng:
    def test_returns_a_png(self):
        png = render_png(ONE_OUTAGE, "10.04.2026 22:18")
        assert png.startswith(b"\x89PNG\r\n")
        assert Image.open(io.BytesIO(png)).format == "PNG"

    def test_missing_fonts_do_not_raise(self, monkeypatch):
        # A schedule is worth sending as text even when the picture cannot be drawn
        monkeypatch.setattr(grid_image, "FONT_DIRS", ("/nonexistent",))
        grid_image._font.cache_clear()
        assert render_png(ONE_OUTAGE, "x") is None
        grid_image._font.cache_clear()

    def test_real_schedule_renders(self):
        with open(os.path.join(FIXTURES, "gpv_outage_day.html"), encoding="utf-8") as f:
            day = parse_response(f.read())[0]
        png = render_png(day["schedule"], day["updated_at"], day["intro"])
        img = Image.open(io.BytesIO(png))
        top = grid_image._table_top(day["intro"])
        # 1.1 is off 00:00–02:30, so slot 0 is red and slot 4 is the switching cell
        assert cell_colour(img, 0, 0, top) == OFF
        assert cell_colour(img, 0, 4, top) == SW
