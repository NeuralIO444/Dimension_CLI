# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_chunk_payload_contract.py — PERF-1 chunk-slimming contract (2026-07-04).

The exporter strips raw scrape-carryover fields (CHUNK_STRIP_FIELDS) from
chunk files because ExtendScript's JSON.parse is the inject hot path's
dominant cost: a 9-layer chunk carrying them measured 1.1 MB / 12.4 s to
parse (99.8% of the tick), against 5 ms of actual setValue work.

Both ends of the producer/consumer contract are enforced here (per the
CLAUDE.md anti-pattern about closing only one side):

  Producer (python/logic/exporter.py):
    - stripped fields must be absent from written chunk files
    - chunks must be written compact (no pretty-print indentation)
  Consumer (Scripts/Dimension_Assets/Babysitter.jsx):
    - every field Babysitter reads off a chunk layer (extracted by
      parsing the JSX source for `cLayer.<field>` accesses, so this
      list can't silently drift) must survive in the written chunk
    - no stripped field may ever appear in Babysitter's read-set

Uses the real PayloadSlicer end-to-end (write to disk, read back), not a
mocked serializer.
"""

from __future__ import annotations

import json
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from logic.exporter import CHUNK_STRIP_FIELDS, PayloadSlicer  # noqa: E402
from models.chunk_contract import ConformedChunkLayer  # noqa: E402
from models.conformed_manifest import ConformedLayer  # noqa: E402

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_BABYSITTER = os.path.join(_REPO_ROOT, "Scripts", "Dimension_Assets", "Babysitter.jsx")


def _babysitter_chunk_layer_reads() -> set:
    """Every `cLayer.<field>` access in Babysitter.jsx. cLayer is the
    injector's name for a chunk-layer object; this is the consumer's
    actual read-set, derived from source rather than hardcoded."""
    with open(_BABYSITTER, "r", encoding="utf-8", errors="replace") as f:
        src = f.read()
    return set(re.findall(r"cLayer\.([A-Za-z_][A-Za-z0-9_]*)", src))


def _sample_layer(**extra) -> dict:
    """Minimal valid ConformedLayer dict, optionally carrying the bloat
    fields a real conform emits."""
    base = {
        "index": 1,
        "name": "TT_Title",
        "layer_kind": "av",
        "containing_comp_id": 1,
        "conformed_transforms": {
            "is_root": True,
            "position": [540.0, 960.0, 0.0],
            "scale": [177.78, 177.78, 100.0],
            "rotation": 0.0,
            "anchor": [960.0, 540.0, 0.0],
        },
    }
    base.update(extra)
    return base


_BLOAT = {
    # shapes don't need to be schema-deep; Optional fields validate
    # loosely enough that empty containers stand in for the real 35 KB
    # payloads without weakening the presence/absence assertion
    "layer_styles": [],
    "properties": [],
    "effects": [],
    "flags": None,
}


@pytest.fixture()
def written_chunk(tmp_path) -> dict:
    """Run the real exporter on one layer carrying bloat fields; return
    the parsed on-disk chunk plus the raw file text."""
    slicer = PayloadSlicer(output_dir=str(tmp_path))
    slicer.slice_and_export(
        [_sample_layer(**_BLOAT)],
        expected_comp_name="TestComp",
        target_width=1080,
        target_height=1920,
        preset_label="TIKTOK",
    )
    chunk_path = tmp_path / "chunk_000.json"
    assert chunk_path.is_file(), "exporter did not write chunk_000.json"
    text = chunk_path.read_text(encoding="utf-8")
    return {"data": json.loads(text), "text": text}


class TestProducerSide:
    def test_stripped_fields_absent_from_chunk(self, written_chunk):
        for layer in written_chunk["data"]["layers"]:
            present = CHUNK_STRIP_FIELDS & set(layer.keys())
            assert not present, (
                f"chunk layer still carries stripped fields {present} — "
                "the PERF-1 slim regressed; ExtendScript parse cost returns."
            )

    def test_chunk_is_written_compact(self, written_chunk):
        assert "\n    " not in written_chunk["text"], (
            "chunk_000.json is pretty-printed — chunks must be compact "
            "(indent=None); indentation inflates ExtendScript parse time."
        )

    def test_strip_set_only_names_real_model_fields(self):
        """A typo'd/renamed field in CHUNK_STRIP_FIELDS would silently
        strip nothing. Fail loudly instead."""
        unknown = CHUNK_STRIP_FIELDS - set(ConformedLayer.model_fields)
        assert not unknown, (
            f"CHUNK_STRIP_FIELDS names unknown model fields: {unknown} — "
            "was a field renamed on LayerModel/ConformedLayer?"
        )


class TestConsumerSide:
    def test_babysitter_never_reads_a_stripped_field(self):
        reads = _babysitter_chunk_layer_reads()
        overlap = reads & CHUNK_STRIP_FIELDS
        assert not overlap, (
            f"Babysitter.jsx now reads {overlap} from chunk layers, but the "
            "exporter strips those fields — the injector would silently see "
            "undefined. Either remove the JSX read or remove the field from "
            "CHUNK_STRIP_FIELDS in exporter.py (and re-measure parse cost)."
        )

    def test_babysitter_read_set_survives_in_chunk(self, written_chunk):
        """Every chunk-layer field Babysitter reads that exists on the
        model must still be a key in the written chunk. Reads that were
        never model fields (e.g. runtime-attached ones) are out of scope."""
        reads = _babysitter_chunk_layer_reads()
        model_backed = reads & set(ConformedLayer.model_fields)
        assert model_backed, "parser found no model-backed cLayer reads — regex broken?"
        layer = written_chunk["data"]["layers"][0]
        missing = model_backed - set(layer.keys())
        assert not missing, (
            f"fields Babysitter reads are missing from the written chunk: "
            f"{missing} — the exporter's exclude set is over-stripping."
        )

    def test_conformed_payload_values_survive(self, written_chunk):
        layer = written_chunk["data"]["layers"][0]
        ct = layer["conformed_transforms"]
        assert ct["position"] == [540.0, 960.0, 0.0]
        assert ct["scale"] == [177.78, 177.78, 100.0]
        assert layer["name"] == "TT_Title"


class TestChunkContractModel:
    """ConformedChunkLayer formalizes Babysitter's chunk-layer read
    contract as a Pydantic model (test-side only — see
    python/models/chunk_contract.py). These tests are additive to the
    producer/consumer checks above, not a replacement: they make the
    read-set a schema instead of just a regex-derived set, so a new
    `cLayer.<field>` access in Babysitter.jsx fails loudly until the
    model is taught about it, and any layer the real exporter writes
    is proven to validate against that schema end-to-end."""

    def test_babysitter_read_set_is_declared_on_model(self):
        """Every field Babysitter actually reads off a chunk layer
        (re-derived from JSX source here, not hardcoded) must be a
        declared field on ConformedChunkLayer — required or Optional,
        it doesn't matter which, but it must be *known* to the model.
        A new `cLayer.<field>` read added to Babysitter.jsx that the
        model hasn't learned about fails this test immediately,
        instead of silently no-op'ing for a year (issue #44)."""
        reads = _babysitter_chunk_layer_reads()
        declared = set(ConformedChunkLayer.model_fields)
        missing = reads - declared
        assert not missing, (
            f"Babysitter.jsx reads {missing} off a chunk layer, but "
            "ConformedChunkLayer does not declare them — teach the "
            "model about the new field(s) in python/models/chunk_contract.py."
        )

    def test_written_chunk_layers_validate_against_contract(self, written_chunk):
        """Every layer in a chunk file written by the real PayloadSlicer
        (not a synthetic fixture — see CLAUDE.md's anti-pattern about
        trusting synthetic fixtures for a producer/consumer contract)
        must validate cleanly against ConformedChunkLayer."""
        layers = written_chunk["data"]["layers"]
        assert layers, "written chunk carried no layers to validate"
        for layer in layers:
            ConformedChunkLayer.model_validate(layer)
