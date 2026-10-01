# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/logic/comp_cleaner_adapter.py
Issue #350 -- adapts the REAL `ProjectStructure` shape (comps +
top-level `references` edge list, `is_render_target`) into the legacy
shape `core/comp_cleaner.py::CompCleaner.analyze_project()` expects
(items with inline `layers[].source_comp_id`, `is_render_queued`).

Deliberately an adapter, not a rewrite of `comp_cleaner.py` itself --
per the explicit decision on issue #350 to adapt in the CLI layer
rather than change comp_cleaner's own input contract.

Why this exists: `comp_cleaner.py` has never actually been run against
a real `project_structure.json` -- its own tests and Pillar 63 both
hand-build the legacy shape by hand (the exact "trusting synthetic
Python test fixtures to prove a JSX<->Python integration contract"
anti-pattern CLAUDE.md documents). Wiring it directly against the real
scanner output without this adapter would leave every comp's
`ref_counts` at zero, since the real reference data lives in a
separate top-level list the legacy shape has no place for -- silently
flagging every Dimension-duplicate-named comp as an orphan, including
ones actively referenced by other comps. This module exists
specifically to close that gap.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List

from models.project_structure import ProjectStructure


def adapt_project_structure_for_comp_cleaner(ps: ProjectStructure) -> Dict[str, Any]:
    """Flatten a real ProjectStructure into comp_cleaner.py's legacy
    `{"items": [...]}` shape.

    Each real `CompReference` (`from_comp_id` has a layer whose source
    is `to_comp_id`) becomes one synthetic layer entry on the `from`
    comp's `layers` list, carrying `source_comp_id` -- exactly the
    field `analyze_project()` reads to build its reference-count graph.
    `is_render_target` maps directly to `is_render_queued`.
    """
    refs_by_from_comp: Dict[int, List[Any]] = defaultdict(list)
    for ref in ps.references:
        refs_by_from_comp[ref.from_comp_id].append(ref)

    items: List[Dict[str, Any]] = []
    for comp in ps.comps:
        layers = [
            {
                "source_comp_id": ref.to_comp_id,
                "name": ref.layer_name,
            }
            for ref in refs_by_from_comp.get(comp.id, [])
        ]
        items.append({
            "id": comp.id,
            "name": comp.name,
            "width": comp.width,
            "height": comp.height,
            "duration": comp.duration if comp.duration is not None else 10.0,
            "is_render_queued": comp.is_render_target,
            "layers": layers,
        })

    return {"items": items}
