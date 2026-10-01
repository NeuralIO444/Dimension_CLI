#!/usr/bin/env python3
# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/scripts/capture_bridge_fixtures.py
PR-B Layer 2 fixture-capture helper.

Captures real JSX-written result.json payloads and saves them to
python/tests/fixtures/bridge/ for Layer 2 contract tests. The
fixtures verify that the Pydantic schemas in
python/models/bridge_jobs.py accept what the running JSX panel
actually writes — the synthetic-fixture anti-pattern guard
documented in CLAUDE.md.

Usage
-----
Open After Effects with the Dimension panel loaded and a comp
visible in the Composition panel. Then from the repo root:

    python python/scripts/capture_bridge_fixtures.py <fixture>

Where <fixture> is one of:

    tag-write          Apply HERO to a layer with a known UID
    tag-write-clear    Clear the HERO tag from the same layer
    select-layer       Select a layer by UID
    duplicate-plan     Run a duplication plan against a comp
    mask-toggle        Toggle a safe-zone mask
    scrape             Synthesize from the most recent session
                       archive (no live AE round-trip needed)
    all                Run all of the above in sequence

Each captured fixture is written to:

    python/tests/fixtures/bridge/<fixture-name>-OK.json

with a sibling metadata file:

    python/tests/fixtures/bridge/<fixture-name>-OK.meta.json

The metadata records:
    - capture_date          (ISO 8601 timestamp)
    - dimension_version     (Python SCHEMA_VERSION at capture)
    - bridge_version        (Python BRIDGE_SCHEMA_VERSION)
    - jsx_version           (DIMENSION_SCHEMA_VERSION from JSX disk)
    - jsx_bridge_version    (DIMENSION_BRIDGE_SCHEMA_VERSION from JSX disk)
    - source_comp           (active AE comp name at capture)
    - capture_args          (the kwargs passed to the bridge method)

Re-capturing a fixture overwrites the existing file. Re-capture
when the wire format changes (which is a deliberate choice
requiring a BRIDGE_SCHEMA_VERSION bump).

Why this script exists
----------------------
The bridge result.json files are deleted by _wait_for_result's
cleanup pass (sovereign_bridge.py:179-182). To capture a real
result, we hook into the bridge BEFORE that cleanup — wrap the
public method, intercept the return value, persist it.

The scrape fixture is special: scrape results consume their
manifest into atomic_write_json + sha256-sidecar before
_wait_for_result returns, but the result envelope itself is just
{status, manifest, comp, result}. We can synthesize that envelope
from any session archive's scrape_manifest.json — no live AE
round-trip required for the scrape fixture.

For the other four (tag-write, select-layer, duplicate-plan,
mask-toggle), live AE is required. Each capture monkey-patches
SovereignBridge._wait_for_result to persist the payload before
returning.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "python"))

from core.schema_version import (  # noqa: E402
    BRIDGE_SCHEMA_VERSION,
    SCHEMA_VERSION,
    read_jsx_bridge_version_from_disk,
    read_jsx_version_from_disk,
)


FIXTURES_DIR = REPO_ROOT / "python" / "tests" / "fixtures" / "bridge"


def _save(fixture_name: str, payload: dict, capture_args: dict,
          source_comp: str | None = None) -> None:
    """Persist payload + metadata side-by-side under FIXTURES_DIR.

    Found via live-AE audit 2026-07-18: every caller names its fixture
    with an "-OK" suffix (`select-layer-OK`, `query-layer-state-OK`,
    etc.) but nothing checked that the captured payload's `status` was
    actually "OK" before saving under that name. A stale UID, no
    active comp, or any other real-world hiccup during capture would
    silently commit a fixture that CLAIMS to be the OK-path example
    but is actually an ERROR/NOT_FOUND payload -- defeating the
    Layer 2 anti-pattern guard's whole purpose (proving JSX's real OK
    shape, not just that *a* real payload parses). Caught in practice:
    this happened twice in a row capturing select-layer-OK.json before
    this guard was added.
    """
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)

    if fixture_name.endswith("-OK") and payload.get("status") != "OK":
        print(
            f"ERROR: refusing to save '{fixture_name}' — captured "
            f"payload has status={payload.get('status')!r}, not 'OK' "
            f"(error={payload.get('error')!r}). Fix the capture "
            f"conditions (active comp, valid uid, fresh poller) and "
            f"retry.",
            file=sys.stderr,
        )
        sys.exit(1)

    fixture_path = FIXTURES_DIR / f"{fixture_name}.json"
    meta_path = FIXTURES_DIR / f"{fixture_name}.meta.json"

    fixture_path.write_text(
        json.dumps(payload, indent=2) + "\n", encoding="utf-8",
    )

    meta = {
        "capture_date": dt.datetime.now(dt.timezone.utc)
                          .isoformat(timespec="seconds"),
        "dimension_version": SCHEMA_VERSION,
        "bridge_version": BRIDGE_SCHEMA_VERSION,
        "jsx_version": read_jsx_version_from_disk(),
        "jsx_bridge_version": read_jsx_bridge_version_from_disk(),
        "source_comp": source_comp,
        "capture_args": capture_args,
    }
    meta_path.write_text(
        json.dumps(meta, indent=2) + "\n", encoding="utf-8",
    )

    print(f"  saved → {fixture_path.relative_to(REPO_ROOT)}")
    print(f"  meta  → {meta_path.relative_to(REPO_ROOT)}")


def _patch_wait_for_result(bridge, captured: list) -> None:
    """Monkey-patch the bridge's _wait_for_result so we capture the
    raw payload before the post-receive validation chain runs.
    Restores the original method when the captured list is non-empty.

    Found via live-AE audit 2026-07-18: `_execute_file_bridge_job` calls
    `self._wait_for_result(result_path, timeout_s, dispatch_ms)` (three
    positional args, `dispatch_ms` added after this wrapper was
    written). The old two-arg `_wrap` raised TypeError on every real
    call, silently swallowed by the bridge method's own broad
    except-Exception into a generic TIMEOUT -- meaning no fixture
    capture (select-layer, tag-write, etc.) had ever actually worked
    through this path. `*args, **kwargs` instead of a fixed signature
    so a future parameter added to `_wait_for_result` can't silently
    break this again the same way.
    """
    original = bridge._wait_for_result

    def _wrap(*args, **kwargs):
        payload = original(*args, **kwargs)
        if isinstance(payload, dict):
            captured.append(dict(payload))
        return payload

    bridge._wait_for_result = _wrap


def _resolve_source_comp() -> str | None:
    """Best-effort: read the most recent session archive's
    scrape_manifest to surface the comp name in metadata. Returns
    None if no archive is available."""
    archive_root = REPO_ROOT / "logs" / "archive"
    if not archive_root.is_dir():
        return None
    sessions = sorted(archive_root.glob("Session_*"))
    if not sessions:
        return None
    manifest = sessions[-1] / "scrape_manifest.json"
    if not manifest.is_file():
        return None
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
        return (data.get("project_info") or {}).get("name")
    except (OSError, json.JSONDecodeError):
        return None


# ── Capture handlers (one per job type) ──────────────────────────


def capture_scrape(skip_if_no_archive: bool = False) -> None:
    """Synthesize a scrape result fixture from the most recent
    session archive. No live AE round-trip required.

    `skip_if_no_archive` turns the missing-archive case into a clean
    no-op instead of a hard failure. It exists for CI: a hosted runner
    has no `logs/archive/Session_*` because an archive is only produced
    by a real conform in AE, so the synthesis step could never succeed
    there — and because it exited non-zero, the two steps after it (the
    scrape-OK drift check and the wire-contract pytest) never ran at
    all. Skipping lets the wire-contract test execute. Left off by
    default so a developer running this locally still gets the loud
    error and the "run a scrape in AE first" hint, which is the only
    place that hint is actionable.

    The on-the-wire scrape result is just an envelope around the
    manifest path:
        {status: "OK", manifest: <path>, comp: <name>, result: <path>}
    plus the PR-B stamps. We build that envelope and write it; the
    Layer 2 test exercises the BridgeScrapeResult schema against
    it.
    """
    print("[scrape] synthesizing from latest session archive...")
    archive_root = REPO_ROOT / "logs" / "archive"
    sessions = sorted(archive_root.glob("Session_*")) if archive_root.is_dir() else []
    if not sessions:
        if skip_if_no_archive:
            print("[scrape] no session archive present — skipping "
                  "synthesis (nothing to re-capture, so no drift).")
            return
        print("ERROR: no session archives found at logs/archive/Session_*",
              file=sys.stderr)
        print("       run a scrape in AE first (any conform produces one)",
              file=sys.stderr)
        sys.exit(1)
    latest = sessions[-1]
    manifest_path = latest / "scrape_manifest.json"
    if not manifest_path.is_file():
        print(f"ERROR: {manifest_path} missing", file=sys.stderr)
        sys.exit(1)

    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    comp = (data.get("project_info") or {}).get("name", "")

    payload = {
        "schema_version": BRIDGE_SCHEMA_VERSION,
        "job_type": "scrape",
        "status": "OK",
        "manifest": str(manifest_path),
        "comp": comp,
        "result": "",
        "dimension_schema_version": read_jsx_version_from_disk(),
        "dimension_panel_build": "v5.1.11",
    }
    _save("scrape-OK", payload,
          capture_args={"source": "session_archive",
                        "session": latest.name},
          source_comp=comp)


def capture_tag_write(uid: str, tag: str = "HERO") -> None:
    """Live AE round-trip: apply a manual tag to the named UID."""
    print(f"[tag-write] applying {tag!r} to uid {uid!r}...")
    from bridge.sovereign_bridge import SovereignBridge

    bridge = SovereignBridge(project_root=str(REPO_ROOT))
    captured: list = []
    _patch_wait_for_result(bridge, captured)
    bridge.apply_manual_tag(uid, tag, timeout_s=8.0)
    if not captured:
        print("ERROR: no payload captured (bridge did not return one)",
              file=sys.stderr)
        sys.exit(1)
    _save("tag-write-OK", captured[0],
          capture_args={"uid": uid, "tag": tag},
          source_comp=_resolve_source_comp())


def capture_tag_write_clear(uid: str) -> None:
    """Live AE round-trip: clear the manual tag from the named UID."""
    print(f"[tag-write] clearing tag from uid {uid!r}...")
    from bridge.sovereign_bridge import SovereignBridge

    bridge = SovereignBridge(project_root=str(REPO_ROOT))
    captured: list = []
    _patch_wait_for_result(bridge, captured)
    bridge.clear_manual_tag(uid, timeout_s=8.0)
    if not captured:
        print("ERROR: no payload captured", file=sys.stderr)
        sys.exit(1)
    _save("tag-write-clear-OK", captured[0],
          capture_args={"uid": uid},
          source_comp=_resolve_source_comp())


def capture_select_layer(uid: str) -> None:
    """Live AE round-trip: select a layer by UID."""
    print(f"[select-layer] selecting uid {uid!r}...")
    from bridge.sovereign_bridge import SovereignBridge

    bridge = SovereignBridge(project_root=str(REPO_ROOT))
    captured: list = []
    _patch_wait_for_result(bridge, captured)
    bridge.select_layer(uid, timeout_s=4.0)
    if not captured:
        print("ERROR: no payload captured", file=sys.stderr)
        sys.exit(1)
    _save("select-layer-OK", captured[0],
          capture_args={"uid": uid},
          source_comp=_resolve_source_comp())


def capture_query_layer_state(
    uid: str,
    property_paths: list[str] | None = None,
    time_s: float | None = None,
) -> None:
    """Live AE round-trip: read property values off a layer by UID."""
    print(f"[query-layer-state] querying uid {uid!r}...")
    from bridge.sovereign_bridge import SovereignBridge

    paths = property_paths or ["position", "scale"]
    bridge = SovereignBridge(project_root=str(REPO_ROOT))
    captured: list = []
    _patch_wait_for_result(bridge, captured)
    bridge.query_layer_state(uid, paths, time_s=time_s, timeout_s=4.0)
    if not captured:
        print("ERROR: no payload captured", file=sys.stderr)
        sys.exit(1)
    _save("query-layer-state-OK", captured[0],
          capture_args={"uid": uid, "property_paths": paths, "time_s": time_s},
          source_comp=_resolve_source_comp())


def capture_duplicate_plan() -> None:
    """Live AE round-trip: run a minimal duplication plan against
    the active comp. Requires the active comp to have at least one
    nested precomp suitable for duplication.

    Plan construction here mirrors the unit-test pattern in
    test_duplication_bridge.py — build a minimal DuplicationPlan
    with one duplicate + one rewire."""
    print("[duplicate-plan] dispatching minimal plan...")
    from bridge.sovereign_bridge import SovereignBridge
    from models.duplication_plan import DuplicationPlan

    bridge = SovereignBridge(project_root=str(REPO_ROOT))
    captured: list = []
    _patch_wait_for_result(bridge, captured)

    # NB: the user must supply real UIDs from the active comp via
    # the --uid flag for live capture. For headless/automated
    # runs against a known fixture comp, hardcode here.
    plan = DuplicationPlan(
        session_id="capture-fixture",
        session_folder="From Dimensions/CAPTURE",
        preset_id="CAPTURE",
        target_dimensions=(1080, 1920),
        aspect_ratio_changed=True,
        duplicates=[],
        rewires=[],
        depth_max=0,
    )
    log_path = REPO_ROOT / ".dimension" / "capture-duplication-log.json"
    bridge.apply_duplication_plan(
        plan,
        conformed_comp_name="[DIMENSION] CAPTURE",
        log_path=str(log_path),
        timeout_s=12.0,
    )
    if not captured:
        print("ERROR: no payload captured", file=sys.stderr)
        sys.exit(1)
    _save("duplicate-plan-OK", captured[0],
          capture_args={"conformed_comp_name": "[DIMENSION] CAPTURE"},
          source_comp=_resolve_source_comp())


def capture_mask_toggle(action: str = "import",
                         mask_path: str | None = None) -> None:
    """Live AE round-trip: toggle a safe-zone mask import/remove."""
    print(f"[mask-toggle] action={action!r}...")
    from bridge.sovereign_bridge import SovereignBridge

    bridge = SovereignBridge(project_root=str(REPO_ROOT))
    captured: list = []
    _patch_wait_for_result(bridge, captured)
    bridge.toggle_safe_zone_mask(action=action, mask_path=mask_path, timeout_s=4.0)
    if not captured:
        print("ERROR: no payload captured", file=sys.stderr)
        sys.exit(1)
    _save("mask-toggle-OK", captured[0],
          capture_args={"action": action, "mask_path": mask_path},
          source_comp=_resolve_source_comp())


# ── CLI ──────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Capture JSX-written bridge result fixtures "
                    "for PR-B Layer 2 contract tests.",
        epilog="Open AE with the Dimension panel before running "
               "anything other than 'scrape'.",
    )
    parser.add_argument(
        "fixture",
        choices=[
            "tag-write", "tag-write-clear", "select-layer",
            "query-layer-state", "duplicate-plan", "mask-toggle",
            "scrape", "all",
        ],
        help="Which fixture to capture",
    )
    parser.add_argument(
        "--skip-if-no-archive", action="store_true",
        help="For 'scrape': exit 0 instead of failing when no session "
             "archive exists. Intended for CI, where an archive can "
             "never be present; keeps the steps after synthesis "
             "reachable. Local runs should omit it.",
    )
    parser.add_argument(
        "--uid",
        help="Layer UID for tag-write / tag-write-clear / select-layer / "
             "query-layer-state",
    )
    parser.add_argument(
        "--tag", default="HERO",
        help="Tag value for tag-write (default: HERO)",
    )
    parser.add_argument(
        "--mask-path",
        help="Mask PNG path for mask-toggle import",
    )
    parser.add_argument(
        "--mask-action", default="import", choices=["import", "remove"],
        help="Action for mask-toggle (default: import)",
    )
    parser.add_argument(
        "--property-paths", default="position,scale",
        help="Comma-separated property names for query-layer-state "
             "(default: position,scale)",
    )
    parser.add_argument(
        "--time-s", type=float, default=None,
        help="Explicit time in seconds for query-layer-state "
             "(default: rest-pose static read)",
    )
    args = parser.parse_args(argv)

    print(f"PR-B fixture capture → {FIXTURES_DIR.relative_to(REPO_ROOT)}")
    print(f"  bridge version: {BRIDGE_SCHEMA_VERSION}")
    print(f"  jsx bridge version on disk: "
          f"{read_jsx_bridge_version_from_disk()}")
    print()

    if args.fixture == "scrape":
        capture_scrape(skip_if_no_archive=args.skip_if_no_archive)
    elif args.fixture == "tag-write":
        if not args.uid:
            parser.error("tag-write requires --uid")
        capture_tag_write(args.uid, args.tag)
    elif args.fixture == "tag-write-clear":
        if not args.uid:
            parser.error("tag-write-clear requires --uid")
        capture_tag_write_clear(args.uid)
    elif args.fixture == "select-layer":
        if not args.uid:
            parser.error("select-layer requires --uid")
        capture_select_layer(args.uid)
    elif args.fixture == "query-layer-state":
        if not args.uid:
            parser.error("query-layer-state requires --uid")
        paths = [p.strip() for p in args.property_paths.split(",") if p.strip()]
        capture_query_layer_state(args.uid, paths, args.time_s)
    elif args.fixture == "duplicate-plan":
        capture_duplicate_plan()
    elif args.fixture == "mask-toggle":
        capture_mask_toggle(args.mask_action, args.mask_path)
    elif args.fixture == "all":
        if not args.uid:
            parser.error("'all' requires --uid for the tag-write / "
                         "select-layer / query-layer-state captures")
        capture_scrape()
        capture_tag_write(args.uid, args.tag)
        capture_tag_write_clear(args.uid)
        capture_select_layer(args.uid)
        paths = [p.strip() for p in args.property_paths.split(",") if p.strip()]
        capture_query_layer_state(args.uid, paths, args.time_s)
        capture_duplicate_plan()
        capture_mask_toggle(args.mask_action, args.mask_path)

    print()
    print("Done. Re-run pytest python/tests/test_bridge_contracts_jsx.py "
          "to lift the skip markers.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
