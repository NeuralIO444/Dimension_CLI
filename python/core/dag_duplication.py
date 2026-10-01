# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/core/dag_duplication.py
Next-Gen Relayout Architecture — Topological Precomp DAG Mirror Planner.

Upgrades precomp duplication planning from flat single-level list matching into
a full Directed Acyclic Graph (DAG) traversal. Compositions nested 2 to 5 levels
deep are classified (FLUID vs. SEALED), topologically ordered (bottom-up), and
duplicated with multi-level layer rewirings.

Key Capabilities:
  - Cycle detection using DFS with full cycle-path reporting.
  - Multi-level topological sorting (leaf precomps first).
  - Autonomous FLUID vs. SEALED mode classification.
  - Multi-level LayerRewire mapping across all parent-child hierarchy levels.
  - Collision-safe unique naming (_v2, _v3) and session bin organization.
"""

from __future__ import annotations

import uuid
from collections import defaultdict, deque
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional, Set, Tuple

from pydantic import BaseModel, Field

from core.duplication_hooks import DEFAULT_REGISTRY, HookRegistry
from core.output_naming import session_bin_path
from core.project_structure_analyzer import ProjectStructureAnalyzer
from models.duplication_plan import (
    DuplicationPlan,
    LayerRewire,
    PrecompDuplicate,
)
from models.project_structure import CompNode, ProjectStructure
from models.scrape_manifest import ScrapeManifest


# Aspect-ratio tolerance below which a conform is "uniform scale" (0.5%)
_ASPECT_TOLERANCE = 0.005


class DAGCycleError(Exception):
    """Raised when a circular reference loop is detected in the comp hierarchy."""

    def __init__(self, cycle_path: List[int], message: Optional[str] = None):
        self.cycle_path = cycle_path
        msg = message or f"Cyclic dependency detected in composition DAG: {' -> '.join(map(str, cycle_path))}"
        super().__init__(msg)


class PrecompConformMode(str, Enum):
    """Execution mode for conformed precomps."""
    FLUID = "fluid"    # Conformed into target aspect ratio; internal layers re-laid out
    SEALED = "sealed"  # Sourced at original aspect ratio; outer wrapper scaled in root comp


class DAGCompNode(BaseModel):
    """One node in the composition Directed Acyclic Graph."""

    comp_id: int
    name: str
    width: int
    height: int
    fps: Optional[float] = None
    duration: Optional[float] = None
    depth: int = 0
    mode: PrecompConformMode = PrecompConformMode.FLUID
    children_comp_ids: List[int] = Field(default_factory=list)
    parent_comp_ids: List[int] = Field(default_factory=list)
    has_3d_camera: bool = False
    has_raw_footage: bool = False
    is_protected: bool = False
    layer_count: int = 0
    in_degree: int = Field(default=0, description="Number of parent comps referencing this comp")
    out_degree: int = Field(default=0, description="Number of child precomps this comp nests")


class DAGDuplicationPlan(BaseModel):
    """Complete multi-level duplication plan for a nested composition hierarchy."""

    session_id: str
    session_folder: str
    preset_id: str
    target_dimensions: Tuple[int, int]
    aspect_ratio_changed: bool
    root_comp_id: int
    topological_order: List[int]
    nodes: Dict[int, DAGCompNode]
    duplicates: List[PrecompDuplicate] = Field(default_factory=list)
    rewires: List[LayerRewire] = Field(default_factory=list)
    depth_max: int = 0
    warnings: List[str] = Field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        """True if no precomps require duplication."""
        return not self.duplicates

    def active_duplicates(self) -> List[PrecompDuplicate]:
        """Filtered list of duplicates that will be executed."""
        return [d for d in self.duplicates if not d.will_be_skipped]

    def to_legacy_duplication_plan(self) -> DuplicationPlan:
        """Converts to standard DuplicationPlan model for Babysitter.jsx execution."""
        return DuplicationPlan(
            session_id=self.session_id,
            session_folder=self.session_folder,
            preset_id=self.preset_id,
            target_dimensions=self.target_dimensions,
            aspect_ratio_changed=self.aspect_ratio_changed,
            duplicates=self.duplicates,
            rewires=self.rewires,
            depth_max=self.depth_max,
        )


class DAGDuplicationPlanner:
    """Plans multi-level topological precomp duplications across deep nested comp trees."""

    def __init__(
        self,
        structure: ProjectStructure,
        active_manifest: ScrapeManifest,
        target_dimensions: Tuple[int, int],
        preset_id: str,
        *,
        session_id: Optional[str] = None,
        hooks: Optional[HookRegistry] = None,
        timestamp: Optional[datetime] = None,
    ):
        self.structure = structure
        self.manifest = active_manifest
        self.target_dimensions = (int(target_dimensions[0]), int(target_dimensions[1]))
        self.preset_id = preset_id
        self.session_id = session_id or str(uuid.uuid4())
        self.hooks = hooks if hooks is not None else DEFAULT_REGISTRY
        self._ts = timestamp or datetime.now()

        self._analyzer = ProjectStructureAnalyzer(structure)
        self._comp_nodes_by_id: Dict[int, CompNode] = {c.id: c for c in structure.comps}
        self._existing_comp_names: Set[str] = {c.name for c in structure.comps}
        self.nodes: Dict[int, DAGCompNode] = {}

    def session_folder_name(self) -> str:
        """Destination folder inside 'From Dimensions/'."""
        ts_str = self._ts.strftime("%Y-%m-%d_%H%M%S") if isinstance(self._ts, datetime) else str(self._ts)
        return session_bin_path(self.preset_id, ts_str)

    def build_dag(self) -> Dict[int, DAGCompNode]:
        """Constructs the dependency graph from project structure references."""
        self.nodes = {}
        # 1. Initialize nodes
        for comp in self.structure.comps:
            has_camera = False
            has_raw = False
            # Check scrape manifest if this is the active comp
            if self.manifest and self.manifest.layers:
                for l in self.manifest.layers:
                    if l.containing_comp_id == comp.id or (comp.name == self.manifest.project_info.name):
                        if getattr(l, "layer_kind", None) == "camera" or getattr(l, "camera", None) is not None:
                            has_camera = True
                        if l.source_item and l.source_item.kind == "footage":
                            has_raw = True

            self.nodes[comp.id] = DAGCompNode(
                comp_id=comp.id,
                name=comp.name,
                width=comp.width,
                height=comp.height,
                fps=comp.fps,
                duration=comp.duration,
                depth=0,
                mode=PrecompConformMode.FLUID,
                children_comp_ids=[],
                parent_comp_ids=[],
                has_3d_camera=has_camera,
                has_raw_footage=has_raw,
                is_protected=comp.preserve_nested_resolution,
                layer_count=comp.layer_count,
            )

        # 2. Wire edges from references
        for ref in self.structure.references:
            parent_id = ref.from_comp_id
            child_id = ref.to_comp_id
            if parent_id in self.nodes and child_id in self.nodes:
                if child_id not in self.nodes[parent_id].children_comp_ids:
                    self.nodes[parent_id].children_comp_ids.append(child_id)
                if parent_id not in self.nodes[child_id].parent_comp_ids:
                    self.nodes[child_id].parent_comp_ids.append(parent_id)

        # 2b. Degree bookkeeping (salvaged from the now-retired
        # core/precomp/dag_graph.py per #413/#406) -- a plain "how many
        # parents does this precomp have" diagnostic, cheap to compute
        # once the edge lists above are final.
        for node in self.nodes.values():
            node.in_degree = len(node.parent_comp_ids)
            node.out_degree = len(node.children_comp_ids)

        # 3. Calculate depths from active root comp
        root_id = self._resolve_root_comp_id()
        if root_id in self.nodes:
            self._compute_depths(root_id, 0, set())

        return self.nodes

    def _compute_depths(self, comp_id: int, current_depth: int, visited: Set[int]):
        if comp_id in visited or comp_id not in self.nodes:
            return
        visited.add(comp_id)
        node = self.nodes[comp_id]
        node.depth = max(node.depth, current_depth)
        for child_id in node.children_comp_ids:
            self._compute_depths(child_id, current_depth + 1, visited.copy())

    def detect_cycles(self) -> List[List[int]]:
        """DFS cycle detection with recursion stack tracking."""
        if not self.nodes:
            self.build_dag()

        visited: Set[int] = set()
        rec_stack: List[int] = []
        cycles: List[List[int]] = []

        def dfs(comp_id: int):
            visited.add(comp_id)
            rec_stack.append(comp_id)

            node = self.nodes.get(comp_id)
            if node:
                for child_id in node.children_comp_ids:
                    if child_id not in visited:
                        dfs(child_id)
                    elif child_id in rec_stack:
                        # Cycle found! Extract the cycle path
                        cycle_start_idx = rec_stack.index(child_id)
                        cycle_path = rec_stack[cycle_start_idx:] + [child_id]
                        cycles.append(cycle_path)

            rec_stack.pop()

        for comp_id in self.nodes:
            if comp_id not in visited:
                dfs(comp_id)

        return cycles

    def topological_sort(self, root_comp_id: Optional[int] = None) -> List[int]:
        """Returns bottom-up topological execution order (leaf precomps first).

        Iterative Kahn's-algorithm implementation, salvaged from the
        now-retired `core/precomp/dag_graph.py` per issue #413/#406 --
        the prior recursive post-order DFS had no ceiling on recursion
        depth, a real risk on a deep nested-precomp chain. This version
        processes a queue instead of recursing.

        NOTE: this method still calls `detect_cycles()` and (via
        `build_dag()`) `_compute_depths()` first, both of which remain
        recursive -- salvaging this sort step doesn't remove either
        ceiling. Those are the other two pre-wiring defects the DAG
        comparison memo flagged as separately fixable; #413 scoped this
        PR to the sort + degree bookkeeping only. Deep-chain safety for
        the whole method is not yet guaranteed end to end.
        """
        if not self.nodes:
            self.build_dag()

        cycles = self.detect_cycles()
        if cycles:
            raise DAGCycleError(cycles[0])

        root_id = root_comp_id if root_comp_id is not None else self._resolve_root_comp_id()

        # Scope to the subgraph reachable from root_id first -- a comp
        # elsewhere in the project that this root doesn't reference must
        # not appear in this root's execution order (matches the old
        # DFS's behavior of only ever visiting reachable nodes).
        reachable: Set[int] = set()
        frontier: deque = deque([root_id])
        while frontier:
            cid = frontier.popleft()
            if cid in reachable or cid not in self.nodes:
                continue
            reachable.add(cid)
            for child_id in self.nodes[cid].children_comp_ids:
                if child_id not in reachable:
                    frontier.append(child_id)

        # Kahn's algorithm, bottom-up: a leaf has zero children remaining
        # within the reachable subgraph.
        out_deg = {
            cid: sum(1 for c in self.nodes[cid].children_comp_ids if c in reachable)
            for cid in reachable
        }
        parents_of: Dict[int, List[int]] = defaultdict(list)
        for cid in reachable:
            for child_id in self.nodes[cid].children_comp_ids:
                if child_id in reachable:
                    parents_of[child_id].append(cid)

        order: List[int] = []
        queue: deque = deque(cid for cid, deg in out_deg.items() if deg == 0)
        while queue:
            curr = queue.popleft()
            order.append(curr)
            for parent_id in parents_of[curr]:
                out_deg[parent_id] -= 1
                if out_deg[parent_id] == 0:
                    queue.append(parent_id)

        if len(order) != len(reachable):
            # detect_cycles() already validated the whole graph above, so
            # this is unreachable in practice -- guarding it anyway rather
            # than silently returning a partial order.
            raise DAGCycleError([root_id], message="Topological sort failed on reachable subgraph despite cycle check passing")

        return order

    def classify_comp_mode(self, comp_id: int) -> PrecompConformMode:
        """Autonomous classification of FLUID vs SEALED mode."""
        node = self.nodes.get(comp_id)
        if not node:
            return PrecompConformMode.FLUID

        # Camera scenes, 3D rigs, or comps marked preserve_nested_resolution -> SEALED
        if node.has_3d_camera or node.is_protected:
            return PrecompConformMode.SEALED

        comp_raw = self._comp_nodes_by_id.get(comp_id)
        if comp_raw and (comp_raw.preserve_nested_resolution or comp_raw.preserve_nested_frame_rate):
            return PrecompConformMode.SEALED

        return PrecompConformMode.FLUID

    def plan(
        self,
        user_overrides: Optional[Dict[str, bool]] = None,
        mode_overrides: Optional[Dict[int, PrecompConformMode]] = None,
        user_fork_overrides: Optional[Dict[str, bool]] = None,
    ) -> DAGDuplicationPlan:
        """Generates the multi-level duplication and rewiring plan."""
        self.build_dag()
        cycles = self.detect_cycles()
        if cycles:
            raise DAGCycleError(cycles[0])

        root_id = self._resolve_root_comp_id()
        topo_order = self.topological_sort(root_id)

        overrides = dict(user_overrides or {})
        mode_ovr = dict(mode_overrides or {})
        fork_ovr = dict(user_fork_overrides or {})

        # Compute aspect ratio changed
        root_node = self.nodes.get(root_id)
        src_w = root_node.width if root_node else (self.manifest.project_info.width if self.manifest else 1920)
        src_h = root_node.height if root_node else (self.manifest.project_info.height if self.manifest else 1080)
        src_aspect = src_w / max(1, src_h)
        tgt_aspect = self.target_dimensions[0] / max(1, self.target_dimensions[1])
        aspect_ratio_changed = abs(src_aspect - tgt_aspect) / src_aspect >= _ASPECT_TOLERANCE

        # Collect protect tags from active manifest
        protect_flags = self._collect_protect_flags()
        active_refs = self._active_layer_refs_to_comps()

        duplicates: List[PrecompDuplicate] = []
        rewires: List[LayerRewire] = []
        warnings: List[str] = []
        depth_max = 0

        # Map from original comp_id to new duplicate UID string
        dup_uid_by_comp_id: Dict[int, str] = {}

        # Iterate in topological order (leaf comps first, root excluded from duplicate list)
        for comp_id in topo_order:
            if comp_id == root_id:
                continue

            node = self.nodes[comp_id]
            depth_max = max(depth_max, node.depth)

            # Classify mode
            assigned_mode = mode_ovr.get(comp_id, self.classify_comp_mode(comp_id))
            node.mode = assigned_mode

            uid_key = str(comp_id)
            is_protect = (
                comp_id in protect_flags
                or node.is_protected
                or (comp_id in self._comp_nodes_by_id and self._comp_nodes_by_id[comp_id].preserve_nested_resolution)
            )

            # Override handling
            will_be_skipped = False
            if uid_key in overrides:
                will_be_skipped = not overrides[uid_key]
            elif is_protect:
                will_be_skipped = True

            reason = "USER_REQUESTED" if uid_key in overrides else "SHARED"

            # Unique name resolution
            dup_name, version = self._unique_duplicate_name(node.name)
            self._existing_comp_names.add(dup_name)

            dup_entry = PrecompDuplicate(
                original_uid=uid_key,
                original_name=node.name,
                duplicate_name=dup_name,
                target_folder_path=self.session_folder_name(),
                original_width=node.width,
                original_height=node.height,
                reason=reason,
                is_protected=is_protect,
                will_be_skipped=will_be_skipped,
                name_version=version,
                fork_per_consumer=fork_ovr.get(uid_key, False),
                consumer_layer_uid=None,
            )
            dup_entry = self.hooks.fire_on_pre_duplicate(dup_entry)
            duplicates.append(dup_entry)
            dup_uid_by_comp_id[comp_id] = f"dup:{uid_key}"

            # If this is a direct child of the active root comp, generate rewires
            if comp_id in active_refs:
                for (luid, lname, ltag) in active_refs[comp_id]:
                    if ltag == "PROTECT" or will_be_skipped:
                        continue
                    rewires.append(LayerRewire(
                        conformed_layer_uid=luid,
                        conformed_layer_name=lname,
                        original_source_uid=uid_key,
                        new_source_uid=f"dup:{uid_key}",
                    ))

            # Multi-level reference rewires from parent comps in the project structure
            for ref in self.structure.references:
                if ref.to_comp_id == comp_id and ref.from_comp_id != root_id:
                    # Parent is an intermediate precomp
                    rewires.append(LayerRewire(
                        conformed_layer_uid=f"layer:{ref.from_comp_id}:{ref.layer_index}",
                        conformed_layer_name=ref.layer_name,
                        original_source_uid=uid_key,
                        new_source_uid=f"dup:{uid_key}",
                    ))

        return DAGDuplicationPlan(
            session_id=self.session_id,
            session_folder=self.session_folder_name(),
            preset_id=self.preset_id,
            target_dimensions=self.target_dimensions,
            aspect_ratio_changed=aspect_ratio_changed,
            root_comp_id=root_id,
            topological_order=topo_order,
            nodes=self.nodes,
            duplicates=duplicates,
            rewires=rewires,
            depth_max=depth_max,
            warnings=warnings,
        )

    # ── Internal Helpers ──────────────────────────────────────────────

    def _resolve_root_comp_id(self) -> int:
        if self.manifest and self.manifest.layers:
            first_layer = self.manifest.layers[0]
            if first_layer.containing_comp_id:
                return first_layer.containing_comp_id
        if self.manifest and self.manifest.project_info:
            for c in self.structure.comps:
                if c.name == self.manifest.project_info.name:
                    return c.id
        if self.structure.comps:
            return self.structure.comps[0].id
        return 1

    def _collect_protect_flags(self) -> Set[int]:
        flags: Set[int] = set()
        if not self.manifest:
            return flags
        for layer in self.manifest.layers:
            if getattr(layer, "content_tag", None) == "PROTECT":
                if layer.source_item and layer.source_item.kind == "comp":
                    flags.add(layer.source_item.id)
        return flags

    def _active_layer_refs_to_comps(self) -> Dict[int, List[Tuple[str, str, str]]]:
        refs: Dict[int, List[Tuple[str, str, str]]] = {}
        if not self.manifest:
            return refs
        for layer in self.manifest.layers:
            if layer.source_item and layer.source_item.kind == "comp":
                cid = layer.source_item.id
                refs.setdefault(cid, []).append((
                    layer.uid or f"idx-{layer.index}",
                    layer.name,
                    getattr(layer, "content_tag", "") or "",
                ))
        return refs

    def _unique_duplicate_name(self, original_name: str) -> Tuple[str, int]:
        target_w, target_h = self.target_dimensions
        base = f"{original_name}_{target_w}x{target_h}"
        if base not in self._existing_comp_names:
            return base, 1

        version = 2
        while True:
            candidate = f"{base}_v{version}"
            if candidate not in self._existing_comp_names:
                return candidate, version
            version += 1
