from fastapi.testclient import TestClient

from API import main
from agent.simple_agent import AgentResult, AgentStatus


def test_chat_passes_confirmation_token(monkeypatch):
    captured = {}

    def fake_agent(text, llm_func, *, confirmation_token=None):
        captured.update(text=text, confirmation_token=confirmation_token)
        return AgentResult(
            response="ok",
            status=AgentStatus.COMPLETED,
            plan_hash="abc",
        )

    monkeypatch.setattr(main, "run_agent", fake_agent)
    client = TestClient(main.app)
    response = client.post(
        "/chat",
        json={"text": "", "confirmation_token": "one-time-token"},
    )
    assert response.status_code == 200
    assert response.json() == {
        "response": "ok",
        "status": "completed",
        "confirmation_token": None,
        "plan_hash": "abc",
        "expires_at": None,
        "trace_id": None,
    }
    assert captured == {"text": "", "confirmation_token": "one-time-token"}
