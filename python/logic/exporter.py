# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/logic/exporter.py
Dimension Engine v5.0 — Payload Slicer

Splits the full conformed layer list into 30-layer chunks and writes them
atomically to disk. Babysitter.jsx reads these files sequentially.

Why 30 layers per chunk:
  ExtendScript holds the entire parsed JSON object in RAM before applying
  transforms. On 100+ layer compositions this causes AE to lock up.
  30 layers keeps each chunk under ~200KB, well within safe limits.

File layout:
  Chunks/
    chunk_000.json   — layers 0–29
    chunk_001.json   — layers 30–59
    ...
  chunk_manifest.json — master index with chunk_paths list

All writes are atomic (tmp → rename) to prevent Babysitter reading
a half-written file if Python is killed mid-export.

cleanup_session() is called by SovereignLauncher after a successful audit.
It moves all session files into logs/archive/Session_YYYYMMDD_HHMMSS/.
"""

import os
import shutil
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional

from models.conformed_manifest import (
    ChunkManifest,
    ConformedLayer,
    MirrorRewire,
    MirrorTreeEntry,
)
from core.io_utils import atomic_write_json
from core.logger import log
from core.manifest_builder import calculate_state_hash

def _clamp_chunk_size(raw: Optional[str], default: int = 30) -> int:
    """Parse + clamp a DIMENSION_CHUNK_SIZE-style value to [1, 30].

    Single source of truth for this parse — both the module-level
    default (`MAX_CHUNK_SIZE`, resolved once at import) and
    `slice_and_export`'s per-call override (which must re-read the env
    var dynamically, not just at import time, so a test/long-running
    process can change it mid-session) go through this. Any parse
    failure (missing, empty, non-numeric) falls back to `default`
    rather than raising — a malformed env value must never crash
    export(); it should behave exactly as if the var weren't set.

    Ceiling is 30, matching `ChunkManifest.layers`'s hard Pydantic
    `max_length=30` (models/conformed_manifest.py) — that constraint is
    unconditional and NOT itself governed by this env var, so any
    resolved chunk_size above 30 would just move the crash from here
    to a ValidationError at ChunkManifest construction a few lines
    later. A prior commit (effbc53) narrowed this same clamp from 200
    to 30 with no commit message and no doc updates; audited here and
    confirmed 30 is the value that's actually enforceable — the "1–200"
    range still printed in README.md/BUGS.md was already stale/wrong
    even before that commit (it never matched the schema's hard cap).
    """
    if raw is None:
        return default
    try:
        return max(1, min(int(raw), 30))
    except ValueError:
        return default


def _resolve_chunk_size() -> int:
    """Inject chunk size — override via DIMENSION_CHUNK_SIZE env (1–30)."""
    return _clamp_chunk_size(os.environ.get("DIMENSION_CHUNK_SIZE"))


MAX_CHUNK_SIZE = _resolve_chunk_size()


def _calculate_layer_weight(layer: ConformedLayer) -> float:
    """Calculate complexity weight for an individual conformed layer.

    - Base weight for any static transform: 1.0
    - Keyframe streams: 0.5 per keyframe timestamp (capped at 15.0)
    - Effects: 2.0 per conformed effect (capped at 10.0)
    - Layer styles: 2.0 per conformed style (capped at 5.0)
    """
    weight = 1.0

    # Keyframes complexity
    keys = getattr(layer, "conformed_keys", None)
    if keys:
        key_count = 0
        if isinstance(keys, dict):
            for prop, stream in keys.items():
                if isinstance(stream, dict) and "times" in stream and isinstance(stream["times"], list):
                    key_count += len(stream["times"])
        elif hasattr(keys, "model_dump"):
            dumped = keys.model_dump(exclude_unset=True)
            for prop, stream in dumped.items():
                if isinstance(stream, dict) and "times" in stream and isinstance(stream["times"], list):
                    key_count += len(stream["times"])
        weight += min(15.0, key_count * 0.5)

    # Effects complexity
    effects = getattr(layer, "conformed_effects", None)
    if effects:
        weight += min(10.0, len(effects) * 2.0)

    # Layer styles complexity
    styles = getattr(layer, "conformed_layer_styles", None)
    if styles:
        weight += min(5.0, len(styles) * 2.0)

    return weight


def _partition_layers_adaptively(
    layers: List[ConformedLayer],
    max_chunk_size: int = 30,
    target_weight: float = 30.0,
) -> List[List[ConformedLayer]]:
    """Partition conformed layers into adaptive chunks based on complexity weight.

    - Ensures no chunk exceeds `max_chunk_size` layers (hard cap <= 30 per Pydantic model).
    - Accumulates layers until the chunk weight reaches `target_weight`.
    - Guarantees at least 1 layer per chunk (never creates empty chunks).
    """
    max_layers = min(max_chunk_size, 30)
    if not layers:
        return []

    chunks: List[List[ConformedLayer]] = []
    current_chunk: List[ConformedLayer] = []
    current_weight: float = 0.0

    for layer in layers:
        layer_w = _calculate_layer_weight(layer)
        if len(current_chunk) >= max_layers or (
            len(current_chunk) > 0 and (current_weight + layer_w > target_weight)
        ):
            chunks.append(current_chunk)
            current_chunk = [layer]
            current_weight = layer_w
        else:
            current_chunk.append(layer)
            current_weight += layer_w

    if current_chunk:
        chunks.append(current_chunk)

    return chunks

# PERF-1 (2026-07-04): raw scrape-carryover fields Babysitter never reads,
# stripped from chunk files at write time. ExtendScript's JSON.parse is the
# inject hot path's dominant cost — a 9-layer chunk carrying these fields
# measured 1.1 MB and 12.4 s to parse (99.8% of the tick); without them the
# same chunk parses in milliseconds. Babysitter's chunk-layer read-set is
# conformed_* + identity fields only (verified by grep, enforced by
# test_chunk_payload_contract.py). Deliberately a DENY-list, not an
# allow-list: per the V5_LAYER_KEYS sharp edge (CLAUDE.md), an allow-list
# here would silently drop any future conformed_* field added for the
# injector. These fields stay untouched in the conformed manifest itself —
# only the chunk serialization slims them.
CHUNK_STRIP_FIELDS = {"layer_styles", "properties", "effects", "flags"}
_CHUNK_EXCLUDE = {"layers": {"__all__": CHUNK_STRIP_FIELDS}}


class PayloadSlicer:
    def __init__(self, output_dir: str = "Chunks"):
        self.output_dir = output_dir
        os.makedirs(self.output_dir, exist_ok=True)

    @staticmethod
    def cleanup_session(
        keep_manifest: bool = True,
        batch_session_folder: Optional[str] = None,
        target_subfolder: Optional[str] = None,
    ) -> None:
        """
        Archive all session files after a successful conform.
        Moves scrape_manifest (or copies it if keep_manifest=True), conformed_manifest,
        chunk_manifest, chunks, conform_report, and SHA-256 sidecar.
        If batch_session_folder is provided, files are placed in that folder.
        If target_subfolder is provided, target-specific files are placed in a subfolder.
        """
        archive_dir = os.path.abspath("logs/archive")
        os.makedirs(archive_dir, exist_ok=True)
        
        if batch_session_folder:
            ts_folder = os.path.join(archive_dir, batch_session_folder)
        else:
            ts_folder = os.path.join(archive_dir, time.strftime("Session_%Y%m%d_%H%M%S"))
            
        os.makedirs(ts_folder, exist_ok=True)

        if target_subfolder:
            dest_dir = os.path.join(ts_folder, target_subfolder)
            os.makedirs(dest_dir, exist_ok=True)
        else:
            dest_dir = ts_folder

        # Scrape manifest files can either be copied (for sequential conforms) or moved (for final cleanup).
        manifest_files = [
            "scrape_manifest.json",
            "scrape_manifest.json.sha256",
        ]
        for fname in manifest_files:
            src = os.path.abspath(fname)
            if os.path.exists(src):
                dst_path = os.path.join(ts_folder, fname)
                if os.path.exists(dst_path):
                    os.remove(dst_path)
                if keep_manifest:
                    shutil.copy2(src, dst_path)
                else:
                    shutil.move(src, dst_path)

        # Target-specific files are always moved.
        target_files = [
            "conformed_manifest.json",
            "chunk_manifest.json",
            "conform_report.html",
            "inject_collapse_overrides.json",
        ]
        for fname in target_files:
            src = os.path.abspath(fname)
            if os.path.exists(src):
                dst_path = os.path.join(dest_dir, fname)
                if os.path.exists(dst_path):
                    if os.path.isdir(dst_path):
                        shutil.rmtree(dst_path)
                    else:
                        os.remove(dst_path)
                shutil.move(src, dst_path)

        chunks_dir = os.path.abspath("Chunks")
        if os.path.exists(chunks_dir):
            dst_chunks = os.path.join(dest_dir, "Chunks")
            if os.path.exists(dst_chunks):
                shutil.rmtree(dst_chunks)
            shutil.move(chunks_dir, dst_chunks)

        # Move .dimension files (soe_corrections.json, duplication_log.json) if present
        dimension_files = [
            os.path.join(".dimension", "soe_corrections.json"),
            os.path.join(".dimension", "duplication_log.json"),
        ]
        for rel_path in dimension_files:
            src = os.path.abspath(rel_path)
            if os.path.exists(src):
                fname = os.path.basename(rel_path)
                dst_path = os.path.join(dest_dir, fname)
                if os.path.exists(dst_path):
                    os.remove(dst_path)
                shutil.move(src, dst_path)

        # Remove preview image if present (it's regenerated each scrape)
        preview_path = os.path.abspath("preview.png")
        if os.path.exists(preview_path):
            try:
                os.remove(preview_path)
            except OSError:
                pass

        # Invalidate the JSX-owned manifest pointer only if we are not keeping the manifest alive.
        if not keep_manifest:
            # Bug H: invalidate the JSX-owned manifest pointer so post-archive
            # resolve_manifest_path returns the idle-state None instead of
            # dangling at the just-moved manifest. Import the module (not the
            # symbol) so monkeypatched tests see the current _POINTER_PATH.
            from logic import manifest_source
            if manifest_source._POINTER_PATH.exists():
                try:
                    manifest_source._POINTER_PATH.unlink()
                except OSError:
                    pass

        # Slot 11 Stage D (2026-05-15) — session-end sweep of
        # `.dimension_inbox/results/`. Result files are session-bound
        # by definition: no live result outlives the requestor that
        # issued the job. Stage A quarantined results to this subdir;
        # Stage B made `_wait_for_result` clean up on every exit path.
        # This sweep is the belt-and-suspenders close-out for any
        # result file that somehow outlived its job (e.g. process
        # killed between JSX rename and Python's finally block).
        #
        # Scoped narrowly: ONLY `*.result.json` files inside
        # `.dimension_inbox/results/`. Inbox root, heartbeat file,
        # `processing/`, `quarantine_*/`, and any other entry under
        # `.dimension_inbox/` are deliberately untouched. Best-effort
        # — never raises.
        results_dir = os.path.abspath(
            os.path.join(".dimension_inbox", "results"))
        if os.path.isdir(results_dir):
            try:
                for entry in os.listdir(results_dir):
                    if not entry.endswith(".result.json"):
                        continue
                    fpath = os.path.join(results_dir, entry)
                    try:
                        os.remove(fpath)
                    except OSError as e:
                        log.warning(
                            "Session-end results sweep: unlink failed",
                            extra={"path": fpath, "error": str(e)},
                        )
            except OSError as e:
                log.warning(
                    "Session-end results sweep: listdir failed",
                    extra={"path": results_dir, "error": str(e)},
                )

        log.info("Session archived", extra={"path": ts_folder})

    def slice_and_export(
        self,
        conformed_layers_data: List[Dict[str, Any]],
        expected_comp_name: str = "",
        target_width: int = 0,
        target_height: int = 0,
        preset_label: str = "",
        output_name_template: Optional[str] = None,
        existing_comp_names: Optional[set] = None,
        mirror_tree: Optional[List[MirrorTreeEntry]] = None,
        target_bin_path: str = "",
        mirror_rewires: Optional[List[MirrorRewire]] = None,
        allow_state_hash_bypass: bool = False,
        render_queue_options: Optional[Dict[str, Any]] = None,
        inject_safe_zone_guide: bool = False,
        panel_slicing_plan: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Validate, slice, and atomically write the conformed layer payload to disk.

        Args:
            conformed_layers_data: list of layer dicts from ScaleEngine.conform()["layers"]
            expected_comp_name: name of the comp the scrape captured. Babysitter
                uses this to verify it's about to duplicate the right comp,
                instead of blindly using app.project.activeItem (which can drift
                if the user clicked another comp between scrape and SEND).
            output_name_template: Track B / B2 (2026-08-26) — the active
                preset's `output_name_template` (e.g. `Target.output_name_template`).
                Resolved via `core.output_naming.resolve_output_name` into
                `output_comp_name` on the legacy single-comp path (mirror_tree
                absent). None/empty falls back to `DEFAULT_OUTPUT_NAME_TEMPLATE`
                (`"{source}_{preset}"`) — never the hardcoded `[DIMENSION]` name;
                that fallback lives in Babysitter and only fires when
                `output_comp_name` is absent from the manifest entirely (e.g.
                pre-B2 manifests, or resolution raised below).
            existing_comp_names: every CompItem name already in the AE
                project (from `project_structure.json::comps`, same
                source the mirror-tree path already uses). Passed to
                `resolve_output_name` so the `_v2`/`_v3` collision bump
                can actually fire on a re-run against the same source
                comp + preset — fixed audit finding (2026-08-27); this
                used to always resolve against an empty set, silently
                defeating the collision check. None/omitted degrades to
                "nothing collides" rather than raising — callers without
                project_structure.json handy still get a name, just
                without collision protection.
            mirror_tree:        Slot 12.5 Stage D Item 2 — list of
                target-dimensioned mirror comps Babysitter must create
                (one per unique source comp visited by the recursive
                scrape, de-duped per Q3A). When absent or empty,
                Babysitter falls back to the legacy single-comp
                `setupWorkspace` path with the hardcoded
                `[DIMENSION] <preset>` name. When present (Stage D+),
                Babysitter creates N target-dimensioned mirror comps
                in `target_bin_path` and uses the resolver-resolved
                `output_name` from each entry.
            target_bin_path:    Slot 12.5 Stage D Item 2 — folder path
                (e.g. `"From Dimensions/TIKTOK_2026-05-17_120000"`)
                where the mirror tree lands. Babysitter creates the
                folder if absent and parents every mirror comp under
                it. Empty string falls back to the legacy `"From
                Dimensions"` folder (single-comp path).
            panel_slicing_plan: Issue #346 (narrow slice) — the
                `PanelSlicingPlan` (as a dict) for multi-panel OOH
                targets (transit triptychs), from
                `stages.conform.plan_panel_slicing`. None for every
                target except the 2 catalog entries carrying a
                `multi_panel` metadata spec. No Babysitter/JSX code
                reads this manifest key yet — see that function's
                docstring.

        Returns:
            Absolute path to chunk_manifest.json (passed to SovereignLauncher).
        """
        # When a mirror_tree is present, filter to only layers from comps in
        # that tree. GUIDE/PROTECT precomps are excluded from the mirror tree
        # by build_mirror_tree_spec — their inner layers must not appear in
        # the chunks or Babysitter would fall back to the root output comp and
        # inject them into the wrong layers (e.g. CheckersAndMattes tick marks
        # written onto content layers). Legacy path (mirror_tree=None): all
        # layers are used unchanged (single-comp, no containing_comp_id).
        if mirror_tree:
            _mirror_comp_ids = {
                e.source_comp_id for e in mirror_tree if e.source_comp_id is not None
            }
            if _mirror_comp_ids:
                conformed_layers_data = [
                    l for l in conformed_layers_data
                    if l.get("containing_comp_id") in _mirror_comp_ids
                ]

        total_layers = len(conformed_layers_data)

        if total_layers == 0:
            log.error("No layers to export — cannot produce chunks from empty layer list")
            raise ValueError("PayloadSlicer: 0 layers passed to slice_and_export()")

        # Validate every layer through Pydantic before touching disk
        validated_layers: List[ConformedLayer] = []
        for i, raw_layer in enumerate(conformed_layers_data):
            try:
                validated_layers.append(ConformedLayer.model_validate(raw_layer))
            except Exception as e:
                layer_name = raw_layer.get("name", "?") if isinstance(raw_layer, dict) else "?"
                log.error(
                    "Layer validation failed before slicing",
                    extra={"layer_index": i, "layer_name": layer_name, "error": str(e)},
                )
                raise ValueError(f"Layer validation failed: {layer_name} (index {i}): {e}")

        chunk_size_env = os.environ.get("DIMENSION_CHUNK_SIZE")
        if chunk_size_env is not None:
            chunk_size = _clamp_chunk_size(chunk_size_env, default=MAX_CHUNK_SIZE)
            chunk_layer_groups: List[List[ConformedLayer]] = []
            for i in range(0, total_layers, chunk_size):
                chunk_layer_groups.append(validated_layers[i : i + chunk_size])
        else:
            chunk_layer_groups = _partition_layers_adaptively(
                validated_layers,
                max_chunk_size=MAX_CHUNK_SIZE,
                target_weight=30.0,
            )

        total_chunks = len(chunk_layer_groups)

        # Build ChunkManifest objects — Pydantic enforces max_length=30 here
        chunk_objects: List[ChunkManifest] = []
        for i, group in enumerate(chunk_layer_groups):
            chunk_objects.append(ChunkManifest(
                chunk_index=i,
                layers=group,
            ))

        log.info("Payload sliced", extra={"total_layers": total_layers, "total_chunks": total_chunks})

        # Write each chunk atomically in parallel with preserved index order
        chunk_paths: List[str] = [None] * total_chunks  # type: ignore
        
        def write_chunk(chunk: ChunkManifest, index: int) -> str:
            chunk_dict = chunk.model_dump(exclude=_CHUNK_EXCLUDE)
            chunk_dict["totalChunks"] = total_chunks
            path = os.path.abspath(os.path.join(self.output_dir, f"chunk_{str(index).zfill(3)}.json"))
            atomic_write_json(path, chunk_dict, indent=None)
            log.debug(
                "slice.chunk.write",
                extra={
                    "chunk_index": index,
                    "path": path,
                    "layers_in_chunk": len(chunk.layers),
                },
            )
            return path

        with ThreadPoolExecutor(max_workers=os.cpu_count() or 4) as executor:
            future_to_chunk = {executor.submit(write_chunk, chunk, i): i for i, chunk in enumerate(chunk_objects)}
            for future in as_completed(future_to_chunk):
                chunk_index = future_to_chunk[future]
                try:
                    chunk_paths[chunk_index] = future.result()
                except Exception as e:
                    log.error("Chunk write failed", extra={"chunk": chunk_index, "error": str(e)})
                    raise IOError(f"Chunk {chunk_index} write failed: {e}")

        if not chunk_paths or any(cp is None for cp in chunk_paths):
            raise IOError("No chunks were written — cannot produce manifest")

        # Write master index atomically — Babysitter reads this first.
        # expected_comp_name flows through to the JSX side so it can verify
        # comp identity before duplicating; see Babysitter.setupWorkspace.
        # Emit the property registry as a sibling JSON so ExtendScript
        # consumers (Babysitter, Sovereign_Core) can read the same table
        # Python dispatches on. Keeps scraper / injector / engines in lockstep
        # without a second source of truth.
        from core import property_registry
        registry_path = os.path.abspath(
            os.path.join(self.output_dir, "property_registry.json"))
        atomic_write_json(registry_path, {
            "version": 1,
            "properties": property_registry.to_json(),
        })

        # Calculate composition structural state hash for pre-flight QC.
        # conformed_layers_data is already filtered to mirror-tree comps above,
        # so the hash covers exactly the same comps that Babysitter.verifyStateHash
        # will check at inject time.
        state_hash = calculate_state_hash(conformed_layers_data)

        manifest_path = os.path.abspath("chunk_manifest.json")
        manifest_data = {
            "status": "ok",
            "state_hash": state_hash,
            "allow_state_hash_bypass": allow_state_hash_bypass,
            # Issue #348/#251 (narrow slice, 2026-09-03): opt-in, default
            # None -- calls the already-built, already-defensive
            # Babysitter.configureRenderQueueItem (Babysitter_src/
            # 95_render_queue.jsx) when present. Expected keys (all
            # optional): template_name, output_path, file_extension,
            # disable_audio. No current caller sets this, so behavior is
            # unchanged for every existing inject. Which preset fields
            # should drive this automatically (audio_allowed,
            # delivery_codec -- neither exists on the Target schema yet)
            # is a product/schema decision left for a follow-up, same
            # pattern as #338's deferred CEP toggle.
            "render_queue_options": render_queue_options,
            # Issue #338 (narrow slice, 2026-09-03): opt-in, default OFF --
            # same "computed capability, not yet default-live" pattern as
            # #329's DIMENSION_SOE_KINEMATIC_AWARE. Wired through to
            # Babysitter.jsx's guide-layer injector; no current caller sets
            # this True yet, so behavior is unchanged for every existing
            # inject until something (a future CEP toggle) turns it on.
            "inject_safe_zone_guide": inject_safe_zone_guide,
            # Issue #346 (narrow slice, 2026-09-07): opt-in, default None --
            # PanelSlicingPlan data contract for multi-panel OOH targets
            # (transit triptychs). Only 2 of 265 catalog targets carry a
            # `multi_panel` metadata spec (see stages.conform.plan_panel_slicing's
            # gating), so this is absent for every other conform. No
            # Babysitter/JSX code reads this key yet -- the JSX comp-creation
            # half (1 Master Comp + N cropping children) is separate,
            # deliberately unwired work pending live-AE verification, per
            # core/panel_slicer.py's own docstring. Presence of this field
            # cannot change any AE behavior until that JSX side is built.
            "panel_slicing_plan": panel_slicing_plan,
            "inject_chunk_size": MAX_CHUNK_SIZE,
            "total_layers": total_layers,
            "total_chunks": total_chunks,
            "chunk_paths": chunk_paths,
            "expected_comp_name": expected_comp_name,
            "property_registry": registry_path,
        }
        if target_width > 0 and target_height > 0:
            manifest_data["target_width"] = target_width
            manifest_data["target_height"] = target_height
        if preset_label:
            manifest_data["preset_label"] = preset_label
        # B2 — emit the resolved output comp name for the legacy
        # single-comp path. When Babysitter has no mirror_tree, it
        # reads this field for the output comp name. Falls back to
        # "[DIMENSION] <preset>" when absent (pre-B2 manifests).
        if expected_comp_name and not mirror_tree:
            try:
                from core.output_naming import (
                    DEFAULT_OUTPUT_NAME_TEMPLATE,
                    resolve_output_name,
                )
                # Audit fix (2026-08-27) — this was a hardcoded empty
                # set, so the resolver's _v2/_v3 collision bump could
                # never fire: re-conforming the same source comp to
                # the same preset always resolved to the identical
                # name. Copy the caller's set (not reserve into it —
                # this is the only resolution happening in this call).
                _existing = set(existing_comp_names) if existing_comp_names else set()
                _resolved = resolve_output_name(
                    expected_comp_name,
                    _existing,
                    template=output_name_template or DEFAULT_OUTPUT_NAME_TEMPLATE,
                    preset_id=preset_label,
                    target_width=target_width,
                    target_height=target_height,
                )
                manifest_data["output_comp_name"] = _resolved.name
            except Exception:
                pass  # Fall through — Babysitter uses legacy naming
        # Slot 12.5 Stage D Item 2 — emit the mirror-tree spec when
        # present. Babysitter branches on its presence: present →
        # N-comp setupWorkspace; absent → legacy single-comp path.
        # Both target_bin_path and mirror_tree are optional for back-
        # compat with pre-Stage-D code paths that don't know about
        # them.
        if target_bin_path:
            manifest_data["target_bin_path"] = target_bin_path
        if mirror_tree:
            manifest_data["mirror_tree"] = [
                entry.model_dump() for entry in mirror_tree
            ]
        # Slot 12.5 Stage D Item 3 — wrapper rewires for the mirror
        # tree. Each entry tells Babysitter to repoint one precomp
        # wrapper from its original source comp to the corresponding
        # target-dimensioned mirror copy. Empty list / None → omit
        # the key; Babysitter's rewire phase no-ops on absence
        # (87N flat regression case — no precomp wrappers exist).
        if mirror_rewires:
            manifest_data["mirror_rewires"] = [
                entry.model_dump() for entry in mirror_rewires
            ]
        atomic_write_json(manifest_path, manifest_data)

        return manifest_path
