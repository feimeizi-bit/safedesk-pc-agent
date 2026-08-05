import pytest

from agent.tool_protocol import ToolProtocolError, parse_tool_calls
from agent.tool_registry import TOOL_REGISTRY


def test_parser_accepts_json_code_fence():
    calls = parse_tool_calls(
        '```json\n[{"tool":"get_cpu_usage","params":{}}]\n```'
    )
    assert calls[0].tool == "get_cpu_usage"


def test_parser_rejects_more_than_five_calls():
    response = "[" + ",".join(
        '{"tool":"get_cpu_usage","params":{}}' for _ in range(6)
    ) + "]"
    with pytest.raises(ToolProtocolError, match="最多允许 5"):
        parse_tool_calls(response)


def test_parameter_models_reject_extra_fields():
    definition = TOOL_REGISTRY["get_cpu_usage"]
    with pytest.raises(ToolProtocolError, match="参数错误"):
        definition.validate_params({"unexpected": True})
