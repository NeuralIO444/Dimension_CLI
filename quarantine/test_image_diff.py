# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_image_diff.py

Unit tests for the image comparison utility used by the visual regression
testing harness.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from tests.cep_harness.image_diff import compare_images  # noqa: E402


@pytest.fixture(scope="module")
def image_fixtures(tmp_path_factory) -> dict:
    """Creates a set of test images for comparison."""
    tmp_path = tmp_path_factory.mktemp("image_diff_fixtures")
    
    # 1. Golden reference image
    golden_path = tmp_path / "golden.png"
    golden_img = Image.new("RGB", (100, 100), color="red")
    golden_img.save(golden_path)

    # 2. Identical image
    identical_path = tmp_path / "identical.png"
    identical_img = Image.new("RGB", (100, 100), color="red")
    identical_img.save(identical_path)

    # 3. Slightly different image (one pixel changed)
    slight_diff_path = tmp_path / "slight_diff.png"
    slight_diff_img = Image.new("RGB", (100, 100), color="red")
    slight_diff_img.putpixel((50, 50), (255, 0, 10)) # Change one pixel slightly
    slight_diff_img.save(slight_diff_path)

    # 4. Very different image
    very_diff_path = tmp_path / "very_diff.png"
    very_diff_img = Image.new("RGB", (100, 100), color="blue")
    very_diff_img.save(very_diff_path)

    # 5. Different dimensions
    wrong_dims_path = tmp_path / "wrong_dims.png"
    wrong_dims_img = Image.new("RGB", (100, 101), color="red")
    wrong_dims_img.save(wrong_dims_path)

    return {
        "golden": golden_path,
        "identical": identical_path,
        "slight_diff": slight_diff_path,
        "very_diff": very_diff_path,
        "wrong_dims": wrong_dims_path,
    }


class TestImageDiff:
    def test_identical_images_match(self, image_fixtures):
        """Identical images should have an MSE of 0.0 and match."""
        is_match, mse, _ = compare_images(image_fixtures["golden"], image_fixtures["identical"])
        assert is_match is True
        assert mse == 0.0

    def test_slightly_different_images_match_with_default_threshold(self, image_fixtures):
        """A single-pixel difference should be below the default threshold of 1.0."""
        is_match, mse, _ = compare_images(image_fixtures["golden"], image_fixtures["slight_diff"])
        assert is_match is True
        assert 0 < mse < 1.0

    def test_slightly_different_images_mismatch_with_zero_threshold(self, image_fixtures):
        """With a threshold of 0.0, even a slight difference should be a mismatch."""
        is_match, mse, _ = compare_images(image_fixtures["golden"], image_fixtures["slight_diff"], threshold=0.0)
        assert is_match is False
        assert mse > 0.0

    def test_very_different_images_mismatch(self, image_fixtures):
        """Completely different images should have a high MSE and mismatch."""
        is_match, mse, _ = compare_images(image_fixtures["golden"], image_fixtures["very_diff"])
        assert is_match is False
        assert mse > 10000 # Expect a large error value

    def test_different_dimensions_mismatch(self, image_fixtures):
        """Images with different dimensions should immediately mismatch."""
        is_match, mse, summary = compare_images(image_fixtures["golden"], image_fixtures["wrong_dims"])
        assert is_match is False
        assert mse == -1.0
        assert "different dimensions" in summary

    def test_non_existent_file_returns_error(self, tmp_path):
        """Comparing a non-existent file should return an error."""
        is_match, mse, summary = compare_images(Path("non_existent.png"), tmp_path / "a.png")
        assert is_match is False
        assert "Error comparing images" in summary