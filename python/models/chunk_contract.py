# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/models/chunk_contract.py
Babysitter chunk-layer read contract (test-side schema).

Formalizes, as a Pydantic model, the exact set of chunk-layer fields
Babysitter.jsx reads off `cLayer` at inject time. This does NOT
replace ConformedLayer/ChunkManifest as the production write-side
schema — python/logic/exporter.py is untouched by this module and
continues to serialize via ConformedLayer/ChunkManifest exactly as
before this file existed.

Why this exists: issue #44 (SOE silently no-op'd for a year) and the
V5_LAYER_KEYS allow-list trap both trace to the same root cause — a
field Babysitter reads had no schema anywhere asserting it survives
to the wire. ConformedLayer/ChunkManifest describe what the PRODUCER
may write; ConformedChunkLayer describes what the CONSUMER actually
reads, so a future `cLayer.<newField>` access added to Babysitter.jsx
without a matching model field fails a test immediately instead of
silently reading `undefined` for a year. See
docs/audits_2026/TechDebt_070726.md §2.2.

`extra="allow"` is deliberate: a real chunk layer is a full
ConformedLayer dict (minus CHUNK_STRIP_FIELDS) serialized to JSON —
this model asserts the presence/type of Babysitter's read-set, not
exhaustiveness of every ConformedLayer field that legitimately rides
along in the chunk.

Test-only. No production code path imports or validates against this
model; python/tests/test_chunk_payload_contract.py is the sole
consumer.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict

from .conformed_manifest import (
    ConformedEffect,
    ConformedKeys,
    ConformedLayerStyle,
    ConformedTransforms,
)

# The exact field set Babysitter.jsx reads off a chunk-layer object
# (`cLayer.<field>`), matching test_chunk_payload_contract.py's
# _babysitter_chunk_layer_reads() source-regex extraction. That
# function — not this constant — is the source of truth; if the two
# ever disagree, re-derive this set from the function rather than
# editing the function to match this list.
#
# Note "parent" is a regex false-positive, not a real Babysitter read:
# the extraction pattern `cLayer\.([A-Za-z_]\w*)` also matches inside
# `srcLayer.parent` (Babysitter.jsx:1049-1051) because the literal
# "srcLayer" contains "cLayer" as a substring. Babysitter does not
# actually access `cLayer.parent` anywhere. It's kept in this set
# (and declared below as an inert Optional field) to stay byte-exact
# with what the extraction function reports, per CLAUDE.md's
# "trusting synthetic test fixtures" anti-pattern — this module does
# not second-guess the extractor's output.
BABYSITTER_READ_SET = frozenset({
    "index",
    "name",
    "uid",
    "parent",
    "layer_kind",
    "containing_comp_id",
    "isBrittle",
    "conformed_transforms",
    "conformed_keys",
    "conformed_effects",
    "conformed_expressions",
    "conformed_layer_styles",
})


class ConformedChunkLayer(BaseModel):
    """One layer as Babysitter.jsx actually consumes it off a written
    chunk file.

    Required fields are the ones no inject path can proceed without
    (identity + the static transform every layer must carry).
    Everything else in BABYSITTER_READ_SET is Optional with a None
    default — Babysitter reads these defensively
    (`if (cLayer.conformed_keys) ...`) and a real chunk layer may
    legitimately omit any of them (e.g. a layer with no keyframes has
    no conformed_keys).
    """

    model_config = ConfigDict(extra="allow")

    # ── Required — no inject path can proceed without these ─────────
    index: int
    name: str
    layer_kind: str
    containing_comp_id: int
    conformed_transforms: ConformedTransforms

    # ── Optional — Babysitter reads these defensively ────────────────
    uid: Optional[str] = None

    # Regex false-positive from `srcLayer.parent` — see the
    # BABYSITTER_READ_SET comment above. Not a real ConformedLayer /
    # LayerModel field; declared here only so the read-set parity
    # test doesn't need a bespoke carve-out for one non-real field.
    parent: Optional[Any] = None

    isBrittle: Optional[bool] = None

    conformed_keys: Optional[ConformedKeys] = None
    conformed_effects: Optional[List[ConformedEffect]] = None
    conformed_layer_styles: Optional[List[ConformedLayerStyle]] = None

    # Populated by python/stages/expression.py as a raw dict attached
    # via ConformedLayer's extra="allow" config — never a declared
    # field on ConformedLayer itself, so there is no model type to
    # mirror there. Typed from the actual producer
    # (ExpressionScaler.scale() -> str per property name):
    # {property_name: scaled_expression_string}.
    conformed_expressions: Optional[Dict[str, str]] = None

    # PR-V3 — variant visibility. Read by
    # `Babysitter._processLayerVisibility` and asserted by the Auditor's
    # VARIANT_VISIBILITY_MISMATCH check.
    #
    # Tri-state on purpose: True = render, False = hide, **None = do not
    # touch this layer's video switch at all**. Every layer in a comp
    # with no `variant:` directives arrives as None, so collapsing this
    # to a plain `bool` with a default would make a conform silently
    # re-enable every layer the artist had switched off by hand.
    conformed_enabled: Optional[bool] = None
