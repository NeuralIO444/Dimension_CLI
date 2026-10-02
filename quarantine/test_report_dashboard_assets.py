# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_report_dashboard_assets.py — report-dashboard-v1 Item 5.

Two static guarantees for the in-panel Report Dashboard page:

1. Offline gate — cep/report_dashboard.html must never reference a CDN,
   Google Fonts, or any remote <script src="http...">. CEP panels have to
   work fully offline; this is the regression test for the Tailwind-CDN/
   Google-Fonts mock export this feature replaced.
2. Asset-existence gate — every local stylesheet/script the page
   references (relative href/src, no scheme) must actually exist on disk
   next to it. A typo'd relative path here fails silently in a real
   panel (blank/unstyled dashboard) with nothing to catch it otherwise.

cep/js/report_dashboard.js's syntax is already covered by the existing
node --check gate (test_cep_js_syntax.py) — it lives under cep/js/*.js.
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_HTML_PATH = _REPO / "cep" / "report_dashboard.html"

_FORBIDDEN_SUBSTRINGS = ("cdn.", "googleapis", "fonts.google")
_REMOTE_SCRIPT_SRC_RE = re.compile(r'<script[^>]+src=["\']https?://', re.IGNORECASE)
_LOCAL_ASSET_RE = re.compile(
    r'(?:href|src)=["\']([^"\':]+?\.(?:css|js))["\']',
    re.IGNORECASE,
)


def _read_html() -> str:
    assert _HTML_PATH.is_file(), f"missing {_HTML_PATH}"
    return _HTML_PATH.read_text(encoding="utf-8")


class TestOfflineGate:
    def test_no_cdn_or_google_fonts_references(self):
        html = _read_html().lower()
        for needle in _FORBIDDEN_SUBSTRINGS:
            assert needle not in html, (
                f"cep/report_dashboard.html references '{needle}' — CEP panels "
                "must work offline; no CDN/webfont dependency is allowed."
            )

    def test_no_remote_script_tags(self):
        html = _read_html()
        assert not _REMOTE_SCRIPT_SRC_RE.search(html), (
            'cep/report_dashboard.html has a <script src="http(s)://...">  '
            "reference — every script must be a local, bundled file."
        )


class TestLocalAssetsExist:
    def test_referenced_css_and_js_assets_exist(self):
        html = _read_html()
        matches = _LOCAL_ASSET_RE.findall(html)
        assert matches, "expected at least one local css/js asset reference"
        for rel in matches:
            resolved = (_HTML_PATH.parent / rel).resolve()
            assert resolved.is_file(), (
                f"cep/report_dashboard.html references '{rel}' but "
                f"{resolved} does not exist on disk"
            )

    def test_report_dashboard_js_lives_under_cep_js(self):
        """report_dashboard.js must live under cep/js/ so the existing
        node --check gate (test_cep_js_syntax.py's cep/js/*.js glob) covers
        it — a syntax error here would otherwise kill the dashboard's
        entire iframe silently, uncaught by any test."""
        html = _read_html()
        assert 'src="js/report_dashboard.js"' in html
        assert (_REPO / "cep" / "js" / "report_dashboard.js").is_file()
        assert not (_REPO / "cep" / "report_dashboard.js").is_file(), (
            "stale pre-move copy at cep/report_dashboard.js should not exist "
            "alongside the cep/js/ version"
        )
