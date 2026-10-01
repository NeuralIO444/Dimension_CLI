# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""test_babysitter_synthetic_stress.py — Synthetic stress tests for Babysitter.

Pushes the new modular Babysitter engine through heavy synthetic workloads:
  1. Massive 50-chunk (250-layer) multi-stage pump execution
  2. Mid-inject abort under stress at chunk 25/50
  3. Extreme / corrupted value firewalling (NaN, Infinity, bounds clamping)
  4. Deep DAG mirror rewire target resolution (15+ nodes)
  5. Error recovery, undo group balancing, and state teardown integrity
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_BABYSITTER_PATH = _REPO_ROOT / "Scripts" / "Dimension_Assets" / "Babysitter.jsx"

NODE_BIN = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE_BIN is None, reason="node not found on PATH")


def _run_node_harness(script_body: str) -> dict:
    """Run an ExtendScript test harness script inside Node.js and parse JSON output."""
    babysitter_src = _BABYSITTER_PATH.read_text(encoding="utf-8")
    full_script = f"""
    // Mock Extended AE Environment for Babysitter Engine
    var $ = {{
        global: {{}},
        writeln: function(msg) {{}},
        gc: function() {{}}
    }};

    var scheduledTasks = [];
    var nextTaskId = 1;    var _itemsArray = [];
    var app = {{
        project: {{
            items: {{
                addComp: function(name, width, height, pixelAspect, duration, frameRate) {{
                    var comp = new CompItem(name, width, height);
                    comp.pixelAspect = pixelAspect || 1.0;
                    comp.duration = duration || 10.0;
                    comp.frameRate = frameRate || 30.0;
                    _itemsArray.push(comp);
                    app.project.numItems = _itemsArray.length;
                    return comp;
                }},
                addFolder: function(name) {{
                    var folder = new FolderItem(name);
                    _itemsArray.push(folder);
                    app.project.numItems = _itemsArray.length;
                    return folder;
                }}
            }},
            item: function(idx) {{
                return _itemsArray[idx - 1] || null;
            }},
            numItems: 0,
            activeItem: null,
            rootFolder: {{ id: 0, name: "Root" }},
            suspendRedraw: function() {{ return true; }}
        }},
        findMenuCommandId: function(name) {{ return 1001; }},
        executeCommand: function(id) {{ return true; }},
        scheduleTask: function(codeStr, delayMs, repeat) {{
            var id = nextTaskId++;
            scheduledTasks.push({{ id: id, code: codeStr, delay: delayMs, repeat: repeat }});
            return id;
        }},
        cancelTask: function(id) {{
            scheduledTasks = scheduledTasks.filter(function(t) {{ return t.id !== id; }});
        }},
        beginUndoGroup: function(name) {{}},
        endUndoGroup: function() {{}}
    }};

    // Mock AE DOM Constructors
    function Property(name, matchName) {{
        this.name = name;
        this.matchName = matchName || name;
        this.numKeys = 0;
        this.value = [0, 0, 0];
        this.dimensionsSeparated = false;
        this.expression = "";
        this.propertyType = 1;
        this._keyframes = [];
    }}
    Property.prototype.setValue = function(v) {{
        this.value = v;
    }};
    Property.prototype.setValueAtTime = function(t, v) {{
        this.numKeys++;
        this._keyframes.push({{ time: t, value: v }});
    }};
    Property.prototype.valueAtTime = function(t, pre) {{
        return this.value;
    }};
    Property.prototype.removeKey = function(idx) {{
        if (this.numKeys > 0) {{
            this.numKeys--;
            this._keyframes.splice(idx - 1, 1);
        }}
    }};
    Property.prototype.setInterpolationTypeAtKey = function(idx, inT, outT) {{
        this._interpolations = this._interpolations || [];
        this._interpolations[idx - 1] = {{ inType: inT, outType: outT }};
    }};

    function Layer(index, name, uid) {{
        this.index = index;
        this.name = name;
        this.comment = uid ? ("uid:" + uid) : "";
        this.locked = false;
        this.parent = null;
        this.transform = {{
            position: new Property("Position", "ADBE Position"),
            scale: new Property("Scale", "ADBE Scale"),
            rotation: new Property("Rotation", "ADBE Rotate Z"),
            opacity: new Property("Opacity", "ADBE Opacity"),
            anchorPoint: new Property("Anchor Point", "ADBE Anchor Point"),
            pointOfInterest: new Property("Point of Interest", "ADBE Point of Interest"),
            xPosition: new Property("X Position", "ADBE Position_0"),
            yPosition: new Property("Y Position", "ADBE Position_1"),
            zPosition: new Property("Z Position", "ADBE Position_2"),
            property: function(n) {{
                if (n === "X Position" || n === "ADBE Position_0") return this.xPosition;
                if (n === "Y Position" || n === "ADBE Position_1") return this.yPosition;
                if (n === "Z Position" || n === "ADBE Position_2") return this.zPosition;
                return this[n] || null;
            }}
        }};
        this.cameraOption = {{
            zoom: new Property("Zoom", "ADBE Camera Zoom"),
            focusDistance: new Property("Focus Distance", "ADBE Camera Focus Distance"),
            aperture: new Property("Aperture", "ADBE Camera Aperture"),
            blurLevel: new Property("Blur Level", "ADBE Camera Blur Level"),
            depthOfField: new Property("Depth of Field", "ADBE Camera Depth of Field"),
            property: function(n) {{
                if (n === "Zoom" || n === "ADBE Camera Zoom") return this.zoom;
                if (n === "Focus Distance" || n === "ADBE Camera Focus Distance") return this.focusDistance;
                if (n === "Aperture" || n === "ADBE Camera Aperture") return this.aperture;
                if (n === "Blur Level" || n === "ADBE Camera Blur Level") return this.blurLevel;
                if (n === "Depth of Field" || n === "ADBE Camera Depth of Field") return this.depthOfField;
                return this[n] || null;
            }}
        }};
        this.property = function(n) {{
            if (n === "Transform" || n === "ADBE Transform Group") return this.transform;
            if (n === "Camera Options" || n === "ADBE Camera Options Group") return this.cameraOption;
            return this.transform[n] || this.cameraOption[n] || null;
        }};
    }}
    Layer.prototype.copyToComp = function(targetComp) {{
        var uidMatch = this.comment.match(/uid:([^\\s]+)/);
        var uidVal = uidMatch ? uidMatch[1] : "";
        var copy = new Layer(1, this.name, uidVal);
        copy.locked = this.locked;
        copy.parent = this.parent;
        targetComp.layers.unshift(copy);
        for (var i = 0; i < targetComp.layers.length; i++) {{
            targetComp.layers[i].index = i + 1;
        }}
        targetComp.numLayers = targetComp.layers.length;
        return copy;
    }};

    function CompItem(name, width, height) {{
        this.id = Math.floor(Math.random() * 100000);
        this.name = name;
        this.width = width || 1920;
        this.height = height || 1080;
        this.layers = [];
        this.numLayers = 0;
        this.parentFolder = null;
        this.pixelAspect = 1.0;
        this.duration = 10.0;
        this.frameRate = 30.0;
    }}
    CompItem.prototype.layer = function(idx) {{
        return this.layers[idx - 1] || null;
    }};
    CompItem.prototype.openInViewer = function() {{
        app.project.activeItem = this;
    }};
    CompItem.prototype.duplicate = function() {{
        var dup = new CompItem(this.name + " Conformed", this.width, this.height);
        for (var i = 0; i < this.layers.length; i++) {{
            var l = this.layers[i];
            var dl = new Layer(l.index, l.name, "dup_" + l.index);
            dup.layers.push(dl);
        }}
        dup.numLayers = dup.layers.length;
        _itemsArray.push(dup);
        app.project.numItems = _itemsArray.length;
        return dup;
    }};

    function FolderItem(name) {{
        this.id = Math.floor(Math.random() * 100000);
        this.name = name;
        this.parentFolder = null;
        this.items = [];
    }}

    // Mock File & Folder I/O
    var fsMemory = {{}};
    function File(path) {{
        this.fsName = path || "";
        this.name = path ? path.split("/").pop().split("\\\\").pop() : "";
        this.encoding = "UTF-8";
        this.lineFeed = "Unix";
        this._buffer = "";
        this._mode = null;
    }}
    Object.defineProperty(File.prototype, "exists", {{
        get: function() {{ return fsMemory.hasOwnProperty(this.fsName); }}
    }});
    File.prototype.open = function(mode) {{
        this._mode = mode;
        if (mode === "r") {{
            this._buffer = fsMemory[this.fsName] || "";
        }} else if (mode === "w") {{
            this._buffer = "";
        }} else if (mode === "a") {{
            this._buffer = fsMemory[this.fsName] || "";
        }}
        return true;
    }};
    File.prototype.read = function() {{
        return this._buffer;
    }};
    File.prototype.write = function(str) {{
        this._buffer += str;
        fsMemory[this.fsName] = this._buffer;
        return true;
    }};
    File.prototype.close = function() {{
        if (this._mode === "w" || this._mode === "a") {{
            fsMemory[this.fsName] = this._buffer;
        }}
        this._mode = null;
        return true;
    }};
    File.prototype.remove = function() {{
        delete fsMemory[this.fsName];
        return true;
    }};
    File.prototype.copy = function(destPath) {{
        fsMemory[destPath] = fsMemory[this.fsName] || "";
        return true;
    }};
    File.prototype.rename = function(newName) {{
        var parts = this.fsName.split("/");
        parts.pop();
        var newPath = parts.join("/") + "/" + newName;
        fsMemory[newPath] = fsMemory[this.fsName];
        delete fsMemory[this.fsName];
        this.fsName = newPath;
        return true;
    }};

    function Folder(path) {{
        this.fsName = path || "";
        this.exists = true;
    }}
    Folder.prototype.create = function() {{ return true; }};

    // Load Babysitter
    {babysitter_src}

    // Runner Execution
    try {{
        {script_body}
    }} catch (fatalErr) {{
        console.log(JSON.stringify({{ status: "HARNESS_ERROR", error: fatalErr.toString(), stack: fatalErr.stack }}));
    }}
    """
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as tmp:
        tmp.write(full_script)
        tmp_name = tmp.name

    try:
        proc = subprocess.run(
            [NODE_BIN, tmp_name],
            capture_output=True,
            text=True,
            check=True,
        )
        return json.loads(proc.stdout.strip())
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


class TestSyntheticMultiChunkPipeline:
    """Stress Vector 1: 50-chunk (250-layer) multi-stage pump execution."""

    def test_50_chunk_lifecycle_executes_to_completion(self):
        script = """
        // 1. Create base project comp with 250 layers
        var srcComp = app.project.items.addComp("Main 4K Comp", 3840, 2160);
        for (var i = 1; i <= 250; i++) {
            var layer = new Layer(i, "Layer_" + i, "uid_" + i);
            srcComp.layers.push(layer);
        }
        srcComp.numLayers = srcComp.layers.length;
        app.project.activeItem = srcComp;

        // 2. Generate 50 synthetic chunk files
        var chunkPaths = [];
        var totalChunks = 50;
        for (var c = 0; c < totalChunks; c++) {
            var chunkFile = "/synthetic/chunk_" + c + ".json";
            var chunkLayers = [];
            for (var l = 0; l < 5; l++) {
                var layerIdx = (c * 5) + l + 1;
                chunkLayers.push({
                    name: "Layer_" + layerIdx,
                    uid: "uid_" + layerIdx,
                    index: layerIdx,
                    layer_index: layerIdx,
                    layer_kind: "av",
                    is_root: true,
                    conformed_transforms: {
                        position: [1920 + layerIdx, 1080, 0],
                        scale: [100, 100, 100],
                        rotation: 0,
                        opacity: 100
                    }
                });
            }
            fsMemory[chunkFile] = JSON.stringify({
                chunk_index: c,
                total_chunks: totalChunks,
                layers: chunkLayers
            });
            chunkPaths.push(chunkFile);
        }

        // 3. Write chunk manifest
        var manifestPath = "/synthetic/chunk_manifest.json";
        var logPath = "/synthetic/transfer_status.log";
        fsMemory[manifestPath] = JSON.stringify({
            preset_label: "DEV_TEST",
            expected_comp_name: "Main 4K Comp",
            target_width: 1080,
            target_height: 1920,
            total_layers: 250,
            total_chunks: totalChunks,
            chunk_paths: chunkPaths,
            mirror_tree: {},
            mirror_rewires: []
        });

        // 4. Trigger injection via async entry point
        Babysitter.executeSovereignInjection(
            manifestPath,
            logPath
        );

        // 5. Drain the pump task loop
        var maxTicks = 200;
        var ticksExecuted = 0;
        var pumpPhases = [];
        while (scheduledTasks.length > 0 && ticksExecuted < maxTicks) {
            var task = scheduledTasks.shift();
            ticksExecuted++;
            pumpPhases.push(Babysitter._state.phase);
            eval(task.code);
        }

        // 6. Read transfer status log lines
        var logLines = (fsMemory[logPath] || "").trim().split("\\n").filter(Boolean).map(function(l) {
            try { return JSON.parse(l); } catch(_) { return null; }
        }).filter(Boolean);

        var terminalBeacons = logLines.filter(function(l) { return l.status === "COMPLETE" || l.status === "FAILED" || l.status === "ABORTED"; });
        var finalBeacon = terminalBeacons[terminalBeacons.length - 1] || {};
        var progressBeacons = logLines.filter(function(l) { return typeof l.chunkIndex === "number"; });

        console.log(JSON.stringify({
            status: "OK",
            ticksExecuted: ticksExecuted,
            finalPhase: Babysitter._state.phase,
            busyAfterTeardown: Babysitter._state.busy,
            finalBeaconStatus: finalBeacon.status,
            totalLogLines: logLines.length,
            progressBeaconCount: progressBeacons.length
        }));
        """
        res = _run_node_harness(script)
        assert res["status"] == "OK"
        assert res["ticksExecuted"] >= 52  # setup + rewire + 50 chunks + audit
        assert res["finalPhase"] == "idle"
        assert res["busyAfterTeardown"] is False
        assert res["finalBeaconStatus"] == "COMPLETE"
        assert res["progressBeaconCount"] >= 50


class TestSyntheticMidInjectAbortUnderStress:
    """Stress Vector 2: Mid-inject cooperative abort at chunk 25/50."""

    def test_abort_stops_immediately_at_chunk_25(self):
        script = """
        var srcComp = app.project.items.addComp("Stress Comp", 1920, 1080);
        for (var i = 1; i <= 250; i++) {
            srcComp.layers.push(new Layer(i, "Layer_" + i, "uid_" + i));
        }
        srcComp.numLayers = 250;
        app.project.activeItem = srcComp;

        var chunkPaths = [];
        var totalChunks = 50;
        for (var c = 0; c < totalChunks; c++) {
            var chunkFile = "/synthetic/chunk_" + c + ".json";
            fsMemory[chunkFile] = JSON.stringify({
                chunk_index: c,
                total_chunks: totalChunks,
                layers: [{
                    name: "Layer_1",
                    uid: "uid_1",
                    index: 1,
                    layer_index: 1,
                    layer_kind: "av",
                    is_root: true,
                    conformed_transforms: { position: [100, 100, 0] }
                }]
            });
            chunkPaths.push(chunkFile);
        }

        var manifestPath = "/synthetic/chunk_manifest.json";
        var logPath = "/synthetic/transfer_status.log";
        var abortFlagPath = "/synthetic/inject_abort_request.json";

        fsMemory[manifestPath] = JSON.stringify({
            preset_label: "DEV_TEST",
            expected_comp_name: "Stress Comp",
            target_width: 1080,
            target_height: 1920,
            total_layers: 250,
            total_chunks: totalChunks,
            chunk_paths: chunkPaths,
            mirror_tree: {},
            mirror_rewires: []
        });

        Babysitter.executeSovereignInjection(
            manifestPath,
            logPath
        );

        var ticksExecuted = 0;
        var abortedAtChunk = null;
        var maxTicks = 100;

        while (scheduledTasks.length > 0 && ticksExecuted < maxTicks) {
            var task = scheduledTasks.shift();
            ticksExecuted++;

            // Trigger abort flag right before chunk 25 executes
            if (Babysitter._state.phase === "chunk" && Babysitter._state.chunkIndex === 25) {
                fsMemory[abortFlagPath] = JSON.stringify({ requested_at: new Date().toISOString() });
                abortedAtChunk = 25;
            }

            eval(task.code);
        }

        var logLines = (fsMemory[logPath] || "").trim().split("\\n").filter(Boolean).map(function(l) {
            try { return JSON.parse(l); } catch(_) { return null; }
        }).filter(Boolean);

        var terminalBeacons = logLines.filter(function(l) { return l.status === "COMPLETE" || l.status === "FAILED" || l.status === "ABORTED"; });
        var finalBeacon = terminalBeacons[terminalBeacons.length - 1] || {};

        console.log(JSON.stringify({
            status: "OK",
            ticksExecuted: ticksExecuted,
            abortedAtChunk: abortedAtChunk,
            finalBeaconStatus: finalBeacon.status,
            finalBeaconError: finalBeacon.error,
            finalBeaconChunkIndex: finalBeacon.chunkIndex,
            busyState: Babysitter._state.busy,
            tasksRemaining: scheduledTasks.length,
            abortFlagCleared: !fsMemory.hasOwnProperty(abortFlagPath)
        }));
        """
        res = _run_node_harness(script)
        assert res["status"] == "OK"
        assert res["finalBeaconStatus"] == "ABORTED"
        assert res["finalBeaconError"] == "User cancelled"
        assert res["finalBeaconChunkIndex"] == 25
        assert res["busyState"] is False
        assert res["tasksRemaining"] == 0
        assert res["abortFlagCleared"] is True


class TestSyntheticExtremeValueFirewall:
    """Stress Vector 3: Corrupted / Malformed Keyframe & Boundary Firewall."""

    def test_nan_and_infinity_rejections(self):
        script = """
        var results = {
            nan_scalar: Babysitter._isFiniteVal(NaN),
            inf_scalar: Babysitter._isFiniteVal(Infinity),
            neg_inf_scalar: Babysitter._isFiniteVal(-Infinity),
            nan_vector: Babysitter._isFiniteVal([100, NaN, 300]),
            inf_vector: Babysitter._isFiniteVal([Infinity, 200]),
            clean_vector: Babysitter._isFiniteVal([100, 200, 300]),
            null_val: Babysitter._isFiniteVal(null),
            undefined_val: Babysitter._isFiniteVal(undefined)
        };
        console.log(JSON.stringify({ status: "OK", results: results }));
        """
        res = _run_node_harness(script)["results"]
        assert res["nan_scalar"] is False
        assert res["inf_scalar"] is False
        assert res["neg_inf_scalar"] is False
        assert res["nan_vector"] is False
        assert res["inf_vector"] is False
        assert res["clean_vector"] is True
        assert res["null_val"] is False
        assert res["undefined_val"] is False

    def test_sanitize_value_clamping_and_bounds(self):
        script = """
        var results = {
            scale_min_clamp: Babysitter._sanitizeValue([0.000001, 0.000001], "scale"),
            scale_max_clamp: Babysitter._sanitizeValue([999999, 999999], "scale"),
            opacity_neg_clamp: Babysitter._sanitizeValue(-50, "opacity"),
            opacity_over_clamp: Babysitter._sanitizeValue(250, "opacity"),
            nan_rejected: Babysitter._sanitizeValue([NaN, 100], "position", 1920, 1080),
            pos_x_clamp: Babysitter._sanitizeValue([1000000, 500], "position", 1920, 1080),
            pos_neg_clamp: Babysitter._sanitizeValue([-1000000, -500000], "position", 1920, 1080)
        };
        console.log(JSON.stringify({ status: "OK", results: results }));
        """
        res = _run_node_harness(script)["results"]
        assert res["scale_min_clamp"] == [0.01, 0.01]
        assert res["scale_max_clamp"] == [10000.0, 10000.0]
        assert res["opacity_neg_clamp"] == 0
        assert res["opacity_over_clamp"] == 100
        assert res["nan_rejected"] is None
        # 1920 * 10 = 19200 max X bound
        assert res["pos_x_clamp"] == [19200, 500]
        assert res["pos_neg_clamp"] == [-19200, -10800]


class TestSyntheticDeepRewireTopology:
    """Stress Vector 4: 15-node DAG rewire target resolution."""

    def test_deep_dag_rewire_resolution(self):
        script = """
        var mirrorComps = {};
        for (var i = 1; i <= 15; i++) {
            mirrorComps["Precomp_" + i] = { id: i, name: "Mirror_Precomp_" + i };
        }
        mirrorComps["Precomp_3#fork_uid_99"] = { id: 301, name: "Fork_Precomp_3_Consumer_99" };

        var rewires = [
            { target_mirror_source_name: "Precomp_1" },
            { target_mirror_source_name: "Precomp_15" },
            { target_mirror_source_name: "Precomp_3", fork_consumer_layer_uid: "fork_uid_99" },
            { target_mirror_source_name: "Precomp_3", fork_consumer_layer_uid: "nonexistent_fork" },
            { target_mirror_source_name: "Unknown_Comp" },
            { target_mirror_source_name: "" },
            null
        ];

        var resolved = rewires.map(function(rw) {
            var target = Babysitter._resolveMirrorRewireTarget(rw, mirrorComps);
            return target ? target.name : null;
        });

        console.log(JSON.stringify({ status: "OK", resolved: resolved }));
        """
        res = _run_node_harness(script)["resolved"]
        assert res[0] == "Mirror_Precomp_1"
        assert res[1] == "Mirror_Precomp_15"
        assert res[2] == "Fork_Precomp_3_Consumer_99"  # Exact fork matched
        assert res[3] == "Mirror_Precomp_3"             # Fork fallback to shared mirror
        assert res[4] is None                           # Unknown comp -> null
        assert res[5] is None                           # Empty string -> null
        assert res[6] is None                           # Null rewire -> null


class TestSyntheticFaultRecoveryAndTeardown:
    """Stress Vector 5: Fault injection, state reset, and undo group balancing."""

    def test_corrupt_chunk_json_fails_gracefully_and_resets(self):
        script = """
        var srcComp = app.project.items.addComp("Fault Comp", 1920, 1080);
        srcComp.layers.push(new Layer(1, "L1", "u1"));
        srcComp.numLayers = 1;
        app.project.activeItem = srcComp;

        // Corrupt chunk content (invalid JSON syntax)
        fsMemory["/synthetic/corrupt_chunk.json"] = "{ invalid json content !!!";

        var manifestPath = "/synthetic/chunk_manifest.json";
        var logPath = "/synthetic/transfer_status.log";

        fsMemory[manifestPath] = JSON.stringify({
            preset_label: "DEV_TEST",
            expected_comp_name: "Fault Comp",
            target_width: 1080,
            target_height: 1920,
            total_chunks: 1,
            chunk_paths: ["/synthetic/corrupt_chunk.json"],
            mirror_tree: {},
            mirror_rewires: []
        });

        Babysitter.executeSovereignInjectionMemory(
            JSON.parse(fsMemory[manifestPath]),
            logPath
        );

        var maxTicks = 10;
        var ticks = 0;
        while (scheduledTasks.length > 0 && ticks < maxTicks) {
            var task = scheduledTasks.shift();
            ticks++;
            eval(task.code);
        }

        var logLines = (fsMemory[logPath] || "").trim().split("\\n").filter(Boolean).map(function(l) {
            try { return JSON.parse(l); } catch(_) { return null; }
        }).filter(Boolean);

        var finalBeacon = logLines[logLines.length - 1] || {};

        console.log(JSON.stringify({
            status: "OK",
            finalBeaconStatus: finalBeacon.status,
            busyState: Babysitter._state.busy,
            tasksRemaining: scheduledTasks.length
        }));
        """
        res = _run_node_harness(script)
        assert res["status"] == "OK"
        assert res["finalBeaconStatus"] == "FAILED"
        assert res["busyState"] is False
        assert res["tasksRemaining"] == 0


class TestSyntheticCameraAndKeyframedStreams:
    """Stress Vector 6: Camera layer dispatch and keyframed property streams."""

    def test_camera_intrinsics_and_keyframes_dispatch(self):
        script = """
        var srcComp = app.project.items.addComp("Camera Comp", 1920, 1080);
        var camLayer = new Layer(1, "Main Camera", "uid_cam_1");
        srcComp.layers.push(camLayer);
        srcComp.numLayers = 1;
        app.project.activeItem = srcComp;

        var chunkFile = "/synthetic/camera_chunk.json";
        fsMemory[chunkFile] = JSON.stringify({
            chunk_index: 0,
            total_chunks: 1,
            layers: [{
                name: "Main Camera",
                uid: "uid_cam_1",
                index: 1,
                layer_index: 1,
                layer_kind: "camera",
                is_root: true,
                conformed_transforms: {
                    position: [960, 540, -1500],
                    rotation: 0,
                    opacity: 100,
                    camera: {
                        zoom: 1200,
                        focusDistance: 1500,
                        aperture: 50,
                        blurLevel: 10,
                        pointOfInterest: [960, 540, 0]
                    }
                },
                conformed_keys: {
                    position: {
                        times: [0, 1.0, 2.0],
                        values: [[960, 540, -1500], [960, 540, -1200], [960, 540, -1000]],
                        keyInInterpolationType: [6613, 6613, 6613],
                        keyOutInterpolationType: [6613, 6613, 6613]
                    },
                    camera_zoom: {
                        times: [0, 2.0],
                        values: [1200, 1800]
                    }
                }
            }]
        });

        var manifestPath = "/synthetic/chunk_manifest.json";
        var logPath = "/synthetic/transfer_status.log";
        fsMemory[manifestPath] = JSON.stringify({
            preset_label: "DEV_TEST",
            expected_comp_name: "Camera Comp",
            target_width: 1080,
            target_height: 1920,
            total_layers: 1,
            total_chunks: 1,
            chunk_paths: [chunkFile],
            mirror_tree: {},
            mirror_rewires: []
        });

        Babysitter.executeSovereignInjection(
            manifestPath,
            logPath
        );

        var maxTicks = 15;
        var ticks = 0;
        while (scheduledTasks.length > 0 && ticks < maxTicks) {
            var task = scheduledTasks.shift();
            ticks++;
            eval(task.code);
        }

        var logLines = (fsMemory[logPath] || "").trim().split("\\n").filter(Boolean).map(function(l) {
            try { return JSON.parse(l); } catch(_) { return null; }
        }).filter(Boolean);

        var terminalBeacons = logLines.filter(function(l) { return l.status === "COMPLETE" || l.status === "FAILED" || l.status === "ABORTED"; });
        var finalBeacon = terminalBeacons[terminalBeacons.length - 1] || {};

        // Inspect output comp created by setupWorkspace
        var outputComp = app.project.items.addComp("Output Test", 1080, 1920); // fallback check
        var targetCam = null;
        for (var i = 1; i <= app.project.numItems; i++) {
            var it = app.project.item(i);
            if (it && it.name && it.name.indexOf("[DIMENSION]") !== -1) {
                targetCam = it.layer(1);
                break;
            }
        }

        console.log(JSON.stringify({
            status: "OK",
            finalBeaconStatus: finalBeacon.status,
            camFound: !!targetCam,
            camZoomKeys: targetCam ? targetCam.cameraOption.zoom.numKeys : 0,
            camPosKeys: targetCam ? targetCam.transform.position.numKeys : 0,
            busyState: Babysitter._state.busy
        }));
        """
        res = _run_node_harness(script)
        assert res["status"] == "OK"
        assert res["finalBeaconStatus"] == "COMPLETE"
        assert res["camFound"] is True
        assert res["camZoomKeys"] == 2
        assert res["camPosKeys"] == 3
        assert res["busyState"] is False


class TestSyntheticMissingStructureRobustness:
    """Stress Vector 7: Missing structure / locked layers resilience."""

    def test_locked_layers_and_missing_properties_handled_safely(self):
        script = """
        var srcComp = app.project.items.addComp("Locked Comp", 1920, 1080);
        var l1 = new Layer(1, "Locked Layer", "uid_locked");
        l1.locked = true;
        srcComp.layers.push(l1);
        srcComp.numLayers = 1;
        app.project.activeItem = srcComp;

        var chunkFile = "/synthetic/locked_chunk.json";
        fsMemory[chunkFile] = JSON.stringify({
            chunk_index: 0,
            total_chunks: 1,
            layers: [{
                name: "Locked Layer",
                uid: "uid_locked",
                index: 1,
                layer_index: 1,
                layer_kind: "av",
                is_root: true,
                conformed_transforms: {
                    position: [500, 500, 0],
                    scale: [80, 80, 100],
                    rotation: 45,
                    opacity: 85
                }
            }]
        });

        var manifestPath = "/synthetic/chunk_manifest.json";
        var logPath = "/synthetic/transfer_status.log";
        fsMemory[manifestPath] = JSON.stringify({
            preset_label: "DEV_TEST",
            expected_comp_name: "Locked Comp",
            target_width: 1080,
            target_height: 1920,
            total_layers: 1,
            total_chunks: 1,
            chunk_paths: [chunkFile],
            mirror_tree: {},
            mirror_rewires: []
        });

        Babysitter.executeSovereignInjection(
            manifestPath,
            logPath
        );

        var maxTicks = 15;
        var ticks = 0;
        while (scheduledTasks.length > 0 && ticks < maxTicks) {
            var task = scheduledTasks.shift();
            ticks++;
            eval(task.code);
        }

        var logLines = (fsMemory[logPath] || "").trim().split("\\n").filter(Boolean).map(function(l) {
            try { return JSON.parse(l); } catch(_) { return null; }
        }).filter(Boolean);

        var terminalBeacons = logLines.filter(function(l) { return l.status === "COMPLETE" || l.status === "FAILED" || l.status === "ABORTED"; });
        var finalBeacon = terminalBeacons[terminalBeacons.length - 1] || {};

        console.log(JSON.stringify({
            status: "OK",
            finalBeaconStatus: finalBeacon.status,
            busyState: Babysitter._state.busy
        }));
        """
        res = _run_node_harness(script)
        assert res["status"] == "OK"
        assert res["finalBeaconStatus"] == "COMPLETE"
        assert res["busyState"] is False

