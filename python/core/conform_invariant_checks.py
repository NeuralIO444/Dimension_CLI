# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/core/conform_invariant_checks.py
Shared, reusable invariant assertions for conform output -- the "universal
truths" every conform must satisfy regardless of input geometry. Deliberately
distinct from a specific-expected-value test: these checks never need to
know what the "correct" output is for a given input, only whether the math
stayed sane.

Used by:
  - python/tests/test_full_catalog_conform_sweep.py (Phase 3)
  - python/tools/pr_diff_harness.py (Phase 4) -- runs these same checks
    against dynamically-generated geometry, not hand-written fixtures, which
    is exactly why the checks can't assume anything about expected values.

The three checks:
  1. Finiteness -- no NaN/Inf anywhere in position/scale/camera transforms.
  2. Layer-count preservation -- nothing silently dropped or duplicated.
  3. Sealed/preserving-unit atomicity (THE INVARIANT, see CLAUDE.md's "Known
     sharp edges") -- every member of a sealed precomp or a preserving
     camera scene must scale by the IDENTICAL ratio (dst/src), even though
     members can have different starting source scales. This directly
     generalizes the manual verification done this session for the Z-depth
     Scene Preservation Guard bug (#365): camera Z scaled by 0.59x while
     content Z scaled by 1.87x in the same supposedly-atomic unit -- exactly
     what this check would have caught automatically.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class InvariantViolation:
    check: str
    label: str
    detail: str

    def __str__(self) -> str:
        return f"[{self.check}] {self.label}: {self.detail}"


@dataclass
class InvariantReport:
    label: str
    violations: List[InvariantViolation] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.violations

    def add(self, check: str, detail: str) -> None:
        self.violations.append(InvariantViolation(check, self.label, detail))


def _walk_finite(value: Any, path: str, report: InvariantReport) -> None:
    if isinstance(value, (list, tuple)):
        for i, v in enumerate(value):
            _walk_finite(v, f"{path}[{i}]", report)
        return
    if isinstance(value, dict):
        for k, v in value.items():
            _walk_finite(v, f"{path}.{k}", report)
        return
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if not math.isfinite(value):
            report.add("finite", f"non-finite value at {path}: {value!r}")


def check_finiteness(result: Dict[str, Any], label: str, report: Optional[InvariantReport] = None) -> InvariantReport:
    report = report or InvariantReport(label=label)
    for layer in result.get("layers", []):
        tf = layer.get("conformed_transforms")
        if not tf:
            continue
        name = layer.get("name", "?")
        for key in ("position", "scale", "anchor"):
            if key in tf:
                _walk_finite(tf[key], f"{name}.{key}", report)
        if tf.get("camera") is not None:
            _walk_finite(tf["camera"], f"{name}.camera", report)
        if tf.get("light") is not None:
            _walk_finite(tf["light"], f"{name}.light", report)
    return report


def check_layer_count_preserved(manifest, result: Dict[str, Any], label: str, report: Optional[InvariantReport] = None) -> InvariantReport:
    report = report or InvariantReport(label=label)
    expected = len(manifest.layers)
    actual = len(result.get("layers", []))
    if expected != actual:
        report.add(
            "layer_count",
            f"{expected} layers in, {actual} out -- a layer was silently dropped or duplicated",
        )
    return report


def _scale_ratio(dst_scale, src_scale, axis: int) -> Optional[float]:
    try:
        s_src = src_scale[axis]
        s_dst = dst_scale[axis]
    except (IndexError, TypeError):
        return None
    if not s_src or not math.isfinite(s_src) or s_src == 0:
        return None
    if not math.isfinite(s_dst):
        return None
    return s_dst / s_src


def check_sealed_unit_atomicity(
    engine, result: Dict[str, Any], label: str, report: Optional[InvariantReport] = None, tolerance: float = 1e-4,
) -> InvariantReport:
    """Every member of a camera_scene or precomp PlacementUnit (the atomic,
    ratio-identical kinds -- see CLAUDE.md's "Known sharp edges" on sealed
    units never scattering across passes) must scale by the same ratio.

    Deliberately keyed off the ACTUAL unit membership the engine computed
    (`engine.placement_units_report.units[].members`), not a coarse
    "same containing_comp_id" guess -- a comp can hold a camera-scene unit
    AND separate FILL/group/singleton units side by side (confirmed against
    the real 87N fixture: EFX is its own FILL-tagged singleton in the same
    comp as the 7-member camera scene, and correctly gets a DIFFERENT scale
    ratio -- an earlier version of this check that grouped by comp_id alone
    produced a false positive on exactly this fixture). "group" kind units
    are excluded on purpose -- they preserve relative *offset*, not an
    identical scale *ratio* (see the group-aware gravity fix in CLAUDE.md),
    a different invariant this check doesn't assert.

    Skips gracefully (no violation) when the engine never built a unit
    report -- absence of unit info is not itself a violation, just nothing
    to check."""
    report = report or InvariantReport(label=label)
    units_report = getattr(engine, "placement_units_report", None)
    if units_report is None:
        return report

    layers_by_key: Dict[Any, dict] = {}
    for layer in result.get("layers", []):
        key = (layer.get("containing_comp_id"), layer.get("index"))
        layers_by_key[key] = layer

    for unit in getattr(units_report, "units", []) or []:
        if getattr(unit, "kind", None) not in ("camera_scene", "precomp"):
            continue
        members = getattr(unit, "members", []) or []
        if len(members) < 2:
            continue

        ratios_by_axis: Dict[int, List[float]] = {0: [], 1: [], 2: []}
        # Z-position ratio, tracked separately from scale: unlike X/Y (which
        # center-remap around a target midpoint, so a raw dst/src ratio is
        # meaningless), Z position in this codebase is a pure multiplicative
        # scale from zero with no offset (`p_conformed[2] = p[2] * z_scale`,
        # confirmed in scale_engine_narrow.py) -- so the same ratio logic
        # applies. This is what would have caught #365 (the Z-depth Scene
        # Preservation Guard bug this session): scale was already correctly
        # uniform across that unit, only Z-position diverged (camera 0.59x,
        # content 1.87x) -- a scale-only check does not see that at all.
        pos_z_ratios: List[float] = []
        member_names: List[str] = []
        for m in members:
            layer = layers_by_key.get((m.comp_id, m.index))
            if layer is None:
                continue
            tf = layer.get("conformed_transforms")
            if not tf or tf.get("skip_inject"):
                continue
            member_names.append(layer.get("name", m.name or "?"))
            src_scale = layer.get("scale") or [100.0, 100.0, 100.0]
            dst_scale = tf.get("scale") or []
            for axis in (0, 1, 2):
                ratio = _scale_ratio(dst_scale, src_scale, axis)
                if ratio is not None:
                    ratios_by_axis[axis].append(ratio)
            src_pos = layer.get("position") or [0.0, 0.0, 0.0]
            dst_pos = tf.get("position") or []
            pz_ratio = _scale_ratio(dst_pos, src_pos, 2)
            if pz_ratio is not None:
                pos_z_ratios.append(pz_ratio)

        for axis, ratios in ratios_by_axis.items():
            if len(ratios) < 2:
                continue
            spread = max(ratios) - min(ratios)
            if spread > tolerance * max(abs(r) for r in ratios):
                report.add(
                    "sealed_unit_atomicity",
                    f"unit {unit.unit_id} ({unit.kind}) axis {axis}: scale ratios "
                    f"diverge across members {member_names} -- {ratios} "
                    f"(spread {spread:.6f})",
                )

        if len(pos_z_ratios) >= 2:
            spread = max(pos_z_ratios) - min(pos_z_ratios)
            if spread > tolerance * max(abs(r) for r in pos_z_ratios):
                report.add(
                    "sealed_unit_atomicity",
                    f"unit {unit.unit_id} ({unit.kind}) Z-position ratios diverge "
                    f"across members {member_names} -- {pos_z_ratios} "
                    f"(spread {spread:.6f})",
                )
    return report


def check_all(manifest, engine, result: Dict[str, Any], label: str) -> InvariantReport:
    report = InvariantReport(label=label)
    check_finiteness(result, label, report)
    check_layer_count_preserved(manifest, result, label, report)
    check_sealed_unit_atomicity(engine, result, label, report)
    return report
