import sys
import os
import json

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from logic.studio_profile_registry import REGISTRY as ProfileRegistry
from logic.target_store import TargetStoreManager

def main():
    # 1. Get Profiles
    profiles = []
    for p in ProfileRegistry.list_profiles():
        profiles.append({
            "id": p.id,
            "name": p.display_name or p.id,
            "description": p.description
        })

    # 2. Get Targets (Formats)
    tsm = TargetStoreManager()
    formats_grouped = {}
    
    for target in tsm.all_targets():
        # E.g. target.subcategory might be "social", "theatrical", etc.
        cat = target.subcategory or "General"
        if cat not in formats_grouped:
            formats_grouped[cat] = []
            
        formats_grouped[cat].append({
            "id": target.id, # The unambiguous full ID e.g. builtin:social:tiktok_9_16 or just tiktok
            "name": target.label,
            "width": target.width,
            "height": target.height,
            "aspect_ratio": target.aspect_ratio,
            "aspect_label": target.aspect_label,
            "category": target.category.value if hasattr(target.category, "value") else str(target.category),
            "subcategory": target.subcategory,
            "duration": target.duration,
            "fps": target.fps,
            "metadata": target.metadata or {},
        })
        
    data = {
        "profiles": profiles,
        "formats_grouped": formats_grouped
    }
    
    # Print to stdout so Node.js can parse it
    print(json.dumps(data))

if __name__ == "__main__":
    main()
