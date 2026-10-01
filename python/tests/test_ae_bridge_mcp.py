# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/tests/test_ae_bridge_mcp.py
Comprehensive Unit Test Suite for ae_eval CLI and ae-bridge-mcp server tools.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from unittest.mock import patch
import pytest

from tools.ae_eval import (
    evaluate,
    get_active_comp_info,
    get_selected_layers,
    eval_extendscript_socket,
    eval_extendscript_file_bridge,
    main as ae_eval_main,
)
from tools.ae_bridge_mcp import (
    handle_call_tool,
    get_tools_list,
    run_stdio_server,
)


def test_get_tools_list():
    tools = get_tools_list()
    tool_names = [t["name"] for t in tools]
    assert "ae_eval_script" in tool_names
    assert "ae_get_active_comp" in tool_names
    assert "ae_get_selected_layers" in tool_names
    assert "ae_get_layer_properties" in tool_names
    assert "ae_scrape_active_comp" in tool_names


@patch("tools.ae_eval.execute_job")
def test_eval_extendscript_socket_success(mock_execute):
    mock_execute.return_value = {
        "status": "OK",
        "result": "MyComp",
        "result_str": "MyComp",
    }
    res = eval_extendscript_socket("app.project.activeItem.name")
    assert res["status"] == "OK"
    assert res["result"] == "MyComp"
    mock_execute.assert_called_once()


@patch("tools.ae_eval.eval_extendscript_socket")
def test_evaluate_socket_transport(mock_socket):
    mock_socket.return_value = {
        "status": "OK",
        "result": 42,
    }
    res = evaluate("21 * 2")
    assert res["status"] == "OK"
    assert res["result"] == 42
    assert res["transport"] == "socket_ipc"


def test_eval_extendscript_file_bridge_fails_fast_not_polling():
    """The real (unmocked) function must raise immediately -- no job type
    for "eval" exists on the JSX inbox dispatcher, so this must never go
    back to silently polling until timeout for something that can't
    succeed."""
    with pytest.raises(NotImplementedError, match="eval"):
        eval_extendscript_file_bridge("app.version")


@patch("tools.ae_eval.eval_extendscript_socket", side_effect=ConnectionRefusedError("Refused"))
@patch("tools.ae_eval.eval_extendscript_file_bridge")
def test_evaluate_file_bridge_fallback(mock_file_bridge, mock_socket):
    mock_file_bridge.return_value = {
        "status": "OK",
        "result": "fallback_val",
    }
    res = evaluate("app.version")
    assert res["status"] == "OK"
    assert res["result"] == "fallback_val"
    assert res["transport"] == "file_bridge"


@patch("tools.ae_eval.eval_extendscript_socket", side_effect=ConnectionRefusedError("Refused"))
@patch("tools.ae_eval.eval_extendscript_file_bridge", side_effect=NotImplementedError("File-bridge eval not implemented"))
@patch("tools.ae_eval.eval_extendscript_applescript")
def test_evaluate_applescript_fallback(mock_apple, mock_file_bridge, mock_socket):
    mock_apple.return_value = {
        "status": "OK",
        "result": "26.3.0",
    }
    with patch("sys.platform", "darwin"):
        res = evaluate("app.version")
        assert res["status"] == "OK"
        assert res["result"] == "26.3.0"
        assert res["transport"] == "applescript"


@patch("tools.ae_eval.eval_extendscript_socket", side_effect=ConnectionRefusedError("Refused"))
@patch("tools.ae_eval.eval_extendscript_file_bridge", side_effect=NotImplementedError("File-bridge eval not implemented"))
@patch("tools.ae_eval.eval_extendscript_applescript", side_effect=RuntimeError("AppleScript error"))
def test_evaluate_all_transports_failed(mock_apple, mock_file_bridge, mock_socket):
    with patch("sys.platform", "darwin"):
        res = evaluate("app.version")
        assert res["status"] == "ERROR"
        assert "All After Effects bridges failed to respond" in res["error"]


@patch("tools.ae_eval.evaluate")
def test_get_active_comp_info(mock_eval):
    mock_eval.return_value = {
        "status": "OK",
        "name": "Master 16x9",
        "width": 1920,
        "height": 1080,
        "fps": 23.976,
    }
    res = get_active_comp_info()
    assert res["status"] == "OK"
    assert res["name"] == "Master 16x9"


@patch("tools.ae_eval.evaluate")
def test_get_selected_layers(mock_eval):
    mock_eval.return_value = {
        "status": "OK",
        "count": 1,
        "layers": [{"index": 1, "name": "Hero Title"}],
    }
    res = get_selected_layers()
    assert res["status"] == "OK"
    assert res["count"] == 1


@patch("tools.ae_bridge_mcp.evaluate")
def test_mcp_handle_call_tool_eval_script(mock_eval):
    mock_eval.return_value = {"status": "OK", "result": "Title Layer"}
    call_res = handle_call_tool("ae_eval_script", {"script": "app.project.activeItem.layer(1).name"})
    assert call_res["isError"] is False
    content_text = call_res["content"][0]["text"]
    assert "Title Layer" in content_text


@patch("tools.ae_bridge_mcp.get_active_comp_info")
def test_mcp_handle_call_tool_get_active_comp(mock_info):
    mock_info.return_value = {"status": "OK", "name": "Active Comp", "width": 1080, "height": 1920}
    call_res = handle_call_tool("ae_get_active_comp", {})
    assert call_res["isError"] is False
    assert "Active Comp" in call_res["content"][0]["text"]


@patch("tools.ae_bridge_mcp.get_selected_layers")
def test_mcp_handle_call_tool_get_selected_layers(mock_layers):
    mock_layers.return_value = {"status": "OK", "count": 2, "layers": [{"name": "L1"}, {"name": "L2"}]}
    call_res = handle_call_tool("ae_get_selected_layers", {})
    assert call_res["isError"] is False
    assert "L1" in call_res["content"][0]["text"]


@patch("tools.ae_bridge_mcp.evaluate")
def test_mcp_handle_call_tool_get_layer_properties(mock_eval):
    mock_eval.return_value = {
        "status": "OK",
        "layer": {
            "index": 1,
            "name": "Live Type",
            "position": [960, 540, 0],
            "effects": [{"name": "Fast Blur"}]
        }
    }
    call_res = handle_call_tool("ae_get_layer_properties", {"layer_index": 1})
    assert call_res["isError"] is False
    assert "Live Type" in call_res["content"][0]["text"]


@patch("tools.ae_bridge_mcp.evaluate")
def test_mcp_handle_call_tool_scrape_active_comp(mock_eval):
    mock_eval.return_value = {
        "status": "OK",
        "project_info": {"width": 1920, "height": 1080},
        "layers": [{"index": 1, "name": "Background"}]
    }
    call_res = handle_call_tool("ae_scrape_active_comp", {})
    assert call_res["isError"] is False
    assert "Background" in call_res["content"][0]["text"]


def test_mcp_handle_unknown_tool():
    unknown_res = handle_call_tool("non_existent_tool", {})
    assert unknown_res["isError"] is True


def test_mcp_stdio_server_initialize_and_ping():
    input_stream = io.StringIO(
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}) + "\n" +
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}) + "\n" +
        json.dumps({"jsonrpc": "2.0", "id": 3, "method": "ping", "params": {}}) + "\n"
    )
    output_stream = io.StringIO()

    with patch("sys.stdin", input_stream), patch("sys.stdout", output_stream):
        run_stdio_server()

    responses = [json.loads(line) for line in output_stream.getvalue().strip().split("\n") if line.strip()]
    assert len(responses) == 3
    assert responses[0]["id"] == 1
    assert responses[0]["result"]["serverInfo"]["name"] == "ae-bridge"
    assert responses[1]["id"] == 2
    assert len(responses[1]["result"]["tools"]) == 5
    assert responses[2]["id"] == 3


@patch("tools.ae_eval.evaluate")
def test_ae_eval_cli_main(mock_eval, tmp_path: Path):
    mock_eval.return_value = {"status": "OK", "result": "Title Comp"}

    # 1. Test direct script evaluation
    with patch("sys.argv", ["ae_eval.py", "app.project.activeItem.name", "--json"]):
        with pytest.raises(SystemExit) as e:
            ae_eval_main()
        assert e.value.code == 0

    # 2. Test file evaluation
    jsx_file = tmp_path / "test.jsx"
    jsx_file.write_text("var x = 10;", encoding="utf-8")
    with patch("sys.argv", ["ae_eval.py", "-f", str(jsx_file)]):
        with pytest.raises(SystemExit) as e:
            ae_eval_main()
        assert e.value.code == 0

    # 3. Test active comp flag
    with patch("sys.argv", ["ae_eval.py", "--active-comp"]):
        with pytest.raises(SystemExit) as e:
            ae_eval_main()
        assert e.value.code == 0
