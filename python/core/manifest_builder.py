# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/core/manifest_builder.py
Dimension Engine v5.9 — Structural Cryptographic Hash Generator

Computes a lightweight structural hash of composition timelines to prevent
injecting conformed properties into altered or corrupted timelines.
"""

from typing import List, Any, Dict, Union
import hashlib
from collections import defaultdict


def _get_val(obj: Any, key: str) -> Any:
    """Helper to safely extract a field from either a dictionary or object."""
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def calculate_state_hash(layers: List[Union[Dict[str, Any], Any]]) -> str:
    """
    Calculate a lightweight SHA-256 hash representing the structural state
    of the target compositions.
    
    The hash is derived from the concatenated string of:
    comp_id + ":" + total_layer_count + ":" + ordered_layer_names + ":" + ordered_layer_ids
    for each target comp (sorted by comp_id and joined by newlines).
    """
    if not layers:
        # Return hash of empty string if no layers are present
        return hashlib.sha256(b"").hexdigest()

    # Determine if containing_comp_id is populated anywhere
    has_comp_id = any(_get_val(l, "containing_comp_id") is not None for l in layers)

    # Group layers by composition ID
    comps = defaultdict(list)
    for layer in layers:
        comp_id = _get_val(layer, "containing_comp_id") if has_comp_id else 0
        if comp_id is None:
            comp_id = 0
        comps[comp_id].append(layer)

    comp_strings = []
    # Sort comps by ID to ensure a deterministic order
    for comp_id in sorted(comps.keys()):
        # Sort layers within the comp by their 1-based index
        comp_layers = sorted(comps[comp_id], key=lambda x: _get_val(x, "index") or 0)
        total_layers = len(comp_layers)
        
        ordered_names = []
        ordered_ids = []
        for l in comp_layers:
            name = _get_val(l, "name")
            if name is None:
                name = ""
            # Ensure special characters in names are preserved exactly
            ordered_names.append(str(name))
            
            lid = _get_val(l, "id")
            if lid is None:
                lid = 0
            ordered_ids.append(str(lid))
            
        names_str = ",".join(ordered_names)
        ids_str = ",".join(ordered_ids)
        
        comp_str = f"{comp_id}:{total_layers}:{names_str}:{ids_str}"
        comp_strings.append(comp_str)

    full_string = "\n".join(comp_strings)
    return hashlib.sha256(full_string.encode("utf-8")).hexdigest()
