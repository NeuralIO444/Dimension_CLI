# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).

"""OSC 9;4 progress: detection, sequences, and the no-op path."""

from dimension.osc_progress import (
    OSC_PROGRESS_BEL,
    OSC_PROGRESS_PREFIX,
    OSC_PROGRESS_ST,
    create_osc_progress,
    sanitize_label,
    strip_osc_progress,
    supports_osc_progress,
)


def _frames(progress):
    seen = []
    progress._write = seen.append
    progress.enabled = True
    return seen


def test_support_requires_tty_and_known_terminal():
    env = {"TERM_PROGRAM": "ghostty"}
    assert supports_osc_progress(env, True) is True
    assert supports_osc_progress({"TERM_PROGRAM": "WezTerm"}, True) is True
    assert supports_osc_progress({"TERM_PROGRAM": "Canario"}, True) is True
    assert supports_osc_progress({"WT_SESSION": "abc"}, True) is True
    assert supports_osc_progress(env, False) is False
    assert supports_osc_progress({"TERM_PROGRAM": "Apple_Terminal"}, True) is False


def test_disable_wins_over_force():
    env = {"DIMENSION_NO_PROGRESS": "1", "DIMENSION_FORCE_PROGRESS": "1", "TERM_PROGRAM": "ghostty"}
    assert supports_osc_progress(env, True) is False
    assert supports_osc_progress(env, True, force=True) is False
    assert supports_osc_progress({"TERM_PROGRAM": "xterm"}, True, force=True) is True
    assert supports_osc_progress({"TERM_PROGRAM": "xterm"}, False, force=True) is False


def test_sequence_shape_and_label_sanitizing():
    seen = []
    progress = create_osc_progress(label="conform", is_tty=True, force=True, env={})
    progress._write = seen.append
    progress.set_percent(42.4, "conform")
    assert seen[0] == f"{OSC_PROGRESS_PREFIX}1;42;conform{OSC_PROGRESS_ST}"
    progress.set_indeterminate("scan\x1b]9;4;1;1\x07")
    assert "\x1b]9;4;1" not in seen[-1].split(";", 3)[-1].rstrip("\x1b\\")
    assert sanitize_label("  a\x1bb  ") == "ab"


def test_disabled_controller_is_silent():
    seen = []
    progress = create_osc_progress(disabled=True, is_tty=True, env={"TERM_PROGRAM": "ghostty"})
    progress._write = seen.append
    progress.set_indeterminate("conform")
    progress.done()
    progress.fail()
    assert seen == []
    assert progress.enabled is False


def test_percent_clamped_and_deduped():
    progress = create_osc_progress(is_tty=True, force=True, env={})
    seen = _frames(progress)
    progress.set_percent(-5)
    progress.set_percent(140)
    progress.set_percent(100)
    progress.set_percent(100)
    assert seen[0].startswith(f"{OSC_PROGRESS_PREFIX}1;0;")
    assert seen[1].startswith(f"{OSC_PROGRESS_PREFIX}1;100;")
    assert len(seen) == 2


def test_strip_removes_st_and_bel_and_unterminated_tail():
    raw = f"ok {OSC_PROGRESS_PREFIX}1;10;conform{OSC_PROGRESS_ST} next {OSC_PROGRESS_PREFIX}3;scan{OSC_PROGRESS_BEL} tail {OSC_PROGRESS_PREFIX}1;2"
    assert strip_osc_progress(raw) == "ok  next  tail "
