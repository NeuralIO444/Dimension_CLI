# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_placement_units_overrides.py

Tests for the unit override mechanism, specifically the "create" action
for manually grouping layers.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.placement_units import compute_placement_resolution, build_placement_units  # noqa: E402
from models.scrape_manifest import (  # noqa: E402
    LayerModel,
    ProjectInfo,
    ScrapeManifest,
)


@pytest.fixture
def sample_manifest_for_overrides() -> ScrapeManifest:
    """Provides a manifest with three distinct layers for override testing."""
    return ScrapeManifest(
        status="OK",
        project_info=ProjectInfo(name="Test", width=1920, height=1080),
        layers=[
            LayerModel(
                index=1,
                name="Layer A",
                uid="uid-a",
                containing_comp_id=1,
                position=[100, 100, 0],
            ),
            LayerModel(
                index=2,
                name="Layer B",
                uid="uid-b",
                containing_comp_id=1,
                position=[300, 100, 0],
            ),
            LayerModel(
                index=3,
                name="Layer C",
                uid="uid-c",
                containing_comp_id=1,
                position=[800, 800, 0],
            ),
            LayerModel(
                index=4,
                name="Grouped Layer D",
                uid="uid-d",
                containing_comp_id=1,
                position=[150, 150, 0],
            ),
            LayerModel(
                index=5,
                name="Grouped Layer E",
                uid="uid-e",
                containing_comp_id=1,
                position=[160, 160, 0],
            ),
        ],
    )


class TestUnitCreateOverride:
    def test_create_override_forces_grouping(
        self, sample_manifest_for_overrides: ScrapeManifest
    ):
        """
        Verifies that a 'create' override forces specified layers to share
        a centroid and cluster size, effectively making them a single unit.
        """
        # Define an override to manually group Layer A and Layer B
        overrides = {
            "manual_unit_1": {
                "action": "create",
                "members": ["uid-a", "uid-b"],
            }
        }
        sample_manifest_for_overrides.unit_overrides = overrides

        # Run the placement resolution
        resolution = compute_placement_resolution(
            sample_manifest_for_overrides, layout="tags"
        )

        # Calculate the expected centroid for the manual group (A and B)
        # Layer A is at x=100, Layer B is at x=300. Centroid x = 200.
        # Both are at y=100. Centroid y = 100.
        expected_centroid = (200.0, 100.0)

        # Check Layer A
        key_a = (1, 1)  # (comp_id, index)
        assert resolution.layer_centroids.get(key_a) == expected_centroid
        assert resolution.layer_cluster_sizes.get(key_a) == 2

        # Check Layer B
        key_b = (1, 2)
        assert resolution.layer_centroids.get(key_b) == expected_centroid
        assert resolution.layer_cluster_sizes.get(key_b) == 2

        # Check Layer C (unaffected)
        # Singletons carry no centroid entry — absent means "use own
        # position" downstream (same contract as compute_group_clusters).
        key_c = (1, 3)
        assert key_c not in resolution.layer_centroids
        assert resolution.layer_cluster_sizes.get(key_c, 1) == 1


class TestUnitDissolveOverride:
    def test_dissolve_override_breaks_up_heuristic_group(
        self, sample_manifest_for_overrides: ScrapeManifest
    ):
        """
        Verifies that a 'dissolve' override correctly breaks apart a unit
        that would have been formed by the spatial clustering heuristic.
        """
        # Layers D and E are close enough to be clustered by default.
        # First, build a baseline report to get the heuristic unit's ID.
        # We need to give them a tag so they are considered for grouping.
        sample_manifest_for_overrides.layers[3].content_tag = "CENTER"
        sample_manifest_for_overrides.layers[4].content_tag = "CENTER"

        baseline_report = build_placement_units(sample_manifest_for_overrides)
        grouped_unit = next(
            (u for u in baseline_report.units if u.kind == "group" and len(u.members) == 2),
            None,
        )
        assert grouped_unit is not None, "Test setup failed: No 2-member group was formed by default."
        unit_to_dissolve_id = grouped_unit.unit_id

        # Now, define an override to dissolve this unit
        overrides = {unit_to_dissolve_id: {"action": "dissolve"}}
        sample_manifest_for_overrides.unit_overrides = overrides

        # Run placement resolution with the dissolve override
        resolution = compute_placement_resolution(
            sample_manifest_for_overrides, layout="tags"
        )

        # Check Layer D - it should now be a singleton. The centroid entry
        # is removed on dissolve — absent means "use own position".
        key_d = (1, 4)
        assert key_d not in resolution.layer_centroids
        assert resolution.layer_cluster_sizes.get(key_d) == 1, "Dissolved layer D should be a singleton (size 1)"

        # Check Layer E - it should also be a singleton
        key_e = (1, 5)
        assert key_e not in resolution.layer_centroids
        assert resolution.layer_cluster_sizes.get(key_e) == 1, "Dissolved layer E should be a singleton (size 1)"