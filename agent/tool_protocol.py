"""Typed protocol for model-generated PC Agent tool calls."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator


class ToolProtocolError(ValueError):
    """Raised when an LLM response cannot be treated as a safe tool plan."""


class StrictParams(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EmptyParams(StrictParams):
    pass


class OrganizeByExtensionParams(StrictParams):
    directory: str = Field(min_length=1, max_length=500)

    @field_validator("directory")
    @classmethod
    def directory_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("directory 不能为空")
        return value


class QueryKnowledgeBaseParams(StrictParams):
    question: str = Field(min_length=1, max_length=2_000)


class OpenAppParams(StrictParams):
    app_name: str = Field(min_length=1, max_length=100)


class GetTopProcessesParams(StrictParams):
    limit: int = Field(default=5, ge=1, le=10)


class UndoFileTransactionParams(StrictParams):
    transaction_id: str = Field(min_length=8, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")


class RawToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool: str = Field(min_length=1, max_length=100)
    params: dict[str, Any] = Field(default_factory=dict)

    @field_validator("tool")
    @classmethod
    def normalize_tool_name(cls, value: str) -> str:
        return value.strip()


def _strip_code_fence(response: str) -> str:
    text = response.strip()
    if text.startswith("```") and text.endswith("```"):
        lines = text.splitlines()
        if len(lines) >= 3:
            return "\n".join(lines[1:-1]).strip()
    return text


def parse_tool_calls(response: str) -> list[RawToolCall]:
    """Parse one model response without accepting arbitrary free-form output."""
    try:
        payload = json.loads(_strip_code_fence(response))
    except json.JSONDecodeError as exc:
        raise ToolProtocolError(f"模型输出不是合法 JSON: {exc.msg}") from exc

    if isinstance(payload, dict):
        payload = [payload]
    if not isinstance(payload, list) or not payload:
        raise ToolProtocolError("模型输出必须是非空工具调用数组")
    if len(payload) > 5:
        raise ToolProtocolError("单次计划最多允许 5 个工具调用")

    try:
        return [RawToolCall.model_validate(item) for item in payload]
    except ValidationError as exc:
        raise ToolProtocolError(f"工具调用结构校验失败: {exc.errors()[0]['msg']}") from exc
