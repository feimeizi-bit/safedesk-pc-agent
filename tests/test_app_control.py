from tools import app_control


def test_open_app_rejects_non_whitelisted_command():
    result = app_control.open_app("powershell -Command whoami")
    assert result.startswith("安全限制")


def test_open_app_uses_no_shell(monkeypatch):
    captured = {}

    def fake_popen(args, shell):
        captured["args"] = args
        captured["shell"] = shell

    monkeypatch.setattr(app_control.subprocess, "Popen", fake_popen)
    result = app_control.open_app("记事本")
    assert result == "已尝试启动应用: 记事本"
    assert captured == {"args": ["notepad.exe"], "shell": False}
