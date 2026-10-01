# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""LUT / color-match ops.

`validate` and the `derive*` family are local math — headless.
`inject` honest-fails: AE exposes Apply Color LUT2's file path as
PropertyValueType.NO_VALUE, so no script can set it (Dimension #494).
There is no code path that injects a LUT into AE and none is planned;
Levels Smart Match is the supported in-AE backend.
"""

from __future__ import annotations

import os
import sys
from typing import Any, NoReturn

from dimension.common import EXIT_UNSCRIPTABLE, DimensionError
from bridge.color_match_bridge import LutUnscriptableError
from core.lut_parser import LutParseError, load_lut


def validate_op(*, path: str) -> dict[str, Any]:
    """Parse a .cube/.3dl and summarize it. Headless."""
    if not os.path.isfile(path):
        raise DimensionError(f"LUT file not found: {path}", code="SOURCE_NOT_FOUND")
    try:
        lut = load_lut(path)
    except LutParseError as e:
        raise DimensionError(str(e), code="LUT_PARSE_ERROR") from e
    except OSError as e:
        raise DimensionError(f"could not read LUT file: {e}", code="SOURCE_NOT_FOUND") from e
    return {
        "status": "OK",
        "headless": True,
        "source_format": lut.source_format,
        "grid_size": lut.grid_size,
        "domain_max": list(lut.domain_max),
        "bit_depth": lut.bit_depth,
        "divisor": lut.divisor,
        "title": lut.title,
    }


def inject_op() -> NoReturn:
    """`lut inject` — fail loudly with the named code, never a traceback."""
    try:
        raise LutUnscriptableError()
    except LutUnscriptableError as e:
        raise DimensionError(
            str(e), code=e.code, exit_code=EXIT_UNSCRIPTABLE
        ) from e


def _run_script_main(argv: list[str], module: str, attr: str = "main") -> int:
    """Delegate to a legacy derive script's own main().

    The derive scripts own their argparse surface and print their own
    JSON; this just reslices argv into the shape they expect and
    converts SystemExit into a return code so the CLI layer stays in
    charge of process exit.
    """
    import importlib

    script = importlib.import_module(module)
    old_argv = sys.argv
    sys.argv = argv
    try:
        try:
            result = getattr(script, attr)()
        except SystemExit as e:
            code = e.code
            return code if isinstance(code, int) else (0 if code is None else 1)
        return result if isinstance(result, int) else 0
    finally:
        sys.argv = old_argv


def derive_op(*, kind: str, args: list[str]) -> int:
    """Run a LUT-derivation script. Returns its exit code. Headless."""
    scripts = {
        "derive": ("scripts.derive_lut", ["derive_lut.py"]),
        "derive-parametric": (
            "scripts.derive_parametric_match",
            ["derive_parametric_match.py"],
        ),
        "derive-smart": ("scripts.derive_smart_match", ["derive_smart_match.py"]),
    }
    if kind not in scripts:
        raise DimensionError(f"unknown derive kind: {kind}", code="USAGE")
    module, prog = scripts[kind]
    return _run_script_main(prog + args, module)
