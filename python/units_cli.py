import json
import sys
import os
from models.scrape_manifest import ScrapeManifest
from core.placement_units import build_placement_units
from core.logger import log

def main():
    try:
        from logic.manifest_source import resolve_manifest_path
        from pathlib import Path
        manifest_path_str = resolve_manifest_path()
        if manifest_path_str is None:
            print(json.dumps({"error": "Manifest not found. Run dimension_engine survey first."}))
            sys.exit(1)
        session_dir = Path(manifest_path_str).parent
        manifest_path = session_dir / "manifest.json"
        overrides_path = session_dir / "unit_overrides.json"

        if not os.path.exists(manifest_path):
            print(json.dumps({"error": "Manifest not found. Run dimension_engine survey first."}))
            sys.exit(1)
            
        with open(manifest_path, "r") as f:
            manifest = ScrapeManifest.model_validate_json(f.read())
            
        overrides = None
        if os.path.exists(overrides_path):
            with open(overrides_path, "r") as f:
                overrides = json.load(f)
                
        # By calling build_placement_units, it will compute the placement resolution,
        # apply any overrides in-place, and then generate the diagnostic report.
        report = build_placement_units(manifest, overrides=overrides)
        
        # Output the JSON payload for the CEP panel to execFile
        print(report.model_dump_json(indent=2))
        
    except Exception as e:
        log.error("Units CLI failed", extra={"error": str(e)})
        print(json.dumps({"error": str(e)}))
        sys.exit(1)

if __name__ == "__main__":
    main()
