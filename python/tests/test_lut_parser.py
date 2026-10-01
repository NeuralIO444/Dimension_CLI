# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/tests/test_lut_parser.py
Color Match (Track D, CM3) — tests for python/core/lut_parser.py.
"""

from pathlib import Path

import pytest

from core.lut_parser import LutParseError, load_lut, parse_3dl, parse_cube

FIXTURES = Path(__file__).parent / "fixtures" / "luts"


class TestParseCube:
    def test_identity_grid(self):
        lut = parse_cube(FIXTURES / "identity_2x2x2.cube")
        assert lut.source_format == "cube"
        assert lut.grid_size == 2
        assert lut.domain_min == (0.0, 0.0, 0.0)
        assert lut.domain_max == (1.0, 1.0, 1.0)
        assert lut.title == "Synthetic_Identity_2x2x2"
        assert len(lut.table) == 8
        assert lut.table[0] == (0.0, 0.0, 0.0)
        assert lut.table[-1] == (1.0, 1.0, 1.0)

    def test_extended_domain_not_clamped(self):
        lut = parse_cube(FIXTURES / "flame_scene_linear.cube")
        assert lut.domain_max == (10.0, 10.0, 10.0)
        assert lut.table[1] == (4.8521, 0.12, 0.05)
        assert lut.table[-1] == (8.5, 9.2, 7.8)

    def test_utf8_bom_header_parses(self):
        lut = parse_cube(FIXTURES / "utf8_bom_standard.cube")
        assert lut.grid_size == 2
        assert len(lut.table) == 8
        assert lut.table[0] == (0.0, 0.0, 0.0)

    def test_nan_rejected_with_line_number(self):
        with pytest.raises(LutParseError, match=r"NaN or Inf.*line 4"):
            parse_cube(FIXTURES / "nan_corrupted.cube")

    def test_inf_rejected_with_line_number(self):
        with pytest.raises(LutParseError, match=r"NaN or Inf.*line 4"):
            parse_cube(FIXTURES / "inf_corrupted.cube")

    def test_truncated_body_rejected(self):
        with pytest.raises(LutParseError, match=r"Expected 8 rows.*found 5"):
            parse_cube(FIXTURES / "truncated_body.cube")

    def test_missing_lut_3d_size_rejected(self, tmp_path):
        bad = tmp_path / "no_size.cube"
        bad.write_text("TITLE \"No Size\"\n0.0 0.0 0.0\n")
        with pytest.raises(LutParseError, match="Missing required LUT_3D_SIZE"):
            parse_cube(bad)

    def test_lut_1d_size_rejected(self, tmp_path):
        bad = tmp_path / "one_d.cube"
        bad.write_text("LUT_1D_SIZE 2\n0.0 0.0 0.0\n1.0 1.0 1.0\n")
        with pytest.raises(LutParseError, match="1D LUTs"):
            parse_cube(bad)

    def test_decimal_comma_rejected(self, tmp_path):
        bad = tmp_path / "comma.cube"
        bad.write_text(
            "LUT_3D_SIZE 2\n"
            "0.0 0.0 0.0\n"
            "1,0 0.0 0.0\n"
            "0.0 1.0 0.0\n"
            "1.0 1.0 0.0\n"
            "0.0 0.0 1.0\n"
            "1.0 0.0 1.0\n"
            "0.0 1.0 1.0\n"
            "1.0 1.0 1.0\n"
        )
        with pytest.raises(LutParseError, match="decimal period"):
            parse_cube(bad)

    def test_extra_columns_rejected(self, tmp_path):
        bad = tmp_path / "extra_col.cube"
        bad.write_text("LUT_3D_SIZE 2\n" + "0.0 0.0 0.0 0.0\n" * 8)
        with pytest.raises(LutParseError, match="Expected 3 numeric columns"):
            parse_cube(bad)

    def test_comments_and_blank_lines_ignored(self, tmp_path):
        good = tmp_path / "commented.cube"
        good.write_text(
            "# a comment\n"
            "LUT_3D_SIZE 2\n"
            "\n"
            "# another comment\n"
            "0.0 0.0 0.0\n"
            "1.0 0.0 0.0\n"
            "0.0 1.0 0.0\n"
            "1.0 1.0 0.0\n"
            "0.0 0.0 1.0\n"
            "1.0 0.0 1.0\n"
            "0.0 1.0 1.0\n"
            "1.0 1.0 1.0\n"
        )
        lut = parse_cube(good)
        assert len(lut.table) == 8


class TestParse3dl:
    def test_bare_int_header_10bit(self):
        lut = parse_3dl(FIXTURES / "flame_2023_10bit.3dl")
        assert lut.source_format == "3dl"
        assert lut.grid_size == 2
        assert lut.bit_depth == 10
        assert lut.divisor == 1023.0
        assert lut.domain_min == (0.0, 0.0, 0.0)
        assert lut.domain_max == (1.0, 1.0, 1.0)
        assert len(lut.table) == 8
        values = {v for triplet in lut.table for v in triplet}
        assert values == {0.0, 1.0}

    def test_mesh_header_12bit(self):
        lut = parse_3dl(FIXTURES / "flame_2026_12bit.3dl")
        assert lut.grid_size == 2
        assert lut.bit_depth == 12
        assert lut.divisor == 4095.0
        values = {v for triplet in lut.table for v in triplet}
        assert values == {0.0, 1.0}

    def test_non_cubic_mesh_rejected(self):
        with pytest.raises(LutParseError, match="Non-cubic 3D LUT mesh 2x3x2"):
            parse_3dl(FIXTURES / "non_cubic_mesh.3dl")

    def test_unknown_variant_guides_to_cube(self):
        with pytest.raises(LutParseError, match=r"re-export as \.cube"):
            parse_3dl(FIXTURES / "unknown_variant.3dl")

    def test_16bit_auto_detected(self, tmp_path):
        lines = ["2", "0 65535"]
        corners = [
            (0, 0, 0), (65535, 0, 0), (0, 65535, 0), (65535, 65535, 0),
            (0, 0, 65535), (65535, 0, 65535), (0, 65535, 65535), (65535, 65535, 65535),
        ]
        lines += [f"{r} {g} {b}" for r, g, b in corners]
        path = tmp_path / "sixteen_bit.3dl"
        path.write_text("\n".join(lines) + "\n")
        lut = parse_3dl(path)
        assert lut.bit_depth == 16
        assert lut.divisor == 65535.0

    def test_out_of_range_integer_rejected(self, tmp_path):
        lines = ["2", "0 0"]
        corners = [(0, 0, 0)] * 7 + [(100000, 0, 0)]
        lines += [f"{r} {g} {b}" for r, g, b in corners]
        path = tmp_path / "out_of_range.3dl"
        path.write_text("\n".join(lines) + "\n")
        with pytest.raises(LutParseError, match="exceeds the known 16-bit"):
            parse_3dl(path)

    def test_row_count_mismatch_rejected(self, tmp_path):
        path = tmp_path / "short.3dl"
        path.write_text("2\n0 1023\n0 0 0\n1023 0 0\n")
        with pytest.raises(LutParseError, match=r"Expected 8 rows"):
            parse_3dl(path)

    def test_empty_file_rejected(self, tmp_path):
        path = tmp_path / "empty.3dl"
        path.write_text("")
        with pytest.raises(LutParseError, match="Empty .3dl file"):
            parse_3dl(path)

    def test_no_shaper_line_still_parses(self, tmp_path):
        # Some exports omit the two-token shaper/range line entirely.
        lines = ["2"]
        corners = [
            (0, 0, 0), (1023, 0, 0), (0, 1023, 0), (1023, 1023, 0),
            (0, 0, 1023), (1023, 0, 1023), (0, 1023, 1023), (1023, 1023, 1023),
        ]
        lines += [f"{r} {g} {b}" for r, g, b in corners]
        path = tmp_path / "no_shaper.3dl"
        path.write_text("\n".join(lines) + "\n")
        lut = parse_3dl(path)
        assert lut.grid_size == 2
        assert lut.bit_depth == 10


class TestLoadLutDispatch:
    def test_dispatches_cube(self):
        lut = load_lut(FIXTURES / "identity_2x2x2.cube")
        assert lut.source_format == "cube"

    def test_dispatches_3dl(self):
        lut = load_lut(FIXTURES / "flame_2023_10bit.3dl")
        assert lut.source_format == "3dl"

    def test_case_insensitive_extension(self, tmp_path):
        src = FIXTURES / "identity_2x2x2.cube"
        upper = tmp_path / "IDENTITY.CUBE"
        upper.write_text(src.read_text())
        lut = load_lut(upper)
        assert lut.source_format == "cube"

    def test_unsupported_extension_rejected(self, tmp_path):
        bad = tmp_path / "grade.txt"
        bad.write_text("not a lut")
        with pytest.raises(LutParseError, match="Unsupported LUT file extension"):
            load_lut(bad)


class TestLutDataImmutability:
    def test_frozen_dataclass(self):
        lut = load_lut(FIXTURES / "identity_2x2x2.cube")
        with pytest.raises(Exception):
            lut.grid_size = 99  # type: ignore[misc]
