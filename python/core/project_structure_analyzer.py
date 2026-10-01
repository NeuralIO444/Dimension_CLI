# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/core/project_structure_analyzer.py
v5.7 — Read-only graph queries over a ProjectStructure scan.

The analyzer answers questions about a project that the upcoming
duplication engine and the (deferred) Project Structure UI panel both
need:

  - Which comps reference which other comps?
  - Which comps are "roots" (render targets or unreferenced)?
  - Which precomps are shared by multiple parents — i.e. duplicating
    one consumer without forking the precomp would break the other?
  - How deep is each comp in the reference graph (0 = leaf)?
  - Are there any comp cycles? (AE refuses to create them, but a
    corrupt scan or future-version weirdness could surface one.)

Everything here is pure: load a ProjectStructure, instantiate the
analyzer, ask questions. No I/O, no side effects.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Set

from models.project_structure import ProjectStructure


class ProjectStructureAnalyzer:
    """Graph queries over a ProjectStructure scan."""

    def __init__(self, structure: ProjectStructure):
        self.structure = structure
        self._comps_by_id: Dict[int, "ProjectStructure.comps[0]"] = (
            structure.comps_by_id()
        )

        # Forward edges: comp_id → set of precomp ids it uses.
        self._forward: Dict[int, Set[int]] = {c.id: set() for c in structure.comps}
        # Reverse edges: comp_id → set of comps that reference it.
        self._reverse: Dict[int, Set[int]] = {c.id: set() for c in structure.comps}

        for ref in structure.references:
            # Defensive: skip edges that point at unknown comps. A
            # corrupt scan could reference a deleted comp by id; we
            # just drop those edges rather than crash.
            if ref.from_comp_id not in self._forward:
                continue
            if ref.to_comp_id not in self._comps_by_id:
                continue
            self._forward[ref.from_comp_id].add(ref.to_comp_id)
            self._reverse[ref.to_comp_id].add(ref.from_comp_id)

    # ── Basic accessors ───────────────────────────────────────────────

    @classmethod
    def from_file(cls, path: Path) -> "ProjectStructureAnalyzer":
        """Load + validate project_structure.json from disk."""
        raw = Path(path).read_text(encoding="utf-8")
        structure = ProjectStructure.model_validate(json.loads(raw))
        return cls(structure)

    @property
    def comp_count(self) -> int:
        return len(self.structure.comps)

    @property
    def reference_count(self) -> int:
        return len(self.structure.references)

    def comp(self, comp_id: int):
        return self._comps_by_id.get(comp_id)

    # ── Graph queries ─────────────────────────────────────────────────

    def precomps_of(self, comp_id: int) -> Set[int]:
        """Direct precomps used by `comp_id` (one-hop, no transitive)."""
        return set(self._forward.get(comp_id, set()))

    def parents_of(self, comp_id: int) -> Set[int]:
        """Direct parents that reference `comp_id` (one-hop)."""
        return set(self._reverse.get(comp_id, set()))

    def descendants(self, comp_id: int) -> Set[int]:
        """All transitive precomps reachable from `comp_id`. Iterative
        DFS to survive deep nesting; cycle-safe via visited set."""
        out: Set[int] = set()
        if comp_id not in self._forward:
            return out
        stack: List[int] = [comp_id]
        while stack:
            cur = stack.pop()
            for nxt in self._forward.get(cur, ()):
                if nxt in out:
                    continue
                out.add(nxt)
                stack.append(nxt)
        return out

    def ancestors(self, comp_id: int) -> Set[int]:
        """All transitive parents that reach `comp_id`."""
        out: Set[int] = set()
        if comp_id not in self._reverse:
            return out
        stack: List[int] = [comp_id]
        while stack:
            cur = stack.pop()
            for nxt in self._reverse.get(cur, ()):
                if nxt in out:
                    continue
                out.add(nxt)
                stack.append(nxt)
        return out

    # ── Roles / classification ────────────────────────────────────────

    def render_targets(self) -> List[int]:
        """Comp ids currently in the render queue."""
        return [c.id for c in self.structure.comps if c.is_render_target]

    def roots(self) -> List[int]:
        """Comps that nothing references (unused-from-above) PLUS comps
        in the render queue. These are the natural starting points for
        a top-down walk — duplications start from a root and the
        engine forks downward from there."""
        rooted: Set[int] = set()
        for c in self.structure.comps:
            if c.is_render_target or not self._reverse.get(c.id):
                rooted.add(c.id)
        return sorted(rooted)

    def orphans(self) -> List[int]:
        """Comps no one references AND that aren't render targets.
        Candidates for cleanup — but harmless to leave."""
        return sorted([
            c.id for c in self.structure.comps
            if not c.is_render_target and not self._reverse.get(c.id)
        ])

    def shared_precomps(self) -> List[int]:
        """Precomps referenced by ≥ 2 distinct parents. These are the
        ones the duplication engine MUST fork — modifying a shared
        precomp in place would mutate every consumer simultaneously.
        Sorted by parent count desc, then by id asc."""
        scored = [
            (len(self._reverse.get(c.id, set())), c.id)
            for c in self.structure.comps
        ]
        scored.sort(key=lambda t: (-t[0], t[1]))
        return [cid for (parent_count, cid) in scored if parent_count >= 2]

    # ── Depth + topology ──────────────────────────────────────────────

    def comp_depth(self, comp_id: int) -> int:
        """Longest path from `comp_id` down to any leaf. Leaves return
        0; a comp using one precomp returns 1; etc. Iterative + memoised
        so a 100-deep nest doesn't blow the stack and a wide DAG with
        diamonds doesn't get re-walked exponentially."""
        memo: Dict[int, int] = {}
        return self._depth_with_memo(comp_id, memo, on_stack=set())

    def max_depth(self) -> int:
        """Max comp_depth across the whole project. Useful for the UI
        panel's tree-view height calculation."""
        memo: Dict[int, int] = {}
        best = 0
        for c in self.structure.comps:
            d = self._depth_with_memo(c.id, memo, on_stack=set())
            if d > best:
                best = d
        return best

    def _depth_with_memo(
        self,
        comp_id: int,
        memo: Dict[int, int],
        on_stack: Set[int],
    ) -> int:
        if comp_id in memo:
            return memo[comp_id]
        if comp_id in on_stack:
            # Cycle — break and return 0 to keep the recursion finite.
            # Cycles also surface via cycles() so the caller can warn.
            return 0
        children = self._forward.get(comp_id, set())
        if not children:
            memo[comp_id] = 0
            return 0
        on_stack.add(comp_id)
        best = 0
        for ch in children:
            d = 1 + self._depth_with_memo(ch, memo, on_stack)
            if d > best:
                best = d
        on_stack.discard(comp_id)
        memo[comp_id] = best
        return best

    # ── Cycle detection ───────────────────────────────────────────────

    def cycles(self) -> List[List[int]]:
        """Find all simple cycles in the forward graph. AE doesn't
        normally allow comp cycles, but a corrupt project file or a
        malformed scan might produce one — the duplication engine
        must NOT walk into an infinite loop. Tarjan-lite SCC: any SCC
        with ≥ 2 nodes is a cycle, plus self-loops on single nodes."""
        cycles: List[List[int]] = []
        index_counter = [0]
        stack: List[int] = []
        on_stack: Set[int] = set()
        indices: Dict[int, int] = {}
        lowlinks: Dict[int, int] = {}

        def strongconnect(v: int):
            indices[v] = index_counter[0]
            lowlinks[v] = index_counter[0]
            index_counter[0] += 1
            stack.append(v)
            on_stack.add(v)

            for w in self._forward.get(v, ()):
                if w not in indices:
                    strongconnect(w)
                    lowlinks[v] = min(lowlinks[v], lowlinks[w])
                elif w in on_stack:
                    lowlinks[v] = min(lowlinks[v], indices[w])

            if lowlinks[v] == indices[v]:
                scc: List[int] = []
                while True:
                    w = stack.pop()
                    on_stack.discard(w)
                    scc.append(w)
                    if w == v:
                        break
                if len(scc) > 1:
                    cycles.append(sorted(scc))
                elif len(scc) == 1 and scc[0] in self._forward.get(scc[0], set()):
                    cycles.append(scc)

        for c in self.structure.comps:
            if c.id not in indices:
                strongconnect(c.id)
        return cycles

    # ── Aggregate report ──────────────────────────────────────────────

    def summary(self) -> Dict[str, object]:
        """One-shot diagnostic: shape of the project at a glance.
        Used by the Phase-2 UI panel and the v5.8 duplication preview."""
        return {
            "comp_count":       self.comp_count,
            "reference_count":  self.reference_count,
            "render_targets":   self.render_targets(),
            "roots":            self.roots(),
            "orphans":          self.orphans(),
            "shared_precomps":  self.shared_precomps(),
            "max_depth":        self.max_depth(),
            "cycles":           self.cycles(),
        }
