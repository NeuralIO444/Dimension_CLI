# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/tests/test_cryptographic_handshake.py
Unit tests for Python hash generation, JSX hash generation parity, and pre-flight state drift verification.
"""

import os
import sys
import json
import shutil
import hashlib
import subprocess
import pytest

import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.manifest_builder import calculate_state_hash
from models.conformed_manifest import MirrorTreeEntry

# Locate Node.js on the path
NODE_BIN = shutil.which("node")
HAS_NODE = NODE_BIN is not None

# Absolute path to Babysitter.jsx
BABYSITTER_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "Scripts", "Dimension_Assets", "Babysitter.jsx")
)


def run_jsx_in_node(js_code: str) -> str:
    """Helper to run arbitrary JS in Node.js with Babysitter.jsx loaded."""
    with open(BABYSITTER_PATH, "r", encoding="utf-8") as f:
        babysitter_code = f.read()

    full_script = f"""
    // Mock basic AE ExtendScript global environment
    var $ = {{ global: {{}} }};
    var app = {{ project: {{ activeItem: null }} }};
    var CompItem = function() {{}};
    var FolderItem = function() {{}};
    
    // Load Babysitter.jsx
    {babysitter_code}
    
    // Assign global Babysitter if it wasn't registered automatically
    var Babysitter = $.global.Babysitter;
    
    // Execute custom JS
    {js_code}
    """
    
    # Written to a temp file rather than passed via `node -e <script>` —
    # Babysitter.jsx is large enough that the combined argv+envp size can
    # exceed the kernel's ARG_MAX in some environments (observed E2BIG in
    # CI), even though the script alone is well under any single-string
    # limit.
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".js", encoding="utf-8", delete=False
    ) as f:
        f.write(full_script)
        script_path = f.name

    try:
        res = subprocess.run(
            [NODE_BIN, script_path],
            capture_output=True,
            text=True,
            check=True
        )
    finally:
        os.remove(script_path)
    return res.stdout.strip()


def test_python_hash_generation():
    """Verify calculate_state_hash correctly groups, sorts, and hashes conformed layers in Python."""
    layers = [
        {"id": 1, "index": 1, "name": "Layer A", "containing_comp_id": 10},
        {"id": 2, "index": 2, "name": "Layer B", "containing_comp_id": 10},
        {"id": 3, "index": 1, "name": "Nested Layer", "containing_comp_id": 20},
    ]
    
    # Expected composition strings:
    # Comp 10: "10:2:Layer A,Layer B:1,2"
    # Comp 20: "20:1:Nested Layer:3"
    # Joined: "10:2:Layer A,Layer B:1,2\n20:1:Nested Layer:3"
    expected_str = "10:2:Layer A,Layer B:1,2\n20:1:Nested Layer:3"
    expected_hash = hashlib.sha256(expected_str.encode("utf-8")).hexdigest()
    
    actual_hash = calculate_state_hash(layers)
    assert actual_hash == expected_hash


@pytest.mark.skipif(not HAS_NODE, reason="Node.js is required to run JSX cross-language tests")
def test_cross_language_parity():
    """Verify that Python and JSX SHA-256 hash implementations produce identical outputs for diverse inputs."""
    test_cases = [
        "hello world",
        "",
        "Comp_1:3:Background,Text,Logo:10,20,30",
        "Unicode characters: 📈, 🎨, 🚀, ★, ✔",
        "Special characters: 'quotes', \"double quotes\", \\backslashes\\, /slashes/, [brackets], {braces}, :colons:, ,commas,",
        "Combined: 10:2:Banner [New] 🚀,Legal:101,102\n20:1:Render - Standard:201",
    ]
    
    for s in test_cases:
        py_hash = hashlib.sha256(s.encode("utf-8")).hexdigest()
        
        js_code = f"""
        console.log(Babysitter._sha256({json.dumps(s)}));
        """
        js_hash = run_jsx_in_node(js_code)
        
        assert js_hash == py_hash, f"Hash mismatch for string: {s!r}\nPython: {py_hash}\nJSX: {js_hash}"


@pytest.mark.skipif(not HAS_NODE, reason="Node.js is required to run JSX cross-language tests")
def test_jsx_verify_state_hash_success():
    """Verify that verifyStateHash succeeds when the AE project state matches the manifest state_hash."""
    # 1. Build a conformed manifest state in Python and compute its hash
    layers = [
        {"id": 101, "index": 1, "name": "Text Layer", "containing_comp_id": 42},
        {"id": 102, "index": 2, "name": "BG Color", "containing_comp_id": 42},
    ]
    state_hash = calculate_state_hash(layers)
    
    # 2. Mock this composition in JS and run verifyStateHash
    js_code = f"""
    // Mock target comp
    var mockComp = new CompItem();
    mockComp.id = 42;
    mockComp.name = "MainComp";
    mockComp.numLayers = 2;
    mockComp.layer = function(idx) {{
        if (idx === 1) return {{ id: 101, name: "Text Layer" }};
        if (idx === 2) return {{ id: 102, name: "BG Color" }};
        return null;
    }};
    
    // Wire up Babysitter mocks
    Babysitter._findCompByName = function(name) {{
        return mockComp;
    }};
    
    var manifest = {{
        state_hash: "{state_hash}",
        expected_comp_name: "MainComp",
        layers: [
            {{ id: 101, index: 1, name: "Text Layer", containing_comp_id: 42 }},
            {{ id: 102, index: 2, name: "BG Color", containing_comp_id: 42 }}
        ]
    }};
    
    // verifyStateHash returns true on success
    var res = Babysitter.verifyStateHash(manifest, null);
    console.log(res);
    """
    
    stdout = run_jsx_in_node(js_code)
    assert stdout == "true"


@pytest.mark.skipif(not HAS_NODE, reason="Node.js is required to run JSX cross-language tests")
def test_jsx_verify_state_hash_drift_mismatch():
    """Verify verifyStateHash throws a StateDriftError on layer renaming."""
    layers = [
        {"id": 101, "index": 1, "name": "Text Layer", "containing_comp_id": 42},
        {"id": 102, "index": 2, "name": "BG Color", "containing_comp_id": 42},
    ]
    state_hash = calculate_state_hash(layers)
    
    # Mock the composition with a different name on the first layer ("Text Layer Edited")
    js_code = f"""
    var mockComp = new CompItem();
    mockComp.id = 42;
    mockComp.name = "MainComp";
    mockComp.numLayers = 2;
    mockComp.layer = function(idx) {{
        if (idx === 1) return {{ id: 101, name: "Text Layer Edited" }};
        if (idx === 2) return {{ id: 102, name: "BG Color" }};
        return null;
    }};
    
    Babysitter._findCompByName = function(name) {{
        return mockComp;
    }};
    
    var manifest = {{
        state_hash: "{state_hash}",
        expected_comp_name: "MainComp",
        layers: [
            {{ id: 101, index: 1, name: "Text Layer", containing_comp_id: 42 }},
            {{ id: 102, index: 2, name: "BG Color", containing_comp_id: 42 }}
        ]
    }};
    
    Babysitter._state = {{ inMemoryLogs: [] }};
    
    try {{
        Babysitter.verifyStateHash(manifest, null);
        console.log(JSON.stringify(Babysitter._state.inMemoryLogs));
    }} catch(e) {{
        console.log("THROWN: " + e.message);
    }}
    """
    
    stdout = run_jsx_in_node(js_code)
    assert "THROWN:" in stdout
    assert "StateDriftError" in stdout
    assert "Expected hash:" in stdout


@pytest.mark.skipif(not HAS_NODE, reason="Node.js is required to run JSX cross-language tests")
def test_jsx_verify_state_hash_drift_layer_count():  # noqa: E302
    """Verify verifyStateHash throws a StateDriftError when layer count changes."""
    layers = [
        {"id": 101, "index": 1, "name": "Text Layer", "containing_comp_id": 42},
        {"id": 102, "index": 2, "name": "BG Color", "containing_comp_id": 42},
    ]
    state_hash = calculate_state_hash(layers)
    
    # Mock comp has 3 layers (artist added one)
    js_code = f"""
    var mockComp = new CompItem();
    mockComp.id = 42;
    mockComp.name = "MainComp";
    mockComp.numLayers = 3;
    mockComp.layer = function(idx) {{
        if (idx === 1) return {{ id: 101, name: "Text Layer" }};
        if (idx === 2) return {{ id: 102, name: "BG Color" }};
        if (idx === 3) return {{ id: 103, name: "New Art" }};
        return null;
    }};
    
    Babysitter._findCompByName = function(name) {{
        return mockComp;
    }};
    
    var manifest = {{
        state_hash: "{state_hash}",
        expected_comp_name: "MainComp",
        layers: [
            {{ id: 101, index: 1, name: "Text Layer", containing_comp_id: 42 }},
            {{ id: 102, index: 2, name: "BG Color", containing_comp_id: 42 }}
        ]
    }};
    
    Babysitter._state = {{ inMemoryLogs: [] }};
    
    try {{
        Babysitter.verifyStateHash(manifest, null);
        console.log(JSON.stringify(Babysitter._state.inMemoryLogs));
    }} catch(e) {{
        console.log("THROWN: " + e.message);
    }}
    """
    
    stdout = run_jsx_in_node(js_code)
    assert "THROWN:" in stdout
    assert "StateDriftError" in stdout
    assert "Expected hash:" in stdout


@pytest.mark.skipif(not HAS_NODE, reason="Node.js is required to run JSX cross-language tests")
def test_jsx_verify_state_hash_drift_bypass_flag():
    """allow_state_hash_bypass logs WARNING and continues instead of throwing."""
    layers = [
        {"id": 101, "index": 1, "name": "Text Layer", "containing_comp_id": 42},
    ]
    state_hash = calculate_state_hash(layers)

    js_code = f"""
    var mockComp = new CompItem();
    mockComp.id = 42;
    mockComp.name = "MainComp";
    mockComp.numLayers = 1;
    mockComp.layer = function(idx) {{
        if (idx === 1) return {{ id: 101, name: "Renamed Layer" }};
        return null;
    }};

    Babysitter._findCompByName = function(name) {{
        return mockComp;
    }};

    var manifest = {{
        state_hash: "{state_hash}",
        allow_state_hash_bypass: true,
        expected_comp_name: "MainComp",
        layers: [
            {{ id: 101, index: 1, name: "Text Layer", containing_comp_id: 42 }}
        ]
    }};

    Babysitter._state = {{ inMemoryLogs: [] }};
    var res = Babysitter.verifyStateHash(manifest, null);
    console.log(res ? "true" : "false");
    console.log(JSON.stringify(Babysitter._state.inMemoryLogs));
    """

    stdout = run_jsx_in_node(js_code)
    assert "true" in stdout
    assert "allow_state_hash_bypass" in stdout
    assert "THROWN:" not in stdout


# ─────────────────────────────────────────────────────────────────────────────
# Mirror-tree filter contract (Q2 from StateDriftError blast radius)
# ─────────────────────────────────────────────────────────────────────────────

class TestMirrorTreeFilterContract:
    """
    Pins the exporter's mirror-tree filter behaviour.

    Before the fix, slice_and_export sent all conformed layers (including
    GUIDE precomp tick-mark layers) to Babysitter. Babysitter would fall
    back to the root output comp for layers whose containing_comp_id was
    not in the mirror tree, corrupting content layers. The fix filters
    conformed_layers_data at the top of slice_and_export so chunks,
    total_layers, and state_hash all cover only the mirror-tree comps.
    """

    _CT = {"is_root": True, "position": [0,0,0], "scale": [100,100,100],
            "anchor": [0,0,0], "rotation": 0.0}

    ROOT_LAYERS = [
        {"id": 1, "index": 1, "name": "Text Layer", "containing_comp_id": 100,
         "conformed_transforms": _CT},
        {"id": 2, "index": 2, "name": "Background",  "containing_comp_id": 100,
         "conformed_transforms": _CT},
    ]
    GUIDE_LAYERS = [
        {"id": 10, "index": 1, "name": "12", "containing_comp_id": 999,
         "conformed_transforms": _CT},
        {"id": 11, "index": 2, "name": "11", "containing_comp_id": 999,
         "conformed_transforms": _CT},
        {"id": 12, "index": 3, "name": "10", "containing_comp_id": 999,
         "conformed_transforms": _CT},
    ]
    ALL_LAYERS = ROOT_LAYERS + GUIDE_LAYERS

    @staticmethod
    def _mirror_tree():
        return [
            MirrorTreeEntry(
                source_comp_name="MainComp_tiktok",
                output_name="MainComp_tiktok",
                source_comp_id=100,
                is_root=True,
            )
        ]

    @staticmethod
    def _run_export(layers, mirror_tree=None):
        from logic.exporter import PayloadSlicer
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "Chunks"), exist_ok=True)
            slicer = PayloadSlicer(output_dir=os.path.join(tmp, "Chunks"))
            orig_dir = os.getcwd()
            os.chdir(tmp)
            try:
                slicer.slice_and_export(
                    conformed_layers_data=layers,
                    expected_comp_name="MainComp_tiktok",
                    mirror_tree=mirror_tree,
                )
                with open("chunk_manifest.json") as f:
                    manifest = json.load(f)
                chunk_names = []
                for cp in manifest.get("chunk_paths", []):
                    with open(cp) as f:
                        chunk = json.load(f)
                    chunk_names.extend(l["name"] for l in chunk.get("layers", []))
            finally:
                os.chdir(orig_dir)
        return manifest, chunk_names

    def test_without_mirror_tree_all_layers_exported(self):
        """Legacy path: no mirror_tree -> all 5 layers in chunks."""
        manifest, names = self._run_export(self.ALL_LAYERS, mirror_tree=None)
        assert manifest["total_layers"] == 5
        assert set(names) == {"Text Layer", "Background", "12", "11", "10"}

    def test_guide_layers_excluded_from_chunks(self):
        """GUIDE comp layers must not appear in exported chunks."""
        _, names = self._run_export(self.ALL_LAYERS, mirror_tree=self._mirror_tree())
        assert "12" not in names
        assert "11" not in names
        assert "10" not in names

    def test_root_layers_present_in_chunks(self):
        """Non-GUIDE layers must still be exported."""
        _, names = self._run_export(self.ALL_LAYERS, mirror_tree=self._mirror_tree())
        assert "Text Layer" in names
        assert "Background" in names

    def test_total_layers_reflects_filtered_count(self):
        """total_layers must be 2 (root comp only), not 5."""
        manifest, _ = self._run_export(self.ALL_LAYERS, mirror_tree=self._mirror_tree())
        assert manifest["total_layers"] == 2

    def test_state_hash_matches_filtered_subset(self):
        """state_hash must equal calculate_state_hash on the root-comp subset."""
        manifest, _ = self._run_export(self.ALL_LAYERS, mirror_tree=self._mirror_tree())
        assert manifest["state_hash"] == calculate_state_hash(self.ROOT_LAYERS)

    def test_state_hash_differs_from_unfiltered(self):
        """Filtered hash is distinct from the full 5-layer hash -- filter is active."""
        assert calculate_state_hash(self.ROOT_LAYERS) != calculate_state_hash(self.ALL_LAYERS)

    def test_legacy_path_hash_covers_all_layers(self):
        """Without mirror_tree, hash covers all 5 layers."""
        manifest, _ = self._run_export(self.ALL_LAYERS, mirror_tree=None)
        assert manifest["state_hash"] == calculate_state_hash(self.ALL_LAYERS)
