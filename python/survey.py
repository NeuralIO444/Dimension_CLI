import argparse
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from models.scrape_manifest import ScrapeManifest
from stages.survey import resolve_studio_profile, run_survey


def main():
    # `--profile <id>` lets the CEP panel re-survey an existing
    # manifest under a different studio profile without setting the
    # registry's persisted active profile — the panel owns its own
    # active-profile UI state and we don't want background scrapes
    # to clobber it.
    parser = argparse.ArgumentParser(
        prog="survey",
        description="Run the surveyor heuristics over a scrape manifest in place.",
    )
    parser.add_argument("manifest", help="Path to scrape_manifest.json")
    parser.add_argument("--profile", default=None,
                        help="Studio profile id to use for prefix rules (defaults to the registry's active profile)")
    args = parser.parse_args()

    if not os.path.exists(args.manifest):
        print(f"Manifest not found: {args.manifest}", file=sys.stderr)
        sys.exit(2)

    with open(args.manifest, 'r', encoding='utf-8') as f:
        json_data = f.read()

    try:
        manifest = ScrapeManifest.model_validate_json(json_data)
    except Exception as e:
        print(f"Error parsing manifest: {e}", file=sys.stderr)
        sys.exit(1)

    if args.profile and resolve_studio_profile(args.profile) is None:
        print(f"Warning: profile '{args.profile}' not found in registry",
              file=sys.stderr)

    stats = run_survey(manifest, profile_id=args.profile)

    with open(args.manifest, 'w', encoding='utf-8') as f:
        f.write(manifest.model_dump_json(by_alias=True, indent=2))

    # Phase 1 (loud failures) — degradations surface on stdout so CLI and
    # panel callers see them without parsing the manifest.
    for w in (stats.get("warnings") or []):
        print(f"WARNING: {w}")

    print(f"Survey complete: {stats}")


if __name__ == "__main__":
    main()
