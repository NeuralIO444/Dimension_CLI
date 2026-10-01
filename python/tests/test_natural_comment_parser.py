# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/tests/test_natural_comment_parser.py
Adversarial test suite for Natural Keyword Comment Parsing.
"""

import pytest
from core.surveyor import _extract_natural_comment_keyword, classify_layer, survey_manifest
from models.scrape_manifest import LayerModel, ProjectInfo, ScrapeManifest


class TestNaturalCommentKeywordExtraction:
    @pytest.mark.parametrize("comment,expected_tag", [
        ("hero", "CENTER"),
        ("HERO", "CENTER"),
        ("Hero", "CENTER"),
        ("logo", "CENTER"),
        ("packshot", "CENTER"),
        ("product", "CENTER"),
        ("anchor", "CENTER"),
        ("focus", "CENTER"),
        ("title", "TOP"),
        ("headline", "TOP"),
        ("header", "TOP"),
        ("mt", "TOP"),
        ("bg", "FILL"),
        ("background", "FILL"),
        ("plate", "FILL"),
        ("solid", "FILL"),
        ("environment", "FILL"),
        ("texture", "FILL"),
        ("legals", "BOTTOM"),
        ("legal", "BOTTOM"),
        ("disclaimer", "BOTTOM"),
        ("footnote", "BOTTOM"),
        ("cta", "BOTTOM"),
        ("protect", "PROTECT"),
        ("lock", "PROTECT"),
        ("guide", "GUIDE"),
    ])
    def test_pure_keyword_resolution(self, comment, expected_tag):
        assert _extract_natural_comment_keyword(comment) == expected_tag

    @pytest.mark.parametrize("comment,expected_tag", [
        ("Client logo hero v4", "CENTER"),
        ("3D rendered bg plate from Octane", "FILL"),
        ("main title sequence text", "TOP"),
        ("approved legal disclaimer footnotes", "BOTTOM"),
        ("final color grading overlay fx", "OVERLAY"),
        ("locked camera protect reference", "PROTECT"),
        ("hero, packshot.", "CENTER"),
        ("bg: final render", "FILL"),
    ])
    def test_mixed_studio_comment_extraction(self, comment, expected_tag):
        assert _extract_natural_comment_keyword(comment) == expected_tag

    @pytest.mark.parametrize("comment", [
        "big dog",            # 'big' != 'bg'
        "entitled person",    # 'entitled' != 'title'
        "illegal move",       # 'illegal' != 'legal'
        "plateau mountains",  # 'plateau' != 'plate'
        "heroic journey",     # 'heroic' != 'hero'
        "random note 123",    # no keywords
        "",
        None,
    ])
    def test_substring_and_noise_rejection(self, comment):
        assert _extract_natural_comment_keyword(comment) is None


class TestSurveyorNaturalCommentIntegration:
    def test_natural_comment_sets_manual_tag(self):
        layer = LayerModel(
            index=1,
            name="Un-named Layer 1",
            match_name="ADBE AV Layer",
            comment="hero packshot",
        )
        res = classify_layer(layer, 1, 1, comp_width=1920, comp_height=1080)
        assert res.tag == "CENTER"
        assert res.source == "manual_comment"
        assert res.confidence == 1.0

    def test_natural_comment_bypasses_foreign_comment_skip(self):
        layer = LayerModel(
            index=1,
            name="Layer with studio notes",
            match_name="ADBE AV Layer",
            comment="final bg plate v3",
        )
        manifest = ScrapeManifest(
            schema_version="5.2.0",
            status="ok",
            project_info=ProjectInfo(name="Test", width=1920, height=1080, fps=24.0, duration=5.0),
            layers=[layer],
        )
        # Mark as foreign in report
        class DummyReport:
            by_class = {}
        manifest.comment_report = DummyReport()

        summary = survey_manifest(manifest, comp_width=1920, comp_height=1080)
        assert manifest.layers[0].content_tag == "FILL"
        assert manifest.layers[0].content_tag_source == "manual_comment"
        assert manifest.layers[0].content_tag_confidence == 1.0
