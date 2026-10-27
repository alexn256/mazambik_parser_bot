import io
import os

import pytest
from PIL import Image

import grid_image
from grid_image import GRID_X, OFF, ON, PITCH_X, PITCH_Y, SW, TBL_L, render, render_png
from poe_source import parse_response

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")

EMPTY = {f"{q}.{s}": [] for q in range(1, 7) for s in (1, 2)}
ONE_OUTAGE = {**EMPTY, "1.1": [{"start": "08:00", "end": "10:00"}]}


def cell_colour(img, row, slot, table_top=5):
    """Colour at the middle of one half-hour cell."""
    grid_y = table_top + grid_image.HDR_HOUR_H + grid_image.HDR_HALF_H
    x = GRID_X + slot * PITCH_X + PITCH_X // 2
    y = grid_y + row * PITCH_Y + PITCH_Y // 2
    return img.convert("RGB").getpixel((x, y))


def table_top_for(intro, live=False, now_box_lines=3):
    """`_table_top`, but with the legend row count computed the same way
    `render` itself does — the legend can wrap, so this isn't always 1."""
    font = grid_image._font("Regular", 11)
    content_w = grid_image.GRID_R - TBL_L
    rows = len(grid_image._legend_rows(grid_image._legend_items(live), font, content_w))
    return grid_image._table_top(intro, live, now_box_lines, rows)


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

    def test_range_ending_at_midnight_stays_off_to_the_last_slot(self):
        # The block continues past midnight, so its switching half-hour falls on
        # the other side of it — the provider paints today's last cell red.
        img = render({**EMPTY, "2.1": [{"start": "23:00", "end": "00:00"}]}, "x")
        assert cell_colour(img, 2, 46) == OFF
        assert cell_colour(img, 2, 47) == OFF


class TestPaintedCells:
    """The provider's own cells beat anything reconstructed from ranges."""

    def test_painted_cells_are_used_verbatim(self):
        painted = {"1.1": "1" * 16 + "2223" + "1" * 28}
        img = render(ONE_OUTAGE, "x", cells=painted)
        assert cell_colour(img, 0, 16) == OFF
        assert cell_colour(img, 0, 18) == OFF    # ranges would have guessed green here
        assert cell_colour(img, 0, 19) == SW

    def test_switching_cell_may_sit_at_the_start_of_a_block(self):
        # 6.2 on the real site: the block opened before midnight, so the first
        # half-hour of today is the switching window, not the last of the range
        painted = {"1.1": "32" + "1" * 46}
        img = render({**EMPTY, "1.1": [{"start": "00:00", "end": "01:00"}]},
                     "x", cells=painted)
        assert cell_colour(img, 0, 0) == SW
        assert cell_colour(img, 0, 1) == OFF

    def test_a_malformed_row_falls_back_to_the_ranges(self):
        img = render(ONE_OUTAGE, "x", cells={"1.1": "123"})
        assert cell_colour(img, 0, 16) == OFF
        assert cell_colour(img, 0, 19) == SW


class TestLayout:
    def test_hour_boundary_is_a_solid_darker_line(self):
        img = render(ONE_OUTAGE, "x").convert("RGB")
        grid_y = 5 + grid_image.HDR_HOUR_H + grid_image.HDR_HALF_H
        # slot 2 starts hour 1 — the boundary before it is the darker hour rule
        x = GRID_X + 2 * PITCH_X
        assert img.getpixel((x, grid_y + 5)) == grid_image.HOUR_BORDER

    def test_half_hour_mark_is_dashed_not_solid(self):
        img = render(ONE_OUTAGE, "x").convert("RGB")
        grid_y = 5 + grid_image.HDR_HOUR_H + grid_image.HDR_HALF_H
        # slot 1 (00:30) is the second half of hour 0 — a dashed divider, not a gap
        x = GRID_X + 1 * PITCH_X
        on_a_dash = img.getpixel((x, grid_y))
        in_a_gap = img.getpixel((x, grid_y + 4))
        assert on_a_dash != ON   # darkened by the dash
        assert in_a_gap == ON    # cells otherwise touch with no visible seam

    def test_a_faint_seam_separates_even_a_queues_own_two_rows(self):
        img = render(ONE_OUTAGE, "x").convert("RGB")
        grid_y = 5 + grid_image.HDR_HOUR_H + grid_image.HDR_HALF_H
        # row 0 (1.1) and row 1 (1.2) are the same queue — faint, not the dark rule
        boundary_y = grid_y + grid_image.CELL_H - 1
        seam = img.getpixel((GRID_X + 5, boundary_y))
        assert seam == grid_image._lighten(ON)
        assert seam != ON
        assert seam != grid_image.LABEL_BORDER

    def test_queue_pairs_are_separated_by_the_darker_line(self):
        img = render(ONE_OUTAGE, "x").convert("RGB")
        grid_y = 5 + grid_image.HDR_HOUR_H + grid_image.HDR_HALF_H
        # row 1 (1.2) and row 2 (2.1) are different queues — a visible divider
        boundary_y = grid_y + 2 * PITCH_Y
        assert img.getpixel((GRID_X + 5, boundary_y)) == grid_image.LABEL_BORDER

    def test_the_whole_table_sits_in_a_thin_frame(self):
        img = render(ONE_OUTAGE, "x").convert("RGB")
        top = 5
        assert img.getpixel((TBL_L - 1, top)) == grid_image.SCROLL_BORDER

    def test_intro_pushes_the_table_down_one_line_at_a_time(self):
        two = render(ONE_OUTAGE, "x", intro=[[("a", False)], [("b", True)]])
        three = render(ONE_OUTAGE, "x", intro=[[("a", False)], [("b", True)], [("c", False)]])
        assert three.height - two.height == grid_image.INTRO_PITCH

    def test_without_an_intro_the_table_starts_at_the_top(self):
        bare = render(ONE_OUTAGE, "x")
        with_intro = render(ONE_OUTAGE, "x", intro=[[("a", False)]])
        assert with_intro.height > bare.height
        assert cell_colour(bare, 0, 16) == OFF


class TestLiveStatus:
    """Live state comes from the site's own response (poe_source's `now`),
    never computed from this machine's clock — see `_parse_now_info`."""

    NOW = {"time": "12:15", "interval": ("12:00", "12:30"),
           "on": ["1.2"], "off": ["1.1"], "switching": [], "line_pct": 50}
    INTRO = [[("a", False)]]

    def test_static_legend_has_three_items(self):
        assert len(grid_image._legend_items(False)) == 3

    def test_live_legend_adds_current_time_and_elapsed(self):
        items = grid_image._legend_items(True)
        assert len(items) == 5
        _, text, swatch_w, bordered = items[3]
        assert text == "Поточний час"
        assert swatch_w == 3  # a bare sliver, not the 16px bordered square
        assert bordered is False

    def test_a_live_picture_is_taller_than_a_static_one(self):
        static = render(ONE_OUTAGE, "x", intro=self.INTRO)
        live = render(ONE_OUTAGE, "x", intro=self.INTRO, now=self.NOW)
        assert live.height > static.height

    def test_current_time_line_sits_at_the_sites_own_percentage(self):
        # 12:00 is slot 24; line_pct=50 puts the line at its exact midpoint
        img = render(ONE_OUTAGE, "x", intro=self.INTRO, now=self.NOW).convert("RGB")
        top = table_top_for(self.INTRO, True, 3)
        grid_y = top + grid_image.HDR_HOUR_H + grid_image.HDR_HALF_H
        line_x = round(GRID_X + 24.5 * PITCH_X)
        assert img.getpixel((line_x, grid_y + 5)) == grid_image.BLUE

    def test_no_now_means_no_line_and_the_static_height(self):
        img = render(ONE_OUTAGE, "x", intro=self.INTRO).convert("RGB")
        top = table_top_for(self.INTRO, False)
        grid_y = top + grid_image.HDR_HOUR_H + grid_image.HDR_HALF_H
        line_x = round(GRID_X + 24.5 * PITCH_X)
        assert img.getpixel((line_x, grid_y + 5)) != grid_image.BLUE

    def test_periods_do_not_change_the_layout(self):
        # Only the periods-list text reads `periods` — the grid still comes
        # from `schedule`/`cells`, so passing it changes nothing geometric
        periods = {"1.1": {"ranges": [{"start": "08:00", "end": "09:00"}], "active": True}}
        plain = render(ONE_OUTAGE, "x")
        with_periods = render(ONE_OUTAGE, "x", periods=periods)
        assert plain.size == with_periods.size


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
        png = render_png(day["schedule"], day["updated_at"], day["intro"],
                         day["cells"])
        img = Image.open(io.BytesIO(png))
        top = table_top_for(day["intro"])
        # 1.1 is off 00:00–02:30, so slot 0 is red and slot 4 is the switching cell
        assert cell_colour(img, 0, 0, top) == OFF
        assert cell_colour(img, 0, 4, top) == SW

    def test_the_real_grid_matches_the_provider_cell_for_cell(self):
        with open(os.path.join(FIXTURES, "gpv_outage_day.html"), encoding="utf-8") as f:
            day = parse_response(f.read())[0]
        img = render(day["schedule"], day["updated_at"], day["intro"], day["cells"])
        top = table_top_for(day["intro"])
        for row in range(12):
            label = f"{row // 2 + 1}.{row % 2 + 1}"
            for slot, painted in enumerate(day["cells"][label]):
                assert cell_colour(img, row, slot, top) == \
                    grid_image.PROVIDER_COLOURS[painted], f"{label} slot {slot}"
