import sqlite3
import re

import sandbox
from tools import file_ops


def test_partial_file_failure_is_automatically_compensated(tmp_path, monkeypatch):
    monkeypatch.setattr(sandbox, "USER_WHITELIST", [tmp_path])
    monkeypatch.setattr(sandbox, "AUDIT_DB_PATH", tmp_path / "audit.db")
    target = tmp_path / "documents"
    target.mkdir()
    first = target / "a.txt"
    second = target / "b.pdf"
    first.write_text("a", encoding="utf-8")
    second.write_text("b", encoding="utf-8")

    original_move = file_ops.shutil.move
    calls = 0

    def fail_second_forward_move(source, destination):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated disk failure")
        return original_move(source, destination)

    monkeypatch.setattr(file_ops.shutil, "move", fail_second_forward_move)
    _preview, prepared = file_ops.prepare_organization_by_extension(str(target))
    result = file_ops.organize_by_extension(
        str(target),
        confirmed=True,
        **prepared,
    )

    assert "已自动回滚" in result
    assert first.exists()
    assert second.exists()
    assert not (target / "txt" / "a.txt").exists()

    with sqlite3.connect(sandbox.AUDIT_DB_PATH) as connection:
        status, completed_count = connection.execute(
            "SELECT status, completed_count FROM file_transactions"
        ).fetchone()
    assert status == "rolled_back"
    assert completed_count == 0


def test_completed_transaction_can_be_previewed_and_undone(tmp_path, monkeypatch):
    monkeypatch.setattr(sandbox, "USER_WHITELIST", [tmp_path])
    monkeypatch.setattr(sandbox, "AUDIT_DB_PATH", tmp_path / "audit.db")
    target = tmp_path / "documents"
    target.mkdir()
    original = target / "a.txt"
    original.write_text("a", encoding="utf-8")

    _preview, prepared = file_ops.prepare_organization_by_extension(str(target))
    result = file_ops.organize_by_extension(str(target), confirmed=True, **prepared)
    transaction_id = re.search(r"事务 (\S+) 共", result).group(1)
    assert (target / "txt" / "a.txt").exists()

    undo_preview, undo_prepared = file_ops.prepare_undo_file_transaction(transaction_id)
    assert "撤销预览" in undo_preview
    undone = file_ops.undo_file_transaction(
        transaction_id,
        confirmed=True,
        **undo_prepared,
    )
    assert "已撤销事务" in undone
    assert original.exists()

    with sqlite3.connect(sandbox.AUDIT_DB_PATH) as connection:
        status = connection.execute(
            "SELECT status FROM file_transactions WHERE transaction_id = ?",
            (transaction_id,),
        ).fetchone()[0]
    assert status == "undone"
