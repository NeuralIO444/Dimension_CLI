"""Comment-gardener CLI shim — scans a scrape manifest and prints a
JSON report the CEP panel can read to show / hide the warning banner.

Output schema (one JSON object on stdout):
    {
      "total_layers":     int,
      "has_warnings":     bool,
      "foreign_count":    int,
      "malformed_count":  int,
      "mixed_count":      int,
      "legacy_count":     int,
      "stamped_count":    int,
      "foreign":   [{"index": 0, "name": "..", "comment": "..", "uid": "..|null"}, ...],
      "malformed": [...],
      "mixed":     [...]
    }

The panel uses the count totals + by-class lists to render the banner
(mirroring python/ui/tagging_page.py::_CommentGardenBanner).
"""

import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from core.comment_gardener import scan_comp, CommentClass
from models.scrape_manifest import ScrapeManifest


def _serialize(items):
    return [
        {
            "index":   s.layer_index,
            "name":    s.layer_name,
            "comment": s.comment_preview,
            "uid":     s.uid,
        }
        for s in items
    ]


def main():
    if len(sys.argv) < 2:
        print("Usage: comment_garden_cli.py <manifest_path>", file=sys.stderr)
        sys.exit(1)

    manifest_path = sys.argv[1]
    if not os.path.exists(manifest_path):
        print(f"Manifest not found: {manifest_path}", file=sys.stderr)
        sys.exit(2)

    with open(manifest_path, 'r', encoding='utf-8') as f:
        manifest = ScrapeManifest.model_validate_json(f.read())

    report = scan_comp(manifest)

    out = {
        "total_layers":    report.total_layers,
        "has_warnings":    report.has_warnings,
        "foreign_count":   report.foreign_count,
        "malformed_count": report.malformed_count,
        "mixed_count":     report.mixed_count,
        "legacy_count":    report.legacy_count,
        "stamped_count":   report.stamped_count,
        "foreign":   _serialize(report.by_class.get(CommentClass.FOREIGN, [])),
        "malformed": _serialize(report.by_class.get(CommentClass.DIMENSION_TAG_MALFORMED, [])),
        "mixed":     _serialize(report.by_class.get(CommentClass.MIXED, [])),
    }
    print(json.dumps(out))


if __name__ == "__main__":
    main()
