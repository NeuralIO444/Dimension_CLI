# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/models/bridge_contract.py
Canonical import surface for the Python ↔ JSX bridge contract.

Re-exports bridge job schemas (Layer 1) and JSX wire-manifest parsers
(Layer 2 scrape validation). Import from here in production code and
contract tests rather than reaching into bridge_jobs / jsx_wire_manifest
directly.
"""

from __future__ import annotations

from models.duplication_plan import DuplicationPlan  # noqa: F401 — re-export
from models.bridge_jobs import (  # noqa: F401 — re-export
    BRIDGE_SCHEMA_VERSION,
    BridgeDuplicatePlanResult,
    BridgeMaskToggleResult,
    BridgePanelSlicingPlanResult,
    BridgeReconPlanResult,
    BridgeResult,
    BridgeQueryLayerStateResult,
    BridgeSchemaVersionError,
    BridgeScrapeResult,
    BridgeSelectLayerResult,
    BridgeTagWriteResult,
    DuplicatePlanJob,
    InjectJob,
    MaskToggleJob,
    PanelSlicingPlanJob,
    QueryLayerStateJob,
    ReconPlanJob,
    ScrapeJob,
    SelectLayerJob,
    TagWriteJob,
    BridgeJob,
    parse_inject_job,
    parse_result,
    parse_typed_job,
)
from models.jsx_wire_manifest import (  # noqa: F401 — re-export
    CRITICAL_LAYER_WIRE_KEYS,
    JsxLayerWire,
    JsxScrapeManifestWire,
    ProjectInfoWire,
    RecursiveScrapeMetaWire,
    ValidationError,
    audit_critical_layer_keys,
    is_fresh_jsx_manifest,
    parse_jsx_wire_manifest,
)

# Alias preferred at receive boundaries
parse_bridge_result = parse_result


def __getattr__(name: str):
    """PEP 562 lazy re-export for chunk_contract's ConformedChunkLayer /
    BABYSITTER_READ_SET.

    chunk_contract.py imports conformed_manifest, which imports
    scrape_manifest, which imports THIS module (for DuplicationPlan) —
    so an eager top-level `from models.chunk_contract import ...` here
    would deadlock on a circular import the moment anything reaches
    this module through that chain. Resolving it lazily, only when a
    caller actually asks for one of these two names, sidesteps the
    cycle: by the time anyone accesses `bridge_contract.ConformedChunkLayer`,
    every module in the chain has already finished loading.
    """
    if name in ("ConformedChunkLayer", "BABYSITTER_READ_SET"):
        from models.chunk_contract import BABYSITTER_READ_SET, ConformedChunkLayer
        globals()["ConformedChunkLayer"] = ConformedChunkLayer
        globals()["BABYSITTER_READ_SET"] = BABYSITTER_READ_SET
        return globals()[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "BABYSITTER_READ_SET",
    "BRIDGE_SCHEMA_VERSION",
    "BridgeDuplicatePlanResult",
    "BridgeJob",
    "BridgeMaskToggleResult",
    "BridgePanelSlicingPlanResult",
    "BridgeReconPlanResult",
    "BridgeResult",
    "BridgeQueryLayerStateResult",
    "BridgeSchemaVersionError",
    "BridgeScrapeResult",
    "BridgeSelectLayerResult",
    "BridgeTagWriteResult",
    "ConformedChunkLayer",
    "CRITICAL_LAYER_WIRE_KEYS",
    "DuplicatePlanJob",
    "DuplicationPlan",
    "InjectJob",
    "JsxLayerWire",
    "JsxScrapeManifestWire",
    "MaskToggleJob",
    "PanelSlicingPlanJob",
    "ProjectInfoWire",
    "QueryLayerStateJob",
    "ReconPlanJob",
    "RecursiveScrapeMetaWire",
    "ScrapeJob",
    "SelectLayerJob",
    "TagWriteJob",
    "ValidationError",
    "audit_critical_layer_keys",
    "is_fresh_jsx_manifest",
    "parse_bridge_result",
    "parse_inject_job",
    "parse_jsx_wire_manifest",
    "parse_result",
    "parse_typed_job",
]