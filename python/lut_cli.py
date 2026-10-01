# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/lut_cli.py
Color Match (Track D, CM4/CM-quick-match) — CLI entry point for every
`lut` subcommand cli.py forwards here (`sys.argv[1] == "lut"` pops the
token and calls `lut_cli.main()`).

`validate` is core.lut_parser.load_lut()'s first production caller —
it runs BEFORE the CEP panel lets the artist click "Apply LUT", so
parse errors (NaN/Inf, non-cubic mesh, unrecognized .3dl variant, etc.)
surface as a clear message in the confirmation modal instead of a
silent or confusing AE-side failure. `derive`/`derive-parametric`/
`derive-smart` forward to scripts/derive_lut.py /
scripts/derive_parametric_match.py / scripts/derive_smart_match.py
(each independently runnable too) — this is the entry point
BB.deriveLutFromStills/BB.convertImageToPng/BB.deriveParametricMatch/
BB.deriveSmartMatch (cep/js/backend_bridge.js) actually reach when
running as the packaged `dimension_engine` binary rather than the dev
venv (see BB.resolveCli's bundled-binary branch, which always routes
through `dimension_engine lut <subcommand> ...` -> cli.py -> here).
Until a 2026-09 fix, this main() only recognized "validate" — any
`derive`/`derive-parametric` invocation via the packaged binary failed
with a "Usage: lut_cli.py validate <path>" error, meaning AUTO-MATCH
and Quick Match's derive step only ever worked from a dev venv
checkout, never a real customer install. `derive-smart` (issue #479,
Horizon Color Planner) is added here for the exact same reason —
a script that only ever gets exercised via a dev venv is a script that
has never actually been proven to work for a customer.

This CLI never touches AE for *injection*; the actual LUT/color-match
inject used to be a separate evalScript call into cep/jsx/host.jsx's
injectColorLut() / injectParametricColorMatch() — which can never work:
AE exposes Apply Color LUT2's file-path property as
PropertyValueType.NO_VALUE, so no scripting mechanism can set it. LUT
*injection* is a permanent AE platform limitation (Dimension #494), not
a bug, and no workaround is being chased. LUT *synthesis* (validate /
derive / derive-parametric / derive-smart below) is fully local math
and works headless. Levels Smart Match is the supported in-AE backend.

Usage:
    lut_cli.py validate <path>
        -> JSON summary of the parsed LUT, or {"status": "ERROR", "error": ...}
    lut_cli.py derive <ref_image> <graded_image> <output_cube> [--size N] [--convert-png <path>]
        -> forwards to scripts.derive_lut.main()
    lut_cli.py derive-parametric <ref_image> <graded_image>
        -> forwards to scripts.derive_parametric_match.main()
    lut_cli.py derive-smart <ref_image> <graded_image> <output_cube> [--size N] [--comp-id ID]
        -> forwards to scripts.derive_smart_match.main()
    lut_cli.py inject <lut_path> [...]
        -> HONEST-FAIL: prints {"status": "ERROR", "code": "LUT_UNSCRIPTABLE", ...}
           and exits 65. LUT injection into AE is impossible (see above).
"""

import json
import sys
from typing import NoReturn

from bridge.color_match_bridge import LutUnscriptableError
from core.lut_parser import LutParseError, load_lut


def _validate(path: str) -> dict:
    try:
        lut = load_lut(path)
    except LutParseError as e:
        return {"status": "ERROR", "error": str(e)}
    except OSError as e:
        return {"status": "ERROR", "error": f"Could not read LUT file: {e}"}

    return {
        "status": "OK",
        "source_format": lut.source_format,
        "grid_size": lut.grid_size,
        "domain_max": list(lut.domain_max),
        "bit_depth": lut.bit_depth,
        "divisor": lut.divisor,
        "title": lut.title,
    }


_USAGE_ERROR = {
    "status": "ERROR",
    "error": "Usage: lut_cli.py <validate|derive|derive-parametric|derive-smart|inject> ...",
}


# EX_DATAERR: the inject request named a real operation the platform
# cannot perform (Dimension #494). Distinct from 1 (usage/parse error).
_EXIT_UNSCRIPTABLE = 65


def _inject_honest_fail() -> "NoReturn":
    """`lut inject` — fail loudly with the named code, never a traceback."""
    try:
        raise LutUnscriptableError()
    except LutUnscriptableError as e:
        print(json.dumps({"status": "ERROR", "code": e.code, "error": str(e)}))
    sys.exit(_EXIT_UNSCRIPTABLE)


_HELP_TEXT = """\
dimension lut — local LUT math (validate, derive). LUT *injection* into
After Effects is impossible and honest-fails.

Subcommands:
  validate <path>        Parse a .cube/.3dl and print a JSON summary.
  derive <ref> <graded> <out.cube> [--size N]
                         Synthesize a 33x33x33 .cube from graded stills.
  derive-parametric <ref> <graded>
                         Derive a parametric match (numbers, not a LUT).
  derive-smart <ref> <graded> <out.cube> [--size N]
                         Smart-match .cube synthesis.
  inject <lut_path> [...]  HONEST-FAIL — prints LUT_UNSCRIPTABLE, exits 65.

Platform limitation (Dimension #494): AE exposes Apply Color LUT2's
file-path property as PropertyValueType.NO_VALUE, so no script can set
it. There is no code path that injects a LUT into AE; none is planned.
LUT *synthesis* above works locally and headless. Levels Smart Match is
the supported in-AE backend.
"""


def main():
    if len(sys.argv) < 2:
        print(json.dumps(_USAGE_ERROR))
        sys.exit(1)

    subcommand = sys.argv[1]

    if subcommand in ("--help", "-h", "help"):
        print(_HELP_TEXT)
        return

    if subcommand == "inject":
        _inject_honest_fail()

    if subcommand == "validate":
        if len(sys.argv) < 3:
            print(json.dumps({"status": "ERROR", "error": "Usage: lut_cli.py validate <path>"}))
            sys.exit(1)
        print(json.dumps(_validate(sys.argv[2])))
        return

    if subcommand == "derive":
        # Reused as-is (own argparse setup, prints its own JSON) — just
        # reslice sys.argv to what it expects as a standalone script,
        # matching the [prog, *rest] shape argparse's implicit
        # sys.argv[1:] read relies on.
        from scripts.derive_lut import main as _derive_main
        sys.argv = ["derive_lut.py"] + sys.argv[2:]
        sys.exit(_derive_main())

    if subcommand == "derive-parametric":
        from scripts.derive_parametric_match import main as _derive_parametric_main
        sys.argv = ["derive_parametric_match.py"] + sys.argv[2:]
        sys.exit(_derive_parametric_main())

    if subcommand == "derive-smart":
        from scripts.derive_smart_match import main as _derive_smart_main
        sys.argv = ["derive_smart_match.py"] + sys.argv[2:]
        sys.exit(_derive_smart_main())

    print(json.dumps(_USAGE_ERROR))
    sys.exit(1)


if __name__ == "__main__":
    main()
