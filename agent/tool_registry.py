"""Declarative registry for tools exposed to the local model."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable

from pydantic import BaseModel, ValidationError

from agent.tool_protocol import (
    EmptyParams,
    GetTopProcessesParams,
    OpenAppParams,
    OrganizeByExtensionParams,
    QueryKnowledgeBaseParams,
    ToolProtocolError,
    UndoFileTransactionParams,
)
from tools.app_control import get_top_processes, open_app
from tools.file_ops import (
    organize_by_extension,
    prepare_organization_by_extension,
    prepare_undo_file_transaction,
    preview_organization_by_extension,
    undo_file_transaction,
)
from tools.rag_query import query_knowledge_base
from tools.sys_info import get_cpu_usage, get_memory_usage, get_system_summary


class RiskLevel(str, Enum):
    READ_ONLY = "read_only"
    LOW = "low"
    HIGH = "high"


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    params_model: type[BaseModel]
    handler: Callable[..., str]
    risk_level: RiskLevel
    preview_handler: Callable[..., str] | None = None
    prepare_handler: Callable[..., tuple[str, dict]] | None = None

    @property
    def requires_confirmation(self) -> bool:
        return self.risk_level is RiskLevel.HIGH

    def validate_params(self, params: dict) -> dict:
        try:
            validated = self.params_model.model_validate(params)
        except ValidationError as exc:
            detail = exc.errors()[0]
            location = ".".join(str(part) for part in detail["loc"])
            raise ToolProtocolError(
                f"工具 {self.name} 参数错误 ({location or 'params'}): {detail['msg']}"
            ) from exc
        return validated.model_dump()


TOOL_REGISTRY: dict[str, ToolDefinition] = {
    "organize_by_extension": ToolDefinition(
        name="organize_by_extension",
        params_model=OrganizeByExtensionParams,
        handler=organize_by_extension,
        preview_handler=preview_organization_by_extension,
        prepare_handler=prepare_organization_by_extension,
        risk_level=RiskLevel.HIGH,
    ),
    "get_cpu_usage": ToolDefinition(
        name="get_cpu_usage",
        params_model=EmptyParams,
        handler=get_cpu_usage,
        risk_level=RiskLevel.READ_ONLY,
    ),
    "get_memory_usage": ToolDefinition(
        name="get_memory_usage",
        params_model=EmptyParams,
        handler=get_memory_usage,
        risk_level=RiskLevel.READ_ONLY,
    ),
    "get_system_summary": ToolDefinition(
        name="get_system_summary",
        params_model=EmptyParams,
        handler=get_system_summary,
        risk_level=RiskLevel.READ_ONLY,
    ),
    "get_top_processes": ToolDefinition(
        name="get_top_processes",
        params_model=GetTopProcessesParams,
        handler=get_top_processes,
        risk_level=RiskLevel.READ_ONLY,
    ),
    "open_app": ToolDefinition(
        name="open_app",
        params_model=OpenAppParams,
        handler=open_app,
        risk_level=RiskLevel.LOW,
    ),
    "query_knowledge_base": ToolDefinition(
        name="query_knowledge_base",
        params_model=QueryKnowledgeBaseParams,
        handler=query_knowledge_base,
        risk_level=RiskLevel.READ_ONLY,
    ),
    "undo_file_transaction": ToolDefinition(
        name="undo_file_transaction",
        params_model=UndoFileTransactionParams,
        handler=undo_file_transaction,
        preview_handler=lambda transaction_id: prepare_undo_file_transaction(transaction_id)[0],
        prepare_handler=prepare_undo_file_transaction,
        risk_level=RiskLevel.HIGH,
    ),
}
