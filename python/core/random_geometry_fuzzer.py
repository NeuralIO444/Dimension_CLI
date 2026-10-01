# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/core/random_geometry_fuzzer.py
Phase 5 of the autonomous-engineering initiative (2026-09-02) --
dependency-free property-based fuzzing.

Researched `hypothesis` first (the obvious choice) before building this:
CLAUDE.md requires explicit approval for any new dependency, even a
test-only one in requirements-ci.txt, and a stdlib-`random`-based fuzzer
with real shrinking covers this codebase's actual need -- bounded numeric
geometry parameters (scale, rotation, depth, layer counts), not the general
arbitrary-Python-object generation Hypothesis's strategy system is built
for. What we give up: Hypothesis's coverage-guided generation and its more
sophisticated shrinking heuristics. What we keep: real random exploration,
real shrinking to a minimal reproducer, and a persistent failure corpus --
the three things that actually matter for this domain.

Reuses Phase 4's infrastructure rather than duplicating it:
  - conform_invariant_checks.check_all -- the same universal invariant
    battery (finiteness, layer-count preservation, sealed-unit atomicity).
    A fuzz case is held to exactly the same bar as a hand-written or
    dynamically-generated one -- never an invented expected value.
  - degenerate_geometry_generators's extreme-value constants, as the
    RANGE bounds random generation samples within (not fixed discrete
    points like Phase 4 -- this is the actual "fuzzing" part: continuous
    random exploration between and beyond those fixed points).

Every run logs its seed. A failure is always reproducible with
`--seed <N>`. Confirmed failures persist to a JSONL corpus
(.dimension/fuzz_corpus/failures.jsonl, gitignored -- same "working
memory, not a permanent artifact" model as telemetry_corpus.py) and are
always replayed first on the next run, before spending budget on new
random exploration -- so a fixed bug never silently un-regresses.
"""

from __future__ import annotations

import json
import os
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from core.conform_invariant_checks import InvariantReport, check_all
from core.scale_engine import ScaleEngine
from models.scrape_manifest import CameraProperties, LayerModel, ProjectInfo, ScrapeManifest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_CORPUS = _REPO_ROOT / ".dimension" / "fuzz_corpus" / "failures.jsonl"

_PROJECT = ProjectInfo(width=1920, height=1080, frame_rate=24.0, duration=10.0, name="fuzz")

# Sampling ranges -- deliberately wider than degenerate_geometry_generators.py's
# fixed points (that module's constants are the boundary VALUES this module
# samples between and around), matching the same extremes this codebase's
# hand-written Hall of Horrors tests already probe individually.
_SCALE_RANGE = (0.0, 1e5)
_ROTATION_RANGE = (0.0, 360.0)
_DEPTH_RANGE = (-1e6, 1e6)
_TARGET_DIM_RANGE = (100, 20000)
_MEMBER_COUNT_RANGE = (1, 25)

_SHAPES = ("single_layer", "camera_scene", "sealed_precomp")


@dataclass
class FuzzCase:
    seed: int
    shape: str
    params: Dict[str, Any]
    manifest: ScrapeManifest
    conform_kwargs: Dict[str, Any]

    def to_repro_dict(self) -> Dict[str, Any]:
        return {"seed": self.seed, "shape": self.shape, "params": self.params, "conform_kwargs": self.conform_kwargs}


@dataclass
class FuzzFailure:
    case: FuzzCase
    report: InvariantReport
    shrunk_from_iterations: int = 0


@dataclass
class FuzzCampaignResult:
    iterations_run: int
    failures: List[FuzzFailure] = field(default_factory=list)
    replayed_corpus_failures: int = 0

    @property
    def ok(self) -> bool:
        return not self.failures


def _rand_float(rng: random.Random, lo: float, hi: float) -> float:
    return rng.uniform(lo, hi)


def _rand_target_dims(rng: random.Random) -> Tuple[int, int]:
    return (rng.randint(*_TARGET_DIM_RANGE), rng.randint(*_TARGET_DIM_RANGE))


def _build_single_layer(rng: random.Random) -> Tuple[Dict[str, Any], ScrapeManifest, Dict[str, Any]]:
    sx = _rand_float(rng, *_SCALE_RANGE)
    sy = _rand_float(rng, *_SCALE_RANGE)
    rot = _rand_float(rng, *_ROTATION_RANGE)
    tw, th = _rand_target_dims(rng)
    params = {"scale_x": sx, "scale_y": sy, "rotation": rot, "target_w": tw, "target_h": th}
    manifest = ScrapeManifest(status="OK", project_info=_PROJECT, layers=[
        LayerModel(
            index=1, name="FuzzSingle", layer_kind="av",
            position=[960.0, 540.0, 0.0], scale=[sx, sy, 100.0], rotation_z=rot,
            is_root=True,
        ),
    ])
    conform_kwargs = dict(target_width=tw, target_height=th, scale_mode=rng.choice(["Fit", "Fill", "Auto"]), bleed_pct=rng.choice([0.0, 0.05, 0.25]), layout="tags")
    return params, manifest, conform_kwargs


def _build_camera_scene(rng: random.Random) -> Tuple[Dict[str, Any], ScrapeManifest, Dict[str, Any]]:
    n_members = rng.randint(*_MEMBER_COUNT_RANGE)
    pitch = _rand_float(rng, *_ROTATION_RANGE)
    depths = [_rand_float(rng, *_DEPTH_RANGE) for _ in range(n_members)]
    tw, th = _rand_target_dims(rng)
    params = {"n_members": n_members, "pitch": pitch, "depths": depths, "target_w": tw, "target_h": th}

    layers = [
        LayerModel(
            index=1, name="Camera", layer_kind="camera",
            position=[960.0, 540.0, -1500.0], rotation_x=pitch, orientation=[pitch, 0.0, 0.0],
            camera=CameraProperties(zoom=1500.0, focusDistance=1500.0, pointOfInterest=[960.0, 540.0, 0.0]),
            is_root=True,
        ),
    ]
    for i, depth in enumerate(depths):
        layers.append(
            LayerModel(
                index=2 + i, name=f"Content3D_{i}", layer_kind="av",
                position=[960.0, 540.0, depth], scale=[100.0, 100.0, 100.0], threeD=True,
                is_root=True,
            )
        )
    manifest = ScrapeManifest(status="OK", project_info=_PROJECT, layers=layers)
    conform_kwargs = dict(target_width=tw, target_height=th, scale_mode=rng.choice(["Fit", "Fill", "Auto"]), bleed_pct=rng.choice([0.0, 0.05, 0.25]), layout="auto")
    return params, manifest, conform_kwargs


def _build_sealed_precomp(rng: random.Random) -> Tuple[Dict[str, Any], ScrapeManifest, Dict[str, Any]]:
    depth = rng.randint(1, 5)
    inner_scales = [_rand_float(rng, *_SCALE_RANGE) for _ in range(depth)]
    tw, th = _rand_target_dims(rng)
    params = {"depth": depth, "inner_scales": inner_scales, "target_w": tw, "target_h": th}

    layers = [
        LayerModel(index=1, name="RootWrapper", layer_kind="av", position=[960.0, 540.0, 0.0], scale=[100.0, 100.0, 100.0], is_root=True, containing_comp_id=0),
    ]
    for level, s in enumerate(inner_scales):
        layers.append(
            LayerModel(
                index=1, name=f"Nested_{level}", layer_kind="av",
                position=[960.0, 540.0, 0.0], scale=[s, s, 100.0],
                is_root=False, containing_comp_id=level + 1,
            )
        )
    manifest = ScrapeManifest(status="OK", project_info=_PROJECT, layers=layers)
    conform_kwargs = dict(target_width=tw, target_height=th, scale_mode="Auto", bleed_pct=0.0, layout="auto")
    return params, manifest, conform_kwargs


_BUILDERS: Dict[str, Callable] = {
    "single_layer": _build_single_layer,
    "camera_scene": _build_camera_scene,
    "sealed_precomp": _build_sealed_precomp,
}


def generate_case(seed: int) -> FuzzCase:
    rng = random.Random(seed)
    shape = rng.choice(_SHAPES)
    params, manifest, conform_kwargs = _BUILDERS[shape](rng)
    return FuzzCase(seed=seed, shape=shape, params=params, manifest=manifest, conform_kwargs=conform_kwargs)


def run_case(case: FuzzCase) -> InvariantReport:
    engine = ScaleEngine(case.manifest, **case.conform_kwargs)
    result = engine.conform()
    return check_all(case.manifest, engine, result, f"fuzz_seed{case.seed}_{case.shape}")


def _rebuild_with_params(shape: str, params: Dict[str, Any], conform_kwargs: Dict[str, Any]) -> ScrapeManifest:
    """Reconstructs a manifest from an already-shrunk params dict, bypassing
    the RNG (shrinking mutates params directly, not the seed)."""
    if shape == "single_layer":
        return ScrapeManifest(status="OK", project_info=_PROJECT, layers=[
            LayerModel(index=1, name="FuzzSingle", layer_kind="av", position=[960.0, 540.0, 0.0],
                       scale=[params["scale_x"], params["scale_y"], 100.0], rotation_z=params["rotation"], is_root=True),
        ])
    if shape == "camera_scene":
        layers = [
            LayerModel(index=1, name="Camera", layer_kind="camera", position=[960.0, 540.0, -1500.0],
                       rotation_x=params["pitch"], orientation=[params["pitch"], 0.0, 0.0],
                       camera=CameraProperties(zoom=1500.0, focusDistance=1500.0, pointOfInterest=[960.0, 540.0, 0.0]),
                       is_root=True),
        ]
        for i, depth in enumerate(params["depths"]):
            layers.append(LayerModel(index=2 + i, name=f"Content3D_{i}", layer_kind="av",
                                      position=[960.0, 540.0, depth], scale=[100.0, 100.0, 100.0], threeD=True, is_root=True))
        return ScrapeManifest(status="OK", project_info=_PROJECT, layers=layers)
    if shape == "sealed_precomp":
        layers = [LayerModel(index=1, name="RootWrapper", layer_kind="av", position=[960.0, 540.0, 0.0],
                              scale=[100.0, 100.0, 100.0], is_root=True, containing_comp_id=0)]
        for level, s in enumerate(params["inner_scales"]):
            layers.append(LayerModel(index=1, name=f"Nested_{level}", layer_kind="av", position=[960.0, 540.0, 0.0],
                                      scale=[s, s, 100.0], is_root=False, containing_comp_id=level + 1))
        return ScrapeManifest(status="OK", project_info=_PROJECT, layers=layers)
    raise ValueError(f"unknown shape: {shape}")


def _still_fails(shape: str, params: Dict[str, Any], conform_kwargs: Dict[str, Any]) -> bool:
    manifest = _rebuild_with_params(shape, params, conform_kwargs)
    engine = ScaleEngine(manifest, **conform_kwargs)
    result = engine.conform()
    report = check_all(manifest, engine, result, "shrink-probe")
    return not report.ok


def shrink(case: FuzzCase, max_steps: int = 200) -> Tuple[FuzzCase, int]:
    """Greedy shrink: repeatedly try to simplify numeric params toward 0
    (or toward the nearest 'nice' round value) and toward fewer list
    elements, keeping any change that still reproduces the failure.
    Not as thorough as Hypothesis's shrinker, but turns "seed 8472083,
    shape camera_scene, 19 random members" into something a human can
    actually read and promote into a permanent test."""
    params = dict(case.params)
    shape = case.shape
    conform_kwargs = case.conform_kwargs
    steps = 0

    def try_set(key: str, value: Any) -> bool:
        nonlocal steps
        if steps >= max_steps:
            return False
        old = params.get(key)
        params[key] = value
        steps += 1
        if _still_fails(shape, params, conform_kwargs):
            return True
        params[key] = old
        return False

    # Shrink scalar numeric fields toward zero via repeated halving.
    for key, value in list(params.items()):
        if isinstance(value, float):
            current = value
            while steps < max_steps:
                candidate = current / 2.0
                if abs(candidate) < 1e-9:
                    break
                if try_set(key, candidate):
                    current = candidate
                else:
                    break
        elif isinstance(value, list) and value and isinstance(value[0], (int, float)):
            # Shrink each list element toward zero, and try dropping
            # elements from the end (fewer members is a simpler repro).
            shrunk_list = list(value)
            changed = True
            while changed and steps < max_steps:
                changed = False
                if len(shrunk_list) > 1:
                    candidate = shrunk_list[:-1]
                    if try_set(key, candidate):
                        shrunk_list = candidate
                        changed = True
                        continue
                for i in range(len(shrunk_list)):
                    v = shrunk_list[i]
                    if not isinstance(v, (int, float)) or abs(v) < 1e-9:
                        continue
                    candidate = list(shrunk_list)
                    candidate[i] = v / 2.0
                    if try_set(key, candidate):
                        shrunk_list = candidate
                        changed = True

    manifest = _rebuild_with_params(shape, params, conform_kwargs)
    shrunk_case = FuzzCase(seed=case.seed, shape=shape, params=params, manifest=manifest, conform_kwargs=conform_kwargs)
    return shrunk_case, steps


def _load_corpus(path: Path) -> List[Dict[str, Any]]:
    if not path.is_file():
        return []
    entries = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except ValueError:
                continue
    return entries


def _append_corpus(path: Path, entry: Dict[str, Any]) -> None:
    os.makedirs(path.parent, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, default=str) + "\n")


def run_campaign(
    iterations: int,
    seed: Optional[int] = None,
    corpus_path: Optional[Path] = None,
    replay_corpus: bool = True,
) -> FuzzCampaignResult:
    corpus_path = corpus_path or _DEFAULT_CORPUS
    master_rng = random.Random(seed)
    result = FuzzCampaignResult(iterations_run=0)

    if replay_corpus:
        for entry in _load_corpus(corpus_path):
            manifest = _rebuild_with_params(entry["shape"], entry["params"], entry["conform_kwargs"])
            case = FuzzCase(seed=entry["seed"], shape=entry["shape"], params=entry["params"],
                             manifest=manifest, conform_kwargs=entry["conform_kwargs"])
            report = run_case(case)
            result.replayed_corpus_failures += 1
            if not report.ok:
                result.failures.append(FuzzFailure(case=case, report=report))

    for _ in range(iterations):
        case_seed = master_rng.randrange(0, 2**31)
        case = generate_case(case_seed)
        report = run_case(case)
        result.iterations_run += 1
        if not report.ok:
            shrunk_case, shrink_steps = shrink(case)
            shrunk_report = run_case(shrunk_case)
            failure = FuzzFailure(case=shrunk_case, report=shrunk_report, shrunk_from_iterations=shrink_steps)
            result.failures.append(failure)
            _append_corpus(corpus_path, shrunk_case.to_repro_dict())

    return result
