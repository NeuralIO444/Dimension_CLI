# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/models/bridge_jobs.py
PR-B — Pydantic schemas for the Python ↔ JSX file-bridge contract.

The bridge moves jobs through the file system:
  Python writes a job descriptor JSON to .dimension_inbox/job_<uuid>.json
  JSX poller in Scripts/Dimension_Launcher.jsx claims, dispatches by `type`
  JSX writes a result JSON to <result_path> via tmp+rename
  Python reads the result and validates it through the schemas here

Six job types exist on the wire today. Five are typed; the sixth
(inject) is the legacy default with no `type` field at all and is
identified by presence of `log` + `babysitter` fields.

Wire format note
----------------
Typed jobs use **kebab-case** type values verbatim:
  "tag-write", "select-layer", "duplicate-plan", "panel-slicing-plan",
  "mask-toggle"

NOT snake_case. The original scope doc said `tag_write` etc.;
PR-B investigation confirmed the wire format is kebab-case and
the schemas pin the wire value verbatim via Literal[].

Schema versioning
-----------------
Every job and result carries `schema_version`, stamped from the
`BRIDGE_SCHEMA_VERSION` constant in `python/core/schema_version.py`.
This is DISTINCT from the product `SCHEMA_VERSION` (currently
"5.8.13") that's used for panel staleness detection.

Bridge schema and product schema evolve independently:
  - Product version bumps for UI changes, feature releases, etc.
  - Bridge schema version bumps ONLY when the wire contract changes

Policy on version mismatch: structured-error refusal, globally.
Unknown `schema_version` → refuse to parse → return
BridgeSchemaVersionError with expected vs. received version.
No best-effort parsing. No per-job-type variation. The whole
point of this module is to make contract drift loud.

Status enum
-----------
Result.status is a Literal:
  "OK"            — normal success
  "ERROR"         — handler failure, `error` field has the reason
  "POLLER_STALE"  — JSX poller heartbeat is stale (select-layer only)
  "TIMEOUT"       — Python gave up waiting (select-layer best-effort)

Adding a status value is a contract change requiring a
BRIDGE_SCHEMA_VERSION bump and synchronized JSX update.

Anti-pattern guard
------------------
Tests for these schemas come in three layers:
  Layer 1 — Python-only unit tests (synthetic dicts are fine here
            because the contract under test IS the Python schema)
  Layer 2 — JSX-written fixture contract tests (real result.json
            files captured from a live AE run, parsed through the
            discriminated union)
  Layer 3 — Dispatch/receive integration tests (mock the FS
            boundary, verify validation fires at the right edges)

The Layer 2 fixtures are required by CLAUDE.md's synthetic-fixture
anti-pattern: a Python schema test that only consumes Python-built
dicts proves the schema is internally consistent, NOT that JSX
actually writes payloads matching it. See PR #45 + PR #48 for
the case studies.
"""

from __future__ import annotations

from typing import Annotated, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator


# ── Bridge schema version ────────────────────────────────────────
# Re-exported here for convenience; canonical definition lives in
# python/core/schema_version.py alongside the product SCHEMA_VERSION.
# Keep them on separate lines so a refactor that fans out to JSX
# can target one without touching the other.

from core.schema_version import BRIDGE_SCHEMA_VERSION  # noqa: E402


# ── Errors ───────────────────────────────────────────────────────


class BridgeSchemaVersionError(ValueError):
    """Raised when an incoming job or result carries a
    `schema_version` that this Python build does not recognise.

    Both higher and lower versions raise — the policy is strict
    refusal until the contract is explicitly updated. Bridge
    contract version mismatches are the loudest possible signal
    that the JSX panel and Python orchestrator need to be
    re-synced (typically a panel reload after pulling new code).
    """

    def __init__(self, expected: str, received: object,
                 source: str = "unknown") -> None:
        self.expected = expected
        self.received = received
        self.source = source
        super().__init__(
            f"Bridge schema version mismatch in {source}: "
            f"expected {expected!r}, received {received!r}. "
            f"Reload the AE Dimension panel or update the Python "
            f"build so both sides agree."
        )


# ── Common base classes ──────────────────────────────────────────


class _BridgeJobBase(BaseModel):
    """Fields common to every job descriptor written by Python.

    Concrete job types add their `type` discriminator (or omit it,
    in InjectJob's case) plus type-specific payload fields.

    `result` is the absolute path where the JSX side should write
    the result.json. Today it's injected by `_write_job_file()` in
    `python/bridge/sovereign_bridge.py` after the job dict is built;
    the schema accepts both pre- and post-injection forms by
    leaving it Optional. Validation at the dispatch boundary will
    re-check that `result` is set before write.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: str = BRIDGE_SCHEMA_VERSION
    assets: str
    ts: float
    result: Optional[str] = None


class _BridgeResultBase(BaseModel):
    """Fields common to every result payload written by JSX.

    `job_type` is the discriminator for the result-side
    `BridgeResult` union. JSX `stampResult()` (in
    Scripts/Dimension_Launcher.jsx) stamps this onto every payload
    so Python can dispatch on it without correlating against the
    job descriptor that prompted the result.

    `dimension_schema_version` and `dimension_panel_build` are the
    PRODUCT version stamps (existing behaviour, unrelated to
    BRIDGE_SCHEMA_VERSION). They flow through unchanged for
    staleness detection.

    `claim_ms` / `started_ms` / `finished_ms` (wire keys `_claim_ms` /
    `_started_ms` / `_finished_ms`) are the Slot 17 bridge handshake
    timing fields stamped by JSX `stampResult()` on every result
    payload, over BOTH transports. They were never added here, so
    every real JSX-written result (any job type, either transport)
    failed this model's `extra="forbid"` validation and silently
    degraded to a generic TIMEOUT in every dispatch method's broad
    except-Exception handler — undetected because no prior test
    exercised a real round-trip against live AE. Declared via
    `Field(alias=...)` (not a bare `_name` annotation) because
    Pydantic v2 treats leading-underscore class attributes as private
    attributes, invisible to input validation, which would silently
    fail to fix this — verified empirically before writing this fix.
    `populate_by_name=True` lets code construct these by their
    Python-side name; the wire alias is what JSX actually sends.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_version: str = BRIDGE_SCHEMA_VERSION
    status: Literal["OK", "ERROR", "POLLER_STALE", "TIMEOUT", "NOT_FOUND", "OCIO_BLOCKED"]
    error: Optional[str] = None
    dimension_schema_version: Optional[str] = None
    dimension_panel_build: Optional[str] = None
    claim_ms: Optional[int] = Field(default=None, alias="_claim_ms")
    started_ms: Optional[int] = Field(default=None, alias="_started_ms")
    finished_ms: Optional[int] = Field(default=None, alias="_finished_ms")


# ── Job descriptors (Python → JSX) ───────────────────────────────


class ScrapeJob(_BridgeJobBase):
    """Trigger a comp scrape and write `manifest` to disk.

    Dispatched from `SovereignBridge.trigger_scrape()`.
    Handled by `_handleScrapeJob` in Dimension_Launcher.jsx.
    """

    type: Literal["scrape"]
    manifest: str
    mode: str = "standard"
    version: int = 1  # legacy per-job version int; kept for back-compat


class TagWriteJob(_BridgeJobBase):
    """Apply or clear a manual `#TAG` hashtag on a layer comment.

    `tag` of None means clear (handled by
    `SovereignBridge.clear_manual_tag()`).

    Optional fallback fields `layer_index`, `layer_name`,
    `comp_name` were added in v5.8.13 to recover when
    `findLayerByUID()` misses (e.g. after a duplication that
    re-keys UIDs). All three are Optional because the primary
    lookup path is by UID.
    """

    type: Literal["tag-write"]
    uid: str
    tag: Optional[str]  # None = clear
    version: int = 1
    # v5.8.13 fallback fields
    layer_index: Optional[int] = None
    layer_name: Optional[str] = None
    comp_name: Optional[str] = None


class SelectLayerJob(_BridgeJobBase):
    """v5.5.5 — request AE to select + scroll-to a layer by UID.

    Best-effort: if the JSX poller is stale, Python returns
    POLLER_STALE rather than raising. UI's double-click handler
    swallows non-OK responses quietly.

    Bug F C (2026-04-29) — Optional fallback fields `layer_index`,
    `layer_name`, `comp_name` mirror `TagWriteJob`'s shape. JSX
    `_handleSelectLayerJob` falls back to `_findLayerByIndexAndName`
    when the UID lookup misses (common for untagged layers before
    Bug F B's scrape-stamping fix lands). All three are Optional
    because UID remains the primary path.
    """

    type: Literal["select-layer"]
    uid: str
    version: int = 1
    # Bug F C fallback fields
    layer_index: Optional[int] = None
    layer_name: Optional[str] = None
    comp_name: Optional[str] = None


class QueryLayerStateJob(_BridgeJobBase):
    """Harness Slice 1 — read-only property-value query for AE-side
    verification tests. Mirrors SelectLayerJob's layer-resolution shape
    (uid + index/name/comp_name fallback) but never mutates AE state
    (no `.selected` write, no `setValue` call).

    `property_paths` is validated non-empty here; the actual per-name
    allow-list (transform-group + cameraOption-group names) is enforced
    JSX-side by `_resolveQueryProp` — the schema doesn't know AE's
    property graph, so an unknown name surfaces in the RESULT's
    `unresolved_paths`, not as a schema-level rejection.

    `time_s` is optional. Omitted -> JSX reads the deterministic
    REST-POSE value (first-keyframe-or-t=0, preExpression=true) via
    `DIMENSION.core.value.readProperty()` — see CLAUDE.md's "prop.value
    reads at the playhead" sharp edge. Provided -> JSX reads
    `prop.valueAtTime(time_s, false)`, the same value AE's Property
    panel shows at that frame, so a test can assert against a specific
    keyed frame instead of asking a human to read the panel.
    """

    type: Literal["query-layer-state"]
    uid: str
    property_paths: List[str]
    time_s: Optional[float] = None
    version: int = 1
    # Mirrors SelectLayerJob's Bug F C fallback fields.
    layer_index: Optional[int] = None
    layer_name: Optional[str] = None
    comp_name: Optional[str] = None

    @model_validator(mode="after")
    def _property_paths_non_empty(self) -> "QueryLayerStateJob":
        if not self.property_paths:
            raise ValueError(
                "QueryLayerStateJob: property_paths must be a "
                "non-empty list"
            )
        return self


class DuplicatePlanJob(_BridgeJobBase):
    """v5.8 — execute a flat-precomp duplication plan.

    Exactly one of `conformed_comp_id` (AE-assigned int) or
    `conformed_comp_name` (str, typically `[DIMENSION] <preset>`)
    must be provided. The model_validator enforces this.
    """

    type: Literal["duplicate-plan"]
    plan: dict
    log_path: str
    progress_log_path: str
    babysitter: str
    version: int = 1
    conformed_comp_id: Optional[int] = None
    conformed_comp_name: Optional[str] = None

    @model_validator(mode="after")
    def _exactly_one_comp_ref(self) -> "DuplicatePlanJob":
        has_id = self.conformed_comp_id is not None
        has_name = bool(self.conformed_comp_name)
        if has_id and has_name:
            raise ValueError(
                "DuplicatePlanJob: specify conformed_comp_id OR "
                "conformed_comp_name, not both"
            )
        if not has_id and not has_name:
            raise ValueError(
                "DuplicatePlanJob: exactly one of conformed_comp_id "
                "or conformed_comp_name is required"
            )
        return self


class ReconPlanJob(_BridgeJobBase):
    """Stage 4 — materialise new AE comps from a ReconstructionPlan.

    Dispatched from `SovereignBridge.execute_reconstruction_plan()`.
    Handled by `dispatchRequest` in socket_server.jsx (line ~352).

    `log_path` is optional; when set Babysitter also writes
    recon_result.json to that path (useful for post-run inspection).
    """

    type: Literal["recon-plan"]
    plan: dict
    babysitter: str
    log_path: Optional[str] = None
    version: int = 1


class MaskToggleJob(_BridgeJobBase):
    """v5.2.5 — toggle a safe-zone mask import in AE.

    `action: "import"` requires `mask_path`. `action: "remove"`
    ignores it (and the field may be None).
    """

    type: Literal["mask-toggle"]
    action: Literal["import", "remove"]
    mask_path: Optional[str]
    babysitter: str
    version: int = 1

    @model_validator(mode="after")
    def _import_requires_mask_path(self) -> "MaskToggleJob":
        if self.action == "import" and not self.mask_path:
            raise ValueError(
                "MaskToggleJob: mask_path is required when "
                "action='import'"
            )
        return self


class ColorMatchRenderJob(_BridgeJobBase):
    """Color Match (Track D, CM2) — render a reference-frame PNG of a
    sealed precomp for handoff to a colorist (Flame/Resolve).

    Dispatched from `ColorMatchBridge.render_reference_frame()`.
    Handled by `color-match-render` in `socket_server.jsx`'s
    `dispatchRequest` and `Dimension_Launcher.jsx`'s
    `_handleColorMatchRenderJob`.

    `comp_name` is a fallback lookup key — JSX resolves the target
    comp by `comp_id` first (`String(item.id) === String(comp_id)`)
    and falls back to matching by name if the id lookup misses (same
    id-then-name fallback shape as `SelectLayerJob`).

    `project_path`, when set, is the absolute path to the `.aep` the
    render was requested against. JSX rejects the job with
    `status: "ERROR"` if the currently open project's file path
    doesn't match, rather than silently rendering the wrong project's
    comp — the same class of guard as the inject-side stale-comp
    check in CM4.
    """

    type: Literal["color-match-render"] = "color-match-render"
    comp_id: str
    comp_name: str
    project_path: Optional[str] = None
    output_path: str


class ColorMatchInjectJob(_BridgeJobBase):
    """Color Match (Track D, CM4) — inject a non-destructive 3D LUT
    adjustment layer directly above a precomp wrapper in the active
    master composition.

    RETIRED (issue #9): `ColorMatchBridge.inject_lut()` now honest-fails
    with LUT_UNSCRIPTABLE and never dispatches this job — AE exposes Apply
    Color LUT2's file-path property as NO_VALUE, so injection is a
    permanent platform limitation (Dimension #494). The model is kept as
    the wire-schema record only; do not re-wire a dispatch path.

    `target_layer_uid` is the UID token stamped into the precomp wrapper
    layer's comment field. `parent_comp_id` identifies the master
    containing composition. `lut_path` is the absolute path to the
    `.cube` or `.3dl` file.
    """

    type: Literal["color-match-inject"] = "color-match-inject"
    parent_comp_id: str
    precomp_comp_id: str
    lut_path: str
    target_layer_uid: Optional[str] = None
    project_path: Optional[str] = None


class CreateUnitJob(BaseModel):
    """CEP → Python "Merge into Unit" job (unit-overrides sidecar write).

    Written by the CEP tagging panel directly into `.dimension_inbox`
    — the opposite direction from the Python→JSX jobs above, so it
    does NOT extend `_BridgeJobBase` (no `assets` path; the panel
    stamps its own schema_version "1.0").
    """

    model_config = ConfigDict(extra="forbid")

    type: Literal["create-unit"]
    uids: List[str]
    ts: float
    schema_version: str = "1.0"


class DissolveUnitJob(BaseModel):
    """CEP → Python "Dissolve Unit" job (unit-overrides sidecar write).

    Same CEP→Python direction as CreateUnitJob; wire shape matches
    `BB.dissolveUnit` in `cep/js/backend_bridge.js`.
    """

    model_config = ConfigDict(extra="forbid")

    type: Literal["dissolve-unit"]
    unit_id: str
    ts: float
    schema_version: str = "1.0"


class InjectJob(_BridgeJobBase):
    """Legacy default job — no `type` field on the wire.

    Identified by Dimension_Launcher.jsx's poll loop via the
    presence of `log` + `babysitter` after the typed dispatchers
    return. This is preserved as-is to avoid breaking the
    SovereignLauncher → Babysitter inject flow.

    Dispatched from `python/launcher.py::_dispatch_job()`.

    A future PR (PR-B-followup or v5.12+) may promote this to an
    explicit type. Until then, the schema admits the descriptor
    with no type field. Pydantic's discriminator will not match
    InjectJob from a wire dict; callers parse it explicitly via
    `parse_inject_job()` below.

    Inject-specific fields:
    - `assets` is Optional (the inject dispatcher passes
      `babysitter` and `auditor` as fully-resolved paths and
      doesn't need an assets directory pointer)
    - `result` is Optional (inject is heartbeat-driven via
      `transfer_status.log` and writes no result.json)
    """

    # NB: no `type` field. Parsing is explicit, not via the
    # discriminator.
    assets: Optional[str] = None  # override base: inject doesn't need it
    result: Optional[str] = None  # override base: inject has no result.json
    manifest: str
    log: str
    babysitter: str
    auditor: str
    version: int = 1


# ── Discriminated union for typed jobs ───────────────────────────
#
# Pydantic discriminator dispatch ONLY covers the typed jobs that
# carry a `type` field. InjectJob has no `type` field on the wire
# and is parsed via the explicit helper below.
#
# PanelSlicingPlanJob lives in panel_slicing_jobs.py (imports the
# bases from this module) — late-import after those bases exist.

from models.panel_slicing_jobs import (  # noqa: E402
    BridgePanelSlicingPlanResult,
    PanelSlicingPlanJob,
)

BridgeJob = Annotated[
    Union[
        ScrapeJob,
        TagWriteJob,
        SelectLayerJob,
        QueryLayerStateJob,
        DuplicatePlanJob,
        PanelSlicingPlanJob,
        MaskToggleJob,
        ReconPlanJob,
        ColorMatchRenderJob,
        ColorMatchInjectJob,
    ],
    Field(discriminator="type"),
]


# ── Result payloads (JSX → Python) ───────────────────────────────


class BridgeScrapeResult(_BridgeResultBase):
    """Result payload for a `scrape` job."""

    job_type: Literal["scrape"]
    manifest: Optional[str] = None
    comp: Optional[str] = None
    result: Optional[str] = None


class BridgeTagWriteResult(_BridgeResultBase):
    """Result payload for a `tag-write` job.

    Success-path fields written by `Layer.applyManualTag` /
    `Layer.clearManualTag` in SovCore_Layer.jsx:
      - `uid`     — echoed back from the job
      - `tag`     — the tag that was written (None on clear)
      - `source`  — content_tag_source value the JSX assigned
                    (e.g. "manual_comment", "manual_label")
      - `label`   — AE label color index that backed the tag,
                    if the tag was applied via label color
      - `layer_index`, `layer_name` — present when the v5.8.13
                    fallback path resolved the layer
      - `resolved_via` — "uid" | "index", mirrors
                    `BridgeSelectLayerResult`. `applyManualTag()`
                    (SovCore_Layer.jsx) has returned this on every
                    OK result since the same v5.8.13 fallback work
                    that added it to select-layer, but it was never
                    added here — meaning every successful live
                    tag-write silently failed this model's
                    `extra="forbid"` validation and degraded to a
                    generic TIMEOUT. Found via live-AE audit
                    2026-07-18, same root cause class as the
                    `_claim_ms`/`_started_ms`/`_finished_ms` gap on
                    `_BridgeResultBase`.

    Future versions can add fields without breaking parsers as
    long as BRIDGE_SCHEMA_VERSION is bumped.
    """

    job_type: Literal["tag-write"]
    uid: Optional[str] = None
    tag: Optional[str] = None
    source: Optional[str] = None
    label: Optional[int] = None
    layer_index: Optional[int] = None
    layer_name: Optional[str] = None
    resolved_via: Optional[Literal["uid", "index"]] = None


class BridgeSelectLayerResult(_BridgeResultBase):
    """Result payload for a `select-layer` job.

    Wire shape (Dimension_Launcher.jsx::_handleSelectLayerJob):
      - `uid`          — echoed back from the job; present on
                         OK and NOT_FOUND
      - `layer_index`  — AE layer index of the resolved layer
                         (OK only)
      - `layer_name`   — AE layer name of the resolved layer
                         (OK only)
      - `reason`       — explanation string when Python-side
                         returns POLLER_STALE or TIMEOUT (best-
                         effort path, not raised)

    PR-E.2 Bug A: `NOT_FOUND` was missing from the base Literal,
    so a strict parse of the JSX-written payload would have
    rejected it. Added 2026-04-29 alongside `layer_index` /
    `layer_name` (both Optional) which JSX has always emitted on
    the OK path.
    """

    job_type: Literal["select-layer"]
    uid: Optional[str] = None
    layer_index: Optional[int] = None
    layer_name: Optional[str] = None
    # Bug F C — JSX echoes which path resolved the layer ("uid" or
    # "index"). None means the field wasn't emitted (older JSX).
    resolved_via: Optional[Literal["uid", "index"]] = None
    reason: Optional[str] = None


class BridgeQueryLayerStateResult(_BridgeResultBase):
    """Result payload for a `query-layer-state` job.

    Wire shape (Dimension_Launcher.jsx::_handleQueryLayerStateJob AND
    socket_server.jsx::dispatchRequest's "query-layer-state" branch —
    both must emit this identically, see plan Finding 0):
      - `uid`              — echoed back; present on OK and NOT_FOUND
      - `layer_index`      — AE layer index of the resolved layer (OK only)
      - `layer_name`       — AE layer name of the resolved layer (OK only)
      - `resolved_via`     — "uid" | "index", mirrors BridgeSelectLayerResult
      - `time_s`           — echoed back; None means the rest-pose static
                             read was used (see QueryLayerStateJob docstring)
      - `values`           — {path_name: coerced_value}, OK only; only
                             includes paths that resolved successfully
      - `kinds`            — {path_name: "scalar"|"vec2"|"vec3"|...},
                             OK only, parallels `values`
      - `unresolved_paths` — requested paths that don't exist on this
                             layer type (e.g. "zoom" on a non-camera
                             layer); empty list when every path resolved
      - `reason`           — POLLER_STALE / TIMEOUT explanation, mirrors
                             BridgeSelectLayerResult

    Status semantics reuse the existing 5-value enum (see module
    docstring's "adding a status value requires a schema bump" note —
    deliberately NOT done here):
      - NOT_FOUND: layer could not be resolved by uid or index+name
      - ERROR: bootstrap/no-active-comp/missing-uid/empty-property_paths,
        OR every requested path failed to resolve (values would be empty)
      - OK: layer resolved AND at least one path resolved (best-effort;
        unresolved_paths may be non-empty)
    """

    job_type: Literal["query-layer-state"]
    uid: Optional[str] = None
    layer_index: Optional[int] = None
    layer_name: Optional[str] = None
    resolved_via: Optional[Literal["uid", "index"]] = None
    time_s: Optional[float] = None
    values: Optional[dict] = None
    kinds: Optional[dict] = None
    unresolved_paths: Optional[List[str]] = None
    reason: Optional[str] = None


class BridgeDuplicatePlanResult(_BridgeResultBase):
    """Result payload for a `duplicate-plan` job.

    Wire shape (Babysitter.jsx applyDuplicationPlan):
      - `duplicates_made` — count of new precomp duplicates created
      - `rewires_made`    — count of layer source-rewires applied
      - `skipped`         — count of plan items skipped (already
                            duplicated, locked, etc.)
      - `errors`          — count of failed plan items
      - `log`             — the inline duplication-log dict,
                            mirroring duplication_log.json on
                            disk (per-item lists live here:
                            log.duplicates_made[],
                            log.rewires_made[], log.skipped[],
                            log.errors[])

    Note: `errors` is a count int, not a list. The per-item error
    list lives inside `log.errors[]`. PR-B initial schema sketch
    had this inverted; corrected once test mocks revealed the
    actual wire shape.

    `detail` — only on the fatal-crash catch path in
    `applyDuplicationPlan` (`{status:"ERROR", error:"applyDuplicationPlan
    crashed", detail: String(eFatal), log: log}`). Rare (a crash within
    the crash handler's own try block), but undeclared here meant that
    specific path would fail `extra="forbid"` validation. Found via the
    same 2026-07-18 live-AE bridge-result audit as the tag-write /
    mask-toggle gaps below.
    """

    job_type: Literal["duplicate-plan"]
    duplicates_made: Optional[int] = None
    rewires_made: Optional[int] = None
    skipped: Optional[int] = None
    errors: Optional[int] = None
    log: Optional[dict] = None
    detail: Optional[str] = None


class BridgeMaskToggleResult(_BridgeResultBase):
    """Result payload for a `mask-toggle` job.

    Real wire shape (Babysitter.jsx, verified by reading
    `importSafeZoneMask()` / `removeSafeZoneMask()` directly during the
    2026-07-18 live-AE bridge-result audit):
      - import OK: `{status:"OK", layer_index: layer.index, name: layer.name}`
      - remove OK: `{status:"OK", removed: removed}` (count of layers removed)

    `action` / `mask_path` below were the original schema's guess at the
    result shape but are never actually present on a real result (they're
    request-only fields) — harmless as unused Optionals, kept rather than
    removed since removing fields needs separate sign-off. `layer_index`,
    `name`, `removed` are the fields JSX actually returns and were missing
    entirely, so EVERY successful mask-toggle (both actions, both
    transports) failed `extra="forbid"` validation and silently degraded
    to TIMEOUT.
    """

    job_type: Literal["mask-toggle"]
    action: Optional[Literal["import", "remove"]] = None
    mask_path: Optional[str] = None
    layer_index: Optional[int] = None
    name: Optional[str] = None
    removed: Optional[int] = None


class BridgeColorMatchRenderResult(_BridgeResultBase):
    """Result payload for a `color-match-render` job.

    Wire shape (`export_frame.jsx::D.core.exportFrameById`, called
    from both `socket_server.jsx` and `Dimension_Launcher.jsx`):
      - `path`           — echoed `output_path` on success
      - `frame_time_s`   — the frame-snapped seconds value passed to
                           `comp.saveFrameToPng`
      - `snapped_frame`  — the integer frame number the midpoint
                           snapped to (`Math.round(mid / frameDuration)`)
      - `bpc`            — `app.project.bitsPerChannel` (8/16/32);
                           informational here, the sub-16bpc warning
                           gate is CM4's concern, not this job's
      - `is_ocio`        — best-effort substring check on
                           `app.project.workingColorSpace` for
                           "ocio"/"aces"; same caveat as `bpc` — the
                           OCIO/ACES block is enforced at CM4 inject
                           time, not here

    UNVERIFIED AGAINST LIVE AE — `bpc` / `is_ocio` mirror the
    (unrun) `spike_color_state.jsx` probe's reading of
    `app.project.bitsPerChannel` / `app.project.workingColorSpace`.
    Both are Optional so a probe surprise degrades to `None` rather
    than failing `extra="forbid"` validation; confirm against a real
    AE project before CM4 relies on either field to gate anything.
    """

    job_type: Literal["color-match-render"]
    path: Optional[str] = None
    frame_time_s: Optional[float] = None
    snapped_frame: Optional[int] = None
    bpc: Optional[int] = None
    is_ocio: Optional[bool] = None


class BridgeColorMatchInjectResult(_BridgeResultBase):
    """Result payload for a `color-match-inject` job.

    Success-path fields written by ExtendScript `injectColorLut`:
      - `adjustment_layer_index` — index of the adjustment layer in the parent comp
      - `adjustment_layer_name`  — name of the created/updated adjustment layer ("Dimension Color Match")
      - `effect_match_name`      — match name of applied effect ("ADBE Apply Color LUT 2")
      - `reused_existing_layer`  — bool indicating whether an existing adjustment layer was updated
    """

    job_type: Literal["color-match-inject"]
    adjustment_layer_index: Optional[int] = None
    adjustment_layer_name: Optional[str] = None
    effect_match_name: Optional[str] = None
    reused_existing_layer: Optional[bool] = None


class BridgeReconPlanResult(_BridgeResultBase):
    """Result payload for a `recon-plan` job.

    Wire shape (Babysitter.jsx::executeReconstructionPlan):
      - `comps_created`     — list of {name, ae_id, width, height, duration, fps,
                              layers_populated?, population_errors?}
      - `errors`            — list of {phase, name?, detail} error dicts
                              (distinct from the base `error` string, which JSX
                              sets on fatal early-return paths)
      - `layers_populated`  — total layer writes across all comps (Task 5,
                              optional — absent on old JSX or empty-comp-only paths)
      - `population_errors` — list of per-layer error dicts from JSX population
                              phase (Task 5, optional — None when population did
                              not run or produced no errors)

    Note: `errors` here is a LIST (not an int count). Reconstruction
    errors are structured dicts; compare with BridgeDuplicatePlanResult
    where `errors` is an int count and the list lives in `log.errors[]`.

    Task 5 — Task 5 adds `layers_populated` and `population_errors` as
    Optional fields. Existing callers that parse a result without these
    fields see None for both. Backwards-compatible.
    """

    job_type: Literal["recon-plan"]
    comps_created: Optional[List[dict]] = None
    errors: Optional[List[dict]] = None
    layers_populated: Optional[int] = None
    population_errors: Optional[List[dict]] = None


# Note: InjectJob has no result.json. The inject path is driven by
# the transfer-status heartbeat log, not a per-job result file.
# No BridgeInjectResult class is defined.


BridgeResult = Annotated[
    Union[
        BridgeScrapeResult,
        BridgeTagWriteResult,
        BridgeSelectLayerResult,
        BridgeQueryLayerStateResult,
        BridgeDuplicatePlanResult,
        BridgePanelSlicingPlanResult,
        BridgeMaskToggleResult,
        BridgeReconPlanResult,
        BridgeColorMatchRenderResult,
        BridgeColorMatchInjectResult,
    ],
    Field(discriminator="job_type"),
]


# ── Parsing helpers ──────────────────────────────────────────────


def _check_version(payload: dict, source: str) -> None:
    """Raise BridgeSchemaVersionError if the payload's
    `schema_version` field is missing or mismatched.

    Policy is strict refusal — see module docstring."""
    received = payload.get("schema_version")
    if received != BRIDGE_SCHEMA_VERSION:
        raise BridgeSchemaVersionError(
            expected=BRIDGE_SCHEMA_VERSION,
            received=received,
            source=source,
        )


def parse_typed_job(payload: dict):
    """Parse a typed job descriptor from a wire dict.

    Returns a concrete job model (ScrapeJob, TagWriteJob, etc.).
    Raises BridgeSchemaVersionError on version mismatch.
    Raises pydantic.ValidationError on shape mismatch.

    For InjectJob (no `type` field), use `parse_inject_job()`.
    """
    _check_version(payload, source="job")
    from pydantic import TypeAdapter
    return TypeAdapter(BridgeJob).validate_python(payload)


def parse_inject_job(payload: dict) -> InjectJob:
    """Parse an inject job descriptor from a wire dict.

    Inject jobs have no `type` field and require `log` +
    `babysitter` to be present (that's how the JSX side
    identifies them as inject vs. typed).
    """
    _check_version(payload, source="inject job")
    return InjectJob.model_validate(payload)


def parse_result(payload: dict):
    """Parse a result payload from a wire dict.

    Returns a concrete result model. Raises
    BridgeSchemaVersionError on version mismatch. Raises
    pydantic.ValidationError on shape mismatch.

    The `job_type` discriminator must be present — JSX's
    stampResult() is responsible for stamping it onto every
    result payload.
    """
    _check_version(payload, source="result")
    from pydantic import TypeAdapter
    return TypeAdapter(BridgeResult).validate_python(payload)


__all__ = [
    "BRIDGE_SCHEMA_VERSION",
    "BridgeSchemaVersionError",
    # Job descriptors
    "ScrapeJob",
    "TagWriteJob",
    "SelectLayerJob",
    "QueryLayerStateJob",
    "DuplicatePlanJob",
    "PanelSlicingPlanJob",
    "MaskToggleJob",
    "CreateUnitJob",
    "DissolveUnitJob",
    "ReconPlanJob",
    "ColorMatchRenderJob",
    "ColorMatchInjectJob",
    "InjectJob",
    "BridgeJob",
    # Result payloads
    "BridgeScrapeResult",
    "BridgeTagWriteResult",
    "BridgeSelectLayerResult",
    "BridgeQueryLayerStateResult",
    "BridgeDuplicatePlanResult",
    "BridgePanelSlicingPlanResult",
    "BridgeMaskToggleResult",
    "BridgeReconPlanResult",
    "BridgeColorMatchRenderResult",
    "BridgeColorMatchInjectResult",
    "BridgeResult",
    # Parsers
    "parse_typed_job",
    "parse_inject_job",
    "parse_result",
]
