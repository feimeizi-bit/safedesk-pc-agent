import hashlib
import json
import sqlite3

import sandbox
from agent.simple_agent import AgentStatus, run_agent, simple_agent
from agent.trace_store import get_recent_traces


def fake_llm(payload):
    encoded = json.dumps(payload, ensure_ascii=False)
    return lambda _messages: encoded


def configure_safe_test_runtime(monkeypatch, tmp_path):
    monkeypatch.setattr(sandbox, "USER_WHITELIST", [tmp_path])
    monkeypatch.setattr(sandbox, "AUDIT_DB_PATH", tmp_path / "audit.db")


def test_none_is_a_normal_agent_response(tmp_path, monkeypatch):
    configure_safe_test_runtime(monkeypatch, tmp_path)
    result = run_agent(
        "你好",
        fake_llm([{"tool": "none", "params": {}}]),
    )
    assert result.status is AgentStatus.COMPLETED
    assert "本地 PC 助手" in result.response


def test_unknown_tool_is_refused(tmp_path, monkeypatch):
    configure_safe_test_runtime(monkeypatch, tmp_path)
    result = run_agent(
        "执行未知操作",
        fake_llm([{"tool": "run_shell", "params": {"command": "whoami"}}]),
    )
    assert result.status is AgentStatus.REFUSED
    assert "未知工具" in result.response


def test_bad_parameters_do_not_raise_type_error(tmp_path, monkeypatch):
    configure_safe_test_runtime(monkeypatch, tmp_path)
    result = run_agent(
        "CPU",
        fake_llm([{"tool": "get_cpu_usage", "params": {"x": 1}}]),
    )
    assert result.status is AgentStatus.REFUSED
    assert "参数错误" in result.response


def test_high_risk_plan_is_bound_to_one_time_token(tmp_path, monkeypatch):
    configure_safe_test_runtime(monkeypatch, tmp_path)
    target = tmp_path / "documents"
    target.mkdir()
    (target / "a.txt").write_text("a", encoding="utf-8")
    (target / "b.pdf").write_text("b", encoding="utf-8")
    planner = fake_llm(
        [{"tool": "organize_by_extension", "params": {"directory": str(target)}}]
    )

    preview = run_agent("整理", planner)
    assert preview.status is AgentStatus.CONFIRMATION_REQUIRED
    assert preview.confirmation_token
    assert preview.plan_hash and len(preview.plan_hash) == 64
    assert (target / "a.txt").exists()

    def model_must_not_be_called(_messages):
        raise AssertionError("确认阶段不应重新调用模型")

    executed = run_agent(
        "",
        model_must_not_be_called,
        confirmation_token=preview.confirmation_token,
    )
    assert executed.status is AgentStatus.COMPLETED
    assert "事务" in executed.response
    assert (target / "txt" / "a.txt").exists()
    assert (target / "pdf" / "b.pdf").exists()

    replay = run_agent("", confirmation_token=preview.confirmation_token)
    assert replay.status is AgentStatus.REFUSED
    assert "已使用" in replay.response

    rows = sandbox.get_audit_log()
    assert rows[0][4] == 1
    assert rows[1][4] == 0


def test_tampered_persisted_plan_fails_integrity_check(tmp_path, monkeypatch):
    configure_safe_test_runtime(monkeypatch, tmp_path)
    target = tmp_path / "documents"
    target.mkdir()
    (target / "a.txt").write_text("a", encoding="utf-8")
    preview = run_agent(
        "整理",
        fake_llm(
            [{"tool": "organize_by_extension", "params": {"directory": str(target)}}]
        ),
    )
    token_hash = hashlib.sha256(preview.confirmation_token.encode("utf-8")).hexdigest()
    with sqlite3.connect(sandbox.AUDIT_DB_PATH) as connection:
        connection.execute(
            "UPDATE pending_plans SET plan_json = ? WHERE token_hash = ?",
            ('[{"tool":"open_app","params":{"app_name":"记事本"}}]', token_hash),
        )

    result = run_agent("", confirmation_token=preview.confirmation_token)
    assert result.status is AgentStatus.REFUSED
    assert "完整性校验失败" in result.response
    assert (target / "a.txt").exists()


def test_directory_change_after_preview_requires_new_plan(tmp_path, monkeypatch):
    configure_safe_test_runtime(monkeypatch, tmp_path)
    target = tmp_path / "documents"
    target.mkdir()
    (target / "a.txt").write_text("a", encoding="utf-8")
    preview = run_agent(
        "整理",
        fake_llm(
            [{"tool": "organize_by_extension", "params": {"directory": str(target)}}]
        ),
    )
    (target / "new.pdf").write_text("new", encoding="utf-8")

    result = run_agent("", confirmation_token=preview.confirmation_token)
    assert result.status is AgentStatus.FAILED
    assert "目录内容在预览后发生变化" in result.response
    assert (target / "a.txt").exists()
    assert (target / "new.pdf").exists()


def test_legacy_boolean_confirmation_cannot_execute(tmp_path, monkeypatch):
    configure_safe_test_runtime(monkeypatch, tmp_path)
    result = simple_agent("整理", fake_llm([]), confirmed=True)
    assert "布尔确认已停用" in result


def test_multiple_observations_are_summarized(tmp_path, monkeypatch):
    configure_safe_test_runtime(monkeypatch, tmp_path)
    responses = iter(
        [
            json.dumps(
                [
                    {"tool": "get_cpu_usage", "params": {}},
                    {"tool": "get_memory_usage", "params": {}},
                ],
                ensure_ascii=False,
            ),
            "综合报告：CPU 与内存数据已采集。",
        ]
    )

    result = run_agent("检查电脑", lambda _messages: next(responses))
    assert result.status is AgentStatus.COMPLETED
    assert result.response == "综合报告：CPU 与内存数据已采集。"
    assert result.trace_id
    assert get_recent_traces(1)[0]["trace_id"] == result.trace_id
