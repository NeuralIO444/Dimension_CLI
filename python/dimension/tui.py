# (c) 2026 NeuralIO 444
# Licensed under PolyForm Noncommercial 1.0.0 + commercial.
# See LICENSE for full terms.

"""Textual human face. A view over the CLI, not a second engine.

`--json` never reaches this module. The machine face stays in cli.py.
"""

from __future__ import annotations

import json
from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import DataTable, Footer, Header, Input, Static

STAGES = ("scrape", "tag", "conform", "SOE", "inject", "audit")


def _zone_art(preset: str) -> str:
    """Block view of the 1000x700 canvas. GO center, NUDGE ring, CUTOFF edge."""
    rows = []
    for y in range(12):
        line = []
        for x in range(24):
            edge = x < 2 or x > 21 or y < 1 or y > 10
            ring = x < 4 or x > 19 or y < 3 or y > 8
            line.append("#" if edge else "+" if ring else ".")
        rows.append("".join(line))
    return f"{preset}  1000x700\n" + "\n".join(rows) + "\n# cutoff  + nudge  . go"


def _runs(project: Path) -> list[tuple[str, str, str]]:
    try:
        from core.dimension_db import list_runs, open_project_db
        conn = open_project_db(str(project))
        try:
            rows = list_runs(conn, limit=8)
        finally:
            conn.close()
    except Exception:
        return []
    out = []
    for row in rows:
        detail = row.get("detail") or {}
        out.append((
            str(row.get("status") or ""),
            str(row.get("session") or ""),
            str(detail.get("chunk_manifest") or ""),
        ))
    return out


def _layers(manifest: Path) -> list[tuple[str, str, str]]:
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    layers = payload.get("layers") or []
    rows = []
    for layer in layers[:12]:
        if not isinstance(layer, dict):
            continue
        rows.append((
            str(layer.get("name") or ""),
            str(layer.get("tag") or layer.get("role") or "untagged"),
            str(layer.get("index") or ""),
        ))
    return rows


class DimensionApp(App):
    """Keyboard-first dashboard. Ctrl+K jumps. q quits."""

    CSS = """
    Screen { background: #12141a; }
    #stages { height: 3; color: #9be7ff; }
    #zone { width: 40; color: #d7ffb3; }
    DataTable { height: 1fr; }
    #palette { dock: bottom; display: none; }
    """
    BINDINGS = [
        Binding("q", "quit", "quit"),
        Binding("ctrl+k", "palette", "commands"),
        Binding("question_mark", "help", "help"),
    ]

    def __init__(self, source: str = "", preset: str = "tiktok_video") -> None:
        super().__init__()
        self.source = source
        self.preset = preset or "tiktok_video"

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static("  ".join(f"[{s}]" for s in STAGES), id="stages")
        with Horizontal():
            yield Static(_zone_art(self.preset), id="zone")
            with Vertical():
                yield DataTable(id="layers")
                yield DataTable(id="runs")
        yield Input(placeholder="command: safe-zone, catalog, provenance, conform", id="palette")
        yield Footer()

    def on_mount(self) -> None:
        self.title = "dimension"
        self.sub_title = self.preset
        layers = self.query_one("#layers", DataTable)
        layers.add_columns("layer", "tag", "index")
        path = Path(self.source) if self.source else None
        for row in (_layers(path) if path and path.is_file() else []):
            layers.add_row(*row)
        runs = self.query_one("#runs", DataTable)
        runs.add_columns("status", "session", "chunk")
        project = path.parent if path else Path.cwd()
        found = _runs(project)
        if not found:
            runs.add_row("none", "", "no dimension.db runs yet")
        for row in found:
            runs.add_row(*row)

    def action_palette(self) -> None:
        box = self.query_one("#palette", Input)
        box.display = not box.display
        if box.display:
            box.focus()

    def action_help(self) -> None:
        self.notify("q quit   ctrl+k commands   --json never opens this screen")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.notify(f"run from the shell: dimension {event.value}")
        event.input.display = False


def launch(source: str = "", preset: str = "tiktok_video") -> None:
    DimensionApp(source=source, preset=preset).run()
