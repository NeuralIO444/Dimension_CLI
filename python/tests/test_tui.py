# (c) 2026 NeuralIO 444
# Licensed under PolyForm Noncommercial 1.0.0 + commercial.

"""Issue #15: the TUI is a view. --json never opens it."""

import asyncio

from dimension.tui import DimensionApp, _zone_art


def test_zone_art_marks_the_three_bands():
    art = _zone_art("tiktok_video")
    assert "#" in art and "+" in art and "." in art


def test_tui_mounts_the_views():
    app = DimensionApp(preset="tiktok_video")

    async def run():
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app.query_one("#stages")
            assert app.query_one("#zone")
            assert app.query_one("#layers")
            assert app.query_one("#runs")

    asyncio.run(run())
