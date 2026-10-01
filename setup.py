# (c) 2026 NeuralIO 444
# Licensed under PolyForm Noncommercial 1.0.0 + commercial. See LICENSE.

"""Copy repo-root config/ into the wheel as dimension/bundled_config.

pyproject.toml is the project metadata. This file only exists so the
build copies runtime data that lives outside the python/ tree.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py

_SKIP = shutil.ignore_patterns("*.md", "SOURCES.md")


class build_py_with_config(build_py):
    def run(self) -> None:
        super().run()
        src = Path("config")
        if not src.is_dir():
            return
        dest = Path(self.build_lib) / "dimension" / "bundled_config"
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(src, dest, ignore=_SKIP)


setup(cmdclass={"build_py": build_py_with_config})
