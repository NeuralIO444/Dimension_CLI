# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/logic/preferences_state.py
Lightweight persistent prefs for the v5.2.5 SOE feature flag.

Reuses the existing state file at
    ~/Library/Application Support/NeuralIO_Dimension/state.json
that the Studio Profile registry already writes (`active_studio_profile`).
This module reads + writes the same file with additional keys, so we
don't fragment user config into multiple stores.

Public surface:
    preferences      — module-level singleton
    preferences.enable_soe (bool, default True)
    preferences.save()       — atomic write
    preferences.reload()     — re-read from disk
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict

from logic.user_support_dir import state_json_path


class _Preferences:
    """Tiny attribute-style wrapper over the JSON state dict.

    We deliberately keep this dataclass-light — Pydantic would be
    overkill for the two-or-three boolean flags this needs to hold.
    """

    # Defaults for every persisted field. Adding a new pref means
    # one entry here — the load path falls back to the default for
    # any key the on-disk file doesn't yet carry.
    _DEFAULTS: Dict[str, Any] = {
        "enable_soe": True,         # v5.2.5 — Spatial Occlusion Engine
        # `global_padding_px` retired in v5.5.4 — slider was a UI ghost.
        "default_profile": "default",  # v6 CEP — preselected on fresh scan
        "onboarding_complete": False,  # v6 CEP (#7) — wizard one-shot gate

        # Working directory (2026-08-23). Where the panel and engine
        # read/write scrape_manifest.json, chunks, logs, and reports.
        #
        # "auto"   — <dir of the active .aep>/Dimension/. Keeps a
        #            client project's conform output with that project
        #            instead of pooling every job in one global folder.
        # "custom" — the literal path in `working_dir_custom`.
        #
        # Both modes fall back to the legacy location (DEV_REPO_ROOT in
        # a source checkout, else ~/Documents/Dimension) when they
        # cannot resolve: "auto" with an unsaved project has no
        # directory to derive from, and "custom" pointing at a deleted
        # folder must not strand the panel with nowhere to write.
        # Resolution lives in cep/js/main.js::resolveWorkingDir; these
        # keys are only the persisted intent.
        "working_dir_mode": "auto",
        "working_dir_custom": "",

        # Expression scaling pass — gated off by default pending real-AE
        # verification (2026-07-08). A prior comment here claimed the
        # 2026-07-07 real-AE conform (session FA43A0DC, 87N) had verified
        # "the sealed-unit invariant" for this pass, which was not
        # possible: the pass's sealed-layer check called a method that
        # has never existed on PlacementResolution (`is_sealed`), so
        # every real invocation raised AttributeError and was silently
        # caught (see BUGS.md). Re-enable only after a real-AE conform
        # confirms sealed-layer expressions are actually skipped.
        "expression_scaling_enabled": False,
        # M1 — 30-day Gumroad trial license shell (2026-07-18 spec).
        # PR1 only adds these keys + pure status derivation in
        # cep/js/license.js; no Gumroad network call exists yet (PR2).
        # See docs/roadmap/2026-07-18-trial-license-system-spec.md §4.1
        # for the full field contract this mirrors verbatim.
        "license_key": "",
        "license_status": "none",       # none|trial|full|expired|revoked
        "license_product": "",          # trial|full
        "license_product_id": "",
        "license_email": "",
        "license_activated_at": "",     # ISO-8601 UTC, first success only
        "license_expires_at": "",       # ISO-8601 UTC; empty for full
        "license_last_check_at": "",    # ISO-8601 UTC, last online verify
        "license_last_error": "",
        # Dev-only bypass switch (spec §6.6's "safer dual check" — this
        # flag is the fallback when the env var is hard to set through
        # CEP's node process). MUST NOT be honored when DEV_REPO_ROOT is
        # null or has no sibling .git — cep/js/license.js enforces that,
        # not this default.
        "license_dev_bypass": False,
    }

    def __init__(self) -> None:
        self._state: Dict[str, Any] = {}
        self._loaded = False

    # ── attribute access ───────────────────────────────────────

    def __getattr__(self, name: str) -> Any:
        # Only triggered for attrs not set directly. Falls through to
        # _DEFAULTS for missing keys.
        if name.startswith("_"):
            raise AttributeError(name)
        if not self._loaded:
            self._load()
        if name in self._state:
            return self._state[name]
        if name in self._DEFAULTS:
            return self._DEFAULTS[name]
        raise AttributeError(name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name in ("_state", "_loaded"):
            super().__setattr__(name, value)
            return
        if not self._loaded:
            self._load()
        self._state[name] = value

    # ── persistence ────────────────────────────────────────────

    def _load(self) -> None:
        path = state_json_path()
        data: Dict[str, Any] = {}
        if path.is_file():
            try:
                with path.open("r", encoding="utf-8") as fh:
                    data = json.load(fh) or {}
            except (OSError, json.JSONDecodeError):
                data = {}
        self._state = data
        self._loaded = True

    def reload(self) -> None:
        self._loaded = False
        self._load()

    def save(self) -> None:
        path = state_json_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        # Read-modify-write so we don't clobber keys this module
        # doesn't know about (e.g. active_studio_profile).
        merged: Dict[str, Any] = {}
        if path.is_file():
            try:
                with path.open("r", encoding="utf-8") as fh:
                    merged = json.load(fh) or {}
            except (OSError, json.JSONDecodeError):
                merged = {}
        merged.update(self._state)
        tmp = path.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(merged, fh, indent=2)
        os.replace(tmp, path)


# Module-level singleton — import as `from logic.preferences_state
# import preferences` and read / write attributes directly.
preferences = _Preferences()
