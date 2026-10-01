# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
Dimension pipeline stages — survey → tag → conform → duplication → inject.

Each stage is importable independently.  `orchestrator.py` dispatches
conform for headless CLI; CEP / dimension_server import stages directly.
"""

from stages.batch import BatchTarget, run_batch_conform
from stages.conform import (
    ConformConfig,
    ConformError,
    ConformResult,
    SourceNotFoundError,
    build_layer_payloads,
    emit_run_warning,
    run_conform,
    validate_no_nan_or_inf,
)
from stages.conform_passes import MirrorTreeSpec, apply_lerp_pass, apply_soe_pass, build_mirror_tree_spec
from stages.duplication import compute_duplication_plan, write_duplication_plan
from stages.inject import (
    INJECT_PHASE_PROGRESS,
    InjectResult,
    dispatch_file_bridge_inject,
    load_monolithic_manifest,
    process_inject_logs,
)
from stages.survey import resolve_studio_profile, run_survey
from stages.tag import load_unit_overrides, save_unit_overrides, unit_overrides_path

__all__ = [
    "BatchTarget",
    "ConformConfig",
    "ConformError",
    "ConformResult",
    "INJECT_PHASE_PROGRESS",
    "InjectResult",
    "MirrorTreeSpec",
    "SourceNotFoundError",
    "apply_lerp_pass",
    "apply_soe_pass",
    "build_layer_payloads",
    "build_mirror_tree_spec",
    "compute_duplication_plan",
    "dispatch_file_bridge_inject",
    "emit_run_warning",
    "load_monolithic_manifest",
    "load_unit_overrides",
    "process_inject_logs",
    "resolve_studio_profile",
    "run_batch_conform",
    "run_conform",
    "run_survey",
    "save_unit_overrides",
    "unit_overrides_path",
    "validate_no_nan_or_inf",
    "write_duplication_plan",
]