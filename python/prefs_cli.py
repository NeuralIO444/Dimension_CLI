"""Preferences CLI shim — lets the CEP panel read or write the
persisted preferences singleton (python/logic/preferences_state.py).

Usage:
    prefs_cli.py get                    → JSON dict of all known prefs
    prefs_cli.py set <key> <value>      → set + persist a single pref
    prefs_cli.py set-many <json>        → set + persist many prefs in
                                           one process, one save()

For the CEP MVP only `enable_soe` is wired through the UI; this shim
is intentionally general so future settings (default profile, theme,
etc.) get added without churning the CLI surface.

Value parsing for `set`/`set-many`: "true"/"false"/"1"/"0" become
bool; otherwise the value is stored as a string. Floats / ints can be
added when a setting needs them.

`set-many` (issue #474): license.js's activation flow used to chain 9
sequential `set` spawns plus a 10th for License.init() — ~53s against
the frozen binary's ~5.3s/spawn cost, all dead air during a paying
customer's first action. `<json>` is a single JSON object of
key/value pairs, e.g. '{"license_key": "ABC", "license_status":
"trial"}'. Every value is coerced exactly like `set` does (the CLI
boundary is still string-shaped — CEP callers JSON.stringify their
values first, same as they already stringify a single `set` value),
all keys are validated before anything is written, and the whole
batch is persisted with exactly one `preferences.save()` call instead
of one per key.
"""

import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from logic.preferences_state import preferences


def _coerce(s: str):
    lower = s.lower()
    if lower in ("true", "1", "yes", "on"):  return True
    if lower in ("false", "0", "no", "off"): return False
    try:
        i = int(s)
        return i
    except ValueError:
        pass
    try:
        f = float(s)
        return f
    except ValueError:
        pass
    return s


def main():
    if len(sys.argv) < 2:
        print('Usage: prefs_cli.py {get|set <key> <value>}', file=sys.stderr)
        sys.exit(1)

    op = sys.argv[1]

    if op == 'get':
        # _DEFAULTS holds the schema; merge with the loaded state so
        # the panel sees both defaults and any persisted overrides.
        preferences.reload()
        out = {}
        for key, default in preferences._DEFAULTS.items():
            out[key] = getattr(preferences, key, default)
        print(json.dumps(out))
        return

    if op == 'set':
        if len(sys.argv) < 4:
            print('Usage: prefs_cli.py set <key> <value>', file=sys.stderr)
            sys.exit(1)
        key = sys.argv[2]
        value = _coerce(sys.argv[3])
        if key not in preferences._DEFAULTS:
            print(f'Unknown preference key: {key}', file=sys.stderr)
            sys.exit(1)
        setattr(preferences, key, value)
        preferences.save()
        print(json.dumps({key: value}))
        return

    if op == 'set-many':
        if len(sys.argv) < 3:
            print('Usage: prefs_cli.py set-many <json-object>', file=sys.stderr)
            sys.exit(1)
        try:
            pairs = json.loads(sys.argv[2])
        except json.JSONDecodeError as e:
            print(f'set-many: invalid JSON: {e}', file=sys.stderr)
            sys.exit(1)
        if not isinstance(pairs, dict):
            print('set-many: expected a JSON object of {key: value}', file=sys.stderr)
            sys.exit(1)

        unknown = [k for k in pairs if k not in preferences._DEFAULTS]
        if unknown:
            print(f'Unknown preference key(s): {", ".join(unknown)}', file=sys.stderr)
            sys.exit(1)

        # A value is coerced exactly like `set` when it arrives as a
        # string (the normal case — CEP callers String()-ify before
        # JSON.stringify-ing the batch, matching `set`'s single-value
        # contract); a value that is already a real JSON bool/int/float
        # is used as-is rather than run through the string-only coercer.
        written = {}
        for key, raw in pairs.items():
            value = _coerce(raw) if isinstance(raw, str) else raw
            setattr(preferences, key, value)
            written[key] = value

        preferences.save()
        print(json.dumps(written))
        return

    print(f'Unknown op: {op}', file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main()
