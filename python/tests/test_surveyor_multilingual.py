# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_surveyor_multilingual.py
TASK-ENG-02 (#257) — Multi-Lingual Heuristics Dictionary Expansion.

Verifies heuristic classification across the top 8 broadcast languages:
German, French, Japanese, Spanish, Portuguese, Korean, Italian, English.
Ensures multi-lingual text layers achieve >85% confidence without falling back to UNKNOWN.
"""

import pytest
from core.surveyor import classify_layer, survey_manifest
from models.scrape_manifest import ScrapeManifest, ProjectInfo, LayerModel, LayerFlags


def _make_text_layer(index: int, name: str) -> LayerModel:
    return LayerModel(
        index=index,
        name=name,
        layer_kind="av",
        flags=LayerFlags(is_text_layer=True),
        position=[960.0, 540.0, 0.0],
        scale=[100.0, 100.0, 100.0],
    )


def _make_av_layer(index: int, name: str) -> LayerModel:
    return LayerModel(
        index=index,
        name=name,
        layer_kind="av",
        position=[960.0, 540.0, 0.0],
        scale=[100.0, 100.0, 100.0],
    )


class TestSurveyorMultilingual:
    """Test suite for 8-language broadcast heuristic classification."""

    @pytest.mark.parametrize(
        "name, expected_tag",
        [
            # German (DE)
            ("Rechtliche Hinweise_v1", "BOTTOM"),
            ("Haftungsausschluss", "BOTTOM"),
            ("AGB_Kleingedrucktes", "BOTTOM"),
            ("Haupttitel_Master", "TOP"),
            ("Untertitel_Szene01", "TOP"),
            ("Schlagzeile_Text", "TOP"),
            # French (FR)
            ("Mentions Légales", "BOTTOM"),
            ("mentions_legales_card", "BOTTOM"),
            ("Droits Réservés", "BOTTOM"),
            ("Titre_Principal", "TOP"),
            ("Sous-titre_01", "TOP"),
            ("Chapeau_Texte", "TOP"),
            # Japanese (JA)
            ("規約_テキスト", "BOTTOM"),
            ("免責事項_注記", "BOTTOM"),
            ("著作権_表記", "BOTTOM"),
            ("メインタイトル", "TOP"),
            ("サブタイトル_v2", "TOP"),
            ("見出し_テロップ", "TOP"),
            # Spanish (ES)
            ("Aviso Legal_Fin", "BOTTOM"),
            ("Términos y Condiciones", "BOTTOM"),
            ("Derechos Reservados", "BOTTOM"),
            ("Título_Episodio", "TOP"),
            ("Subtítulo_Castellano", "TOP"),
            ("Titular_Noticia", "TOP"),
            # Portuguese (PT)
            ("Condições Gerais", "BOTTOM"),
            ("Direitos Reservados_2026", "BOTTOM"),
            ("Notas Legais", "BOTTOM"),
            ("Cabeçalho_Promo", "TOP"),
            ("Manchete_Principal", "TOP"),
            ("Tarja_Texto", "TOP"),
            # Korean (KO)
            ("이용약관_고지", "BOTTOM"),
            ("법적고지_카드", "BOTTOM"),
            ("면책조항_텍스트", "BOTTOM"),
            ("메인타이틀_시퀀스", "TOP"),
            ("서브타이틀_자막", "TOP"),
            ("헤드라인_뉴스", "TOP"),
            # Italian (IT)
            ("Note Legali_Spot", "BOTTOM"),
            ("Diritti Riservati_RAI", "BOTTOM"),
            ("Avvertenze_Legali", "BOTTOM"),
            ("Titolo_Centrale", "TOP"),
            ("Sottotitolo_01", "TOP"),
            ("Didascalia_Testo", "TOP"),
        ],
    )
    def test_multilingual_text_layer_high_confidence(self, name: str, expected_tag: str):
        """Text layers in any supported broadcast language achieve >85% confidence."""
        layer = _make_text_layer(1, name)
        res = classify_layer(layer, layer_index=1, total_layers=10)

        assert res.tag == expected_tag, f"Layer '{name}' expected {expected_tag}, got {res.tag}"
        assert res.confidence >= 0.85, f"Layer '{name}' confidence {res.confidence:.2f} < 0.85 threshold"
        assert res.source == "heuristic"

    @pytest.mark.parametrize(
        "name, expected_tag",
        [
            # German Visual Elements
            ("Hintergrund_Farbfläche", "FILL"),
            ("Hauptgrafik_KeyVisual", "CENTER"),
            ("Markenlogo_Anim", "CENTER"),
            # French Visual Elements
            ("Fond_Arrière-Plan", "FILL"),
            ("Visuel_Principal", "CENTER"),
            ("Affiche_Hero", "CENTER"),
            # Japanese Visual Elements
            ("背景_ベース", "FILL"),
            ("背景レイヤー", "FILL"),
            ("キービジュアル_PSD", "CENTER"),
            ("メイン画像_Hero", "CENTER"),
            # Spanish Visual Elements
            ("Capa de Fondo", "FILL"),
            ("Arte Principal_Key", "CENTER"),
            # Portuguese Visual Elements
            ("Plano de Fundo", "FILL"),
            ("Visual Principal_Hero", "CENTER"),
            # Korean Visual Elements
            ("배경_그라디언트", "FILL"),
            ("키비주얼_컴프", "CENTER"),
            # Italian Visual Elements
            ("Livello di Sfondo", "FILL"),
            ("Immagine Principale", "CENTER"),
        ],
    )
    def test_multilingual_visual_elements_classification(self, name: str, expected_tag: str):
        """Non-text background plates and key art visuals classify accurately."""
        layer = _make_av_layer(2, name)
        res = classify_layer(layer, layer_index=5, total_layers=10)

        assert res.tag == expected_tag, f"Visual layer '{name}' expected {expected_tag}, got {res.tag}"
        assert res.confidence >= 0.50

    def test_full_multilingual_comp_survey_manifest(self):
        """Full comp manifest with mixed international layers surveys cleanly."""
        manifest = ScrapeManifest(
            schema_version="5.0",
            status="OK",
            project_info=ProjectInfo(
                name="International_Promo_Campaign",
                path="/Volumes/Creative/Promo.aep",
                active_comp_id=1,
                width=1920,
                height=1080,
                fps=25.0,
                duration=15.0,
                linear_color=False,
            ),
            layers=[
                _make_text_layer(1, "Haupttitel_German"),
                _make_text_layer(2, "Mentions Légales_French"),
                _make_text_layer(3, "規約_Japanese"),
                _make_text_layer(4, "Aviso Legal_Spanish"),
                _make_text_layer(5, "이용약관_Korean"),
                _make_av_layer(6, "Plano de Fundo_Portuguese"),
                _make_av_layer(7, "Immagine Principale_Italian"),
            ],
        )

        survey_manifest(manifest)

        # In-place mutations verified
        assert manifest.layers[0].content_tag == "TOP"
        assert manifest.layers[1].content_tag == "BOTTOM"
        assert manifest.layers[2].content_tag == "BOTTOM"
        assert manifest.layers[3].content_tag == "BOTTOM"
        assert manifest.layers[4].content_tag == "BOTTOM"
        assert manifest.layers[5].content_tag == "FILL"
        assert manifest.layers[6].content_tag == "CENTER"

        # Zero unclassified / None layers
        for layer in manifest.layers:
            assert layer.content_tag is not None
            assert layer.content_tag_confidence >= 0.50
