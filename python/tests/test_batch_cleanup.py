# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_batch_cleanup.py
Slot 13 Stage E Integration Test Suite.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..")))


def test_payload_slicer_cleanup_session_batch(tmp_path, monkeypatch):
    """Test that cleanup_session properly supports batch folder and target subfolder."""
    # Setup mock folders
    monkeypatch.chdir(tmp_path)
    
    # Create mock session files in root
    with open("scrape_manifest.json", "w") as f:
        f.write("{}")
    with open("conformed_manifest.json", "w") as f:
        f.write("{}")
    with open("chunk_manifest.json", "w") as f:
        f.write("{}")
    
    os.makedirs(".dimension", exist_ok=True)
    with open(os.path.join(".dimension", "duplication_log.json"), "w") as f:
        f.write("{}")
        
    os.makedirs("Chunks", exist_ok=True)
    with open(os.path.join("Chunks", "chunk_0.json"), "w") as f:
        f.write("{}")
        
    from logic.exporter import PayloadSlicer
    
    PayloadSlicer.cleanup_session(
        keep_manifest=True,
        batch_session_folder="TestBatch_123",
        target_subfolder="tiktok_vertical",
    )
    
    # Assert root scrape manifest is kept
    assert os.path.exists("scrape_manifest.json")
    
    # Assert other files are archived under logs/archive/TestBatch_123/tiktok_vertical/
    archive_dir = os.path.join("logs", "archive", "TestBatch_123")
    target_dir = os.path.join(archive_dir, "tiktok_vertical")
    
    assert os.path.exists(os.path.join(archive_dir, "scrape_manifest.json"))
    assert os.path.exists(os.path.join(target_dir, "conformed_manifest.json"))
    assert os.path.exists(os.path.join(target_dir, "chunk_manifest.json"))
    assert os.path.exists(os.path.join(target_dir, "duplication_log.json"))
    assert os.path.exists(os.path.join(target_dir, "Chunks", "chunk_0.json"))


# test_launcher_skips_cleanup deleted in the v6 CEP port — exercised
# the deleted SovereignLauncher's skip_automatic_cleanup flag (a Qt-
# UI-era affordance for "I'm staging multiple targets, don't archive
# yet"). The CEP panel doesn't use the launcher; the batch-cleanup
# behavior it actually depends on is covered by
# test_payload_slicer_cleanup_session_batch above.


def test_generate_batch_report(tmp_path):
    """Test that generate_batch_report creates a correct HTML file."""
    from logic import report_generator
    
    batch_results = [
        {
            "target_id": "tiktok",
            "target_name": "TikTok Vertical",
            "width": 1080,
            "height": 1920,
            "aspect_strategy": "Fit",
            "layers_count": 15,
            "duplicates_count": 2,
            "status": "SUCCESS",
            "error_message": "",
        },
        {
            "target_id": "youtube",
            "target_name": "YouTube Landscape",
            "width": 1920,
            "height": 1080,
            "aspect_strategy": "—",
            "layers_count": 0,
            "duplicates_count": 0,
            "status": "FAILED",
            "error_message": "AE Timeout",
        }
    ]
    
    out_path = str(tmp_path / "batch_report.html")
    report_generator.generate_batch_report(
        batch_session_id="BatchSession_999",
        source_name="MyComp",
        batch_results=batch_results,
        output_path=out_path,
    )
    
    assert os.path.exists(out_path)
    with open(out_path, "r", encoding="utf-8") as f:
        html = f.read()
        
    assert "DIMENSION BATCH CONFORM REPORT" in html
    assert "TikTok Vertical" in html
    assert "YouTube Landscape" in html
    assert "1080 × 1920" in html
    assert "AE Timeout" in html
    assert "FAILED" in html
    assert "SUCCESS" in html
