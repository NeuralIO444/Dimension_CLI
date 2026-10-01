"""Studio-profile CLI shim — lets the CEP panel inspect and edit
profiles from the Studio Profile editor + tag-picker promote-to-rule.

Subcommands:
    list                       → list every profile with id, display name, rule_count
    show <id>                  → full dump of one profile (rules, gravities, safe_area, etc.)
    add-rule <id> ...          → append a prefix→tag rule
    update-rule <id> <idx> ... → change one rule by index
    delete-rule <id> <idx>     → remove one rule by index
    create <new_id> --extends <base_id> [--display-name <name>]
                               → create a new custom profile, cloned from
                                 a base profile's rules (#401)
    delete <id>                → delete a custom profile (#401)

All rule/field edits route through StudioProfileRegistry.save() so YAML
on disk stays canonical and the in-memory cache is invalidated.
`create`/`delete` route through save_user_profile()/delete_profile()
instead — a separate JSON-backed persistence path
(data/user_profile_storage.py) for whole custom profiles, distinct from
per-field YAML edits on an already-existing profile.
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from logic.studio_profile_registry import REGISTRY
from models.studio_profile import ProfileRule, SafeArea, StudioProfile


def _add_rule_args(p):
    p.add_argument("--prefix", required=True)
    p.add_argument("--tag",    required=True)
    p.add_argument("--gravity", default="center")
    p.add_argument("--scale",  type=float, default=1.0)
    p.add_argument("--weight", default="normal")


def _update_rule_args(p):
    # All optional — only fields explicitly passed get updated.
    p.add_argument("--prefix")
    p.add_argument("--tag")
    p.add_argument("--gravity")
    p.add_argument("--scale",  type=float)
    p.add_argument("--weight")


def main():
    parser = argparse.ArgumentParser(prog="profile")
    sub = parser.add_subparsers(dest="op", required=True)

    sub.add_parser("list", help="List every profile with id + rule count")

    sh = sub.add_parser("show", help="Dump one profile's full rule list")
    sh.add_argument("profile_id")

    add = sub.add_parser("add-rule", help="Append a prefix→tag rule")
    add.add_argument("--profile", required=True)
    _add_rule_args(add)

    up = sub.add_parser("update-rule", help="Update one rule by index")
    up.add_argument("--profile", required=True)
    up.add_argument("--index", type=int, required=True,
                    help="0-based index into the profile's prefixes list")
    _update_rule_args(up)

    rm = sub.add_parser("delete-rule", help="Remove one rule by index")
    rm.add_argument("--profile", required=True)
    rm.add_argument("--index", type=int, required=True)

    # ── Advanced fields (#5) ──────────────────────────────────────
    meta = sub.add_parser("set-meta",
                          help="Edit profile display_name / description / author / color")
    meta.add_argument("--profile", required=True)
    meta.add_argument("--display-name")
    meta.add_argument("--description")
    meta.add_argument("--author")
    meta.add_argument("--color")

    sa = sub.add_parser("set-safe-area",
                        help="Set the profile's safe-area inset fractions (0..1 per edge)")
    sa.add_argument("--profile", required=True)
    sa.add_argument("--top", type=float)
    sa.add_argument("--right", type=float)
    sa.add_argument("--bottom", type=float)
    sa.add_argument("--left", type=float)

    sto = sub.add_parser("set-type-override",
                         help="Set the per-tag scale multiplier (e.g. HERO=1.15)")
    sto.add_argument("--profile", required=True)
    sto.add_argument("--tag", required=True)
    sto.add_argument("--scale", type=float, required=True)

    dto = sub.add_parser("delete-type-override",
                         help="Remove a per-tag scale multiplier")
    dto.add_argument("--profile", required=True)
    dto.add_argument("--tag", required=True)

    # ── Whole-profile lifecycle (#401) ─────────────────────────────
    cr = sub.add_parser("create",
                        help="Create a new custom profile, cloned from a base profile")
    cr.add_argument("new_id")
    cr.add_argument("--extends", required=True,
                    help="Base profile id to clone rules from")
    cr.add_argument("--display-name")

    de = sub.add_parser("delete", help="Delete a custom profile")
    de.add_argument("profile_id")

    args = parser.parse_args()

    if args.op == "list":
        out = []
        for p in REGISTRY.list_profiles():
            out.append({
                "id":           p.id,
                "display_name": p.display_name or p.id,
                "description":  getattr(p, "description", "") or "",
                "extends":      getattr(p, "extends", None),
                "rule_count":   len(p.prefixes),
                "is_base":      p.id in {"default", "ooh", "social", "theatrical"},
            })
        print(json.dumps(out))
        return

    if args.op == "show":
        profile = REGISTRY.get(args.profile_id)
        if profile is None:
            print(f"Profile '{args.profile_id}' not found.", file=sys.stderr)
            sys.exit(2)
        # Type-overrides may be missing on legacy profiles.
        try:
            type_overrides = dict(profile.type_overrides or {})
        except Exception:
            type_overrides = {}
        print(json.dumps({
            "id":             profile.id,
            "display_name":   profile.display_name or profile.id,
            "description":    getattr(profile, "description", "") or "",
            "extends":        getattr(profile, "extends", None),
            "revision":       getattr(profile, "revision", "") or "",
            "author":         getattr(profile, "author", "") or "",
            "color":          getattr(profile, "color", "") or "",
            # Track B / B3 — rules now carry is_inherited/source_profile
            # so the CEP editor can style inherited rules distinctly and
            # offer an Override action, without re-deriving the parent
            # diff client-side.
            "rules":          REGISTRY.rule_provenance(profile),
            "safe_area":      profile.safe_area.model_dump() if hasattr(profile, "safe_area") and profile.safe_area else None,
            "type_overrides": type_overrides,
        }))
        return

    # ── Whole-profile lifecycle (#401) — handled before the generic
    # mutate scaffolding below since these ops key off `new_id`/
    # `profile_id`, not `--profile`.
    if args.op == "create":
        base = REGISTRY.get(args.extends)
        if base is None:
            print(f"Base profile '{args.extends}' not found.", file=sys.stderr)
            sys.exit(2)
        if REGISTRY.get(args.new_id) is not None:
            print(f"Profile '{args.new_id}' already exists.", file=sys.stderr)
            sys.exit(2)
        new_profile = StudioProfile(
            id=args.new_id,
            display_name=args.display_name or args.new_id,
            extends=args.extends,
            color=base.color,
            prefixes=[r.model_copy() for r in base.prefixes],
            type_overrides=dict(base.type_overrides or {}),
            safe_area=base.safe_area.model_copy() if base.safe_area else SafeArea(),
        )
        try:
            path = REGISTRY.save_user_profile(new_profile)
        except ValueError as e:
            print(str(e), file=sys.stderr)
            sys.exit(2)
        print(json.dumps({
            "id":       new_profile.id,
            "extends":  args.extends,
            "path":     path,
        }))
        return

    if args.op == "delete":
        if REGISTRY.get(args.profile_id) is None:
            print(f"Profile '{args.profile_id}' not found.", file=sys.stderr)
            sys.exit(2)
        try:
            REGISTRY.delete_profile(args.profile_id)
        except ValueError as e:
            print(str(e), file=sys.stderr)
            sys.exit(2)
        print(json.dumps({"id": args.profile_id, "deleted": True}))
        return

    # All mutate ops share the same load + save scaffolding.
    profile = REGISTRY.get(args.profile)
    if profile is None:
        print(f"Profile '{args.profile}' not found.", file=sys.stderr)
        sys.exit(2)

    if args.op == "add-rule":
        # De-dup: silently succeed when the exact rule already exists.
        for existing in profile.prefixes:
            if existing.match == args.prefix and existing.tag == args.tag:
                print(json.dumps({
                    "profile":    profile.id,
                    "added":      existing.model_dump(),
                    "rule_count": len(profile.prefixes),
                    "noop":       True,
                }))
                return
        rule = ProfileRule(
            match=args.prefix, tag=args.tag, gravity=args.gravity,
            scale=args.scale, weight=args.weight,
        )
        profile.prefixes.append(rule)
        REGISTRY.save(profile)
        print(json.dumps({
            "profile":    profile.id,
            "added":      rule.model_dump(),
            "rule_count": len(profile.prefixes),
        }))
        return

    if args.op == "update-rule":
        if args.index < 0 or args.index >= len(profile.prefixes):
            print(f"Index {args.index} out of range for {profile.id} "
                  f"(0..{len(profile.prefixes) - 1})", file=sys.stderr)
            sys.exit(2)
        old = profile.prefixes[args.index]
        # Build a fresh ProfileRule rather than mutating in place so
        # pydantic runs its validators (prefix non-empty, scale range,
        # tag in registry).
        new = ProfileRule(
            match  =args.prefix  if args.prefix  is not None else old.match,
            tag    =args.tag     if args.tag     is not None else old.tag,
            gravity=args.gravity if args.gravity is not None else old.gravity,
            scale  =args.scale   if args.scale   is not None else old.scale,
            weight =args.weight  if args.weight  is not None else old.weight,
        )
        profile.prefixes[args.index] = new
        REGISTRY.save(profile)
        print(json.dumps({
            "profile":    profile.id,
            "index":      args.index,
            "before":     old.model_dump(),
            "after":      new.model_dump(),
            "rule_count": len(profile.prefixes),
        }))
        return

    if args.op == "delete-rule":
        if args.index < 0 or args.index >= len(profile.prefixes):
            print(f"Index {args.index} out of range for {profile.id} "
                  f"(0..{len(profile.prefixes) - 1})", file=sys.stderr)
            sys.exit(2)
        removed = profile.prefixes.pop(args.index)
        REGISTRY.save(profile)
        print(json.dumps({
            "profile":    profile.id,
            "removed":    removed.model_dump(),
            "rule_count": len(profile.prefixes),
        }))
        return

    # ── Advanced field handlers (#5) ──────────────────────────────

    if args.op == "set-meta":
        changes = {}
        if args.display_name is not None:
            profile.display_name = args.display_name; changes["display_name"] = args.display_name
        if args.description is not None:
            profile.description = args.description; changes["description"] = args.description
        if args.author is not None:
            profile.author = args.author; changes["author"] = args.author
        if args.color is not None:
            profile.color = args.color; changes["color"] = args.color
        if not changes:
            print(json.dumps({"profile": profile.id, "noop": True,
                              "reason": "no fields passed"}))
            return
        REGISTRY.save(profile)
        print(json.dumps({"profile": profile.id, "updated": changes}))
        return

    if args.op == "set-safe-area":
        sa = profile.safe_area or SafeArea()
        kwargs = {
            "top":    args.top    if args.top    is not None else sa.top,
            "right":  args.right  if args.right  is not None else sa.right,
            "bottom": args.bottom if args.bottom is not None else sa.bottom,
            "left":   args.left   if args.left   is not None else sa.left,
        }
        new_sa = SafeArea(**kwargs)
        profile.safe_area = new_sa
        REGISTRY.save(profile)
        print(json.dumps({"profile": profile.id, "safe_area": new_sa.model_dump()}))
        return

    if args.op == "set-type-override":
        overrides = dict(profile.type_overrides or {})
        overrides[args.tag] = args.scale
        profile.type_overrides = overrides
        REGISTRY.save(profile)
        print(json.dumps({"profile": profile.id, "tag": args.tag,
                          "scale": args.scale, "count": len(overrides)}))
        return

    if args.op == "delete-type-override":
        overrides = dict(profile.type_overrides or {})
        removed = overrides.pop(args.tag, None)
        if removed is None:
            print(json.dumps({"profile": profile.id, "tag": args.tag,
                              "noop": True}))
            return
        profile.type_overrides = overrides
        REGISTRY.save(profile)
        print(json.dumps({"profile": profile.id, "tag": args.tag,
                          "removed_scale": removed, "count": len(overrides)}))
        return


if __name__ == "__main__":
    main()
