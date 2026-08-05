import shutil
from pathlib import Path
from sandbox import safe_path
from tools.transaction_store import (
    create_file_transaction,
    get_file_transaction,
    update_file_transaction,
)

# ========== 路径解析辅助函数 ==========
def _resolve_user_directory(expression: str) -> Path:
    """
    将自然语言描述的目录转换为绝对路径。
    增强版：如果是一个简单的文件夹名（不含路径分隔符），默认视为桌面上的子文件夹。
    """
    home = Path.home()
    
    desktop = home / "Desktop"
    downloads = home / "Downloads"

    expr = expression.strip()
    lower_expr = expr.lower()

    # 如果是已经存在的绝对路径，直接返回
    if Path(expr).exists():
        return Path(expr)

    # 常见快捷词
    if lower_expr == "桌面":
        return desktop
    if lower_expr in ("下载", "downloads"):
        return downloads

    # 处理 "桌面上的 xxx" 或 "桌面上 xxx"
    import re
    match = re.search(r"桌面(?:上的)?[\s]*([^\\/]+?)(?:文件夹)?$", expr)
    if match:
        folder_name = match.group(1).strip()
        return desktop / folder_name

    # 处理 "桌面/xxx" 格式
    if lower_expr.startswith("桌面/"):
        folder_name = expr[3:]   # 去掉 "桌面/"
        return desktop / folder_name

    # 【关键新增】如果只是一个简单的文件名（不含路径分隔符），默认当作桌面下的子文件夹
    if not ( "/" in expr or "\\" in expr or ":" in expr ):
        return desktop / expr

    # 未匹配任何规则，原样返回
    return Path(expr)


def _checked_directory(directory: str) -> Path:
    abs_path = _resolve_user_directory(directory)
    checked_path = safe_path(str(abs_path), auto_confirm=False)
    if not checked_path.exists():
        raise FileNotFoundError(f"目录 {checked_path} 不存在")
    if not checked_path.is_dir():
        raise NotADirectoryError(f"{checked_path} 不是一个目录")
    return checked_path


def preview_organization_by_extension(directory: str) -> str:
    """Return a non-mutating preview for the high-risk organization tool."""
    return prepare_organization_by_extension(directory)[0]


def _build_planned_moves(abs_path: Path) -> list[tuple[Path, Path]]:
    planned_moves: list[tuple[Path, Path]] = []
    for item in abs_path.iterdir():
        if item.is_file():
            extension = item.suffix[1:].lower() if item.suffix else "no_extension"
            planned_moves.append((item, abs_path / extension / item.name))
    return planned_moves


def prepare_organization_by_extension(directory: str) -> tuple[str, dict]:
    """Create the exact move manifest that a user will later confirm."""
    try:
        abs_path = _checked_directory(directory)
    except (PermissionError, FileNotFoundError, NotADirectoryError) as exc:
        return f"无法生成预览：{exc}", {}

    planned_moves = _build_planned_moves(abs_path)
    if not planned_moves:
        return f"操作预览：目录 {abs_path} 中没有需要整理的文件。", {
            "prepared_operations": []
        }

    groups: dict[str, int] = {}
    conflicts: list[str] = []
    for source, destination in planned_moves:
        extension = destination.parent.name
        groups[extension] = groups.get(extension, 0) + 1
        if destination.exists():
            conflicts.append(source.name)

    summary = "，".join(f"{extension}: {count} 个" for extension, count in sorted(groups.items()))
    conflict_text = "无同名冲突"
    if conflicts:
        conflict_text = f"发现 {len(conflicts)} 个同名冲突，将拒绝执行"
    preview = (
        f"操作预览：将在 {abs_path} 中整理 {len(planned_moves)} 个文件；"
        f"分类为 {summary}；{conflict_text}。"
    )
    prepared_operations = [
        {"source": str(source), "destination": str(destination)}
        for source, destination in planned_moves
    ]
    return preview, {"prepared_operations": prepared_operations}


def organize_by_extension(
    directory: str,
    confirmed: bool = False,
    prepared_operations: list[dict[str, str]] | None = None,
) -> str:
    """
    将指定目录下的文件按扩展名移动到子文件夹。
    支持自然语言路径：如 "桌面"、"下载"、"桌面上的 test 文件夹"。
    """
    if not confirmed:
        return preview_organization_by_extension(directory) + " 操作尚未执行：需要用户明确确认。"
    if prepared_operations is None:
        return "操作已取消：缺少与预览绑定的文件操作清单。"

    try:
        abs_path = _checked_directory(directory)
    except (PermissionError, FileNotFoundError, NotADirectoryError) as exc:
        return f"安全限制：{exc}"

    planned_moves: list[tuple[Path, Path]] = []
    try:
        for operation in prepared_operations:
            if set(operation) != {"source", "destination"}:
                raise ValueError("操作清单字段无效")
            source = Path(operation["source"]).resolve()
            destination = Path(operation["destination"]).resolve()
            if source.parent != abs_path.resolve():
                raise ValueError(f"源文件不属于已确认目录：{source}")
            if destination.parent.parent != abs_path.resolve():
                raise ValueError(f"目标目录超出已确认目录：{destination}")
            planned_moves.append((source, destination))
    except (TypeError, ValueError, OSError) as exc:
        return f"操作已取消：已保存的文件操作清单无效：{exc}"

    current_sources = {
        item.resolve() for item in abs_path.iterdir() if item.is_file()
    }
    expected_sources = {source for source, _ in planned_moves}
    if current_sources != expected_sources:
        return "操作已取消：目录内容在预览后发生变化，请重新生成预览。"

    for source, destination in planned_moves:
        if not source.exists():
            return f"操作已取消：源文件已不存在：{source}"
        if destination.exists():
            return f"操作已取消：目标文件已存在，避免覆盖：{destination}"

    if not planned_moves:
        return "完成！目录中没有需要整理的文件。"

    transaction_id = create_file_transaction(planned_moves)
    completed: list[tuple[Path, Path]] = []
    try:
        for source, destination in planned_moves:
            destination.parent.mkdir(exist_ok=True)
            shutil.move(str(source), str(destination))
            completed.append((source, destination))
            update_file_transaction(
                transaction_id,
                status="running",
                completed_count=len(completed),
                result="执行中",
            )
    except Exception as exc:
        rollback_errors: list[str] = []
        for source, destination in reversed(completed):
            try:
                if source.exists():
                    raise FileExistsError(f"回滚目标已存在：{source}")
                if destination.exists():
                    shutil.move(str(destination), str(source))
                if destination.parent.exists() and not any(destination.parent.iterdir()):
                    destination.parent.rmdir()
            except Exception as rollback_exc:
                rollback_errors.append(str(rollback_exc))

        if rollback_errors:
            result = (
                f"事务 {transaction_id} 执行失败，且自动回滚不完整：{exc}；"
                f"回滚错误：{'；'.join(rollback_errors)}"
            )
            status = "rollback_failed"
        else:
            result = f"事务 {transaction_id} 执行失败，已自动回滚：{exc}"
            status = "rolled_back"
        update_file_transaction(
            transaction_id,
            status=status,
            completed_count=0 if not rollback_errors else len(completed),
            result=result,
        )
        return result

    result = (
        f"完成！事务 {transaction_id} 共整理了 {len(completed)} 个文件，"
        "已按扩展名放入对应子文件夹。"
    )
    update_file_transaction(
        transaction_id,
        status="completed",
        completed_count=len(completed),
        result=result,
    )
    return result


def prepare_undo_file_transaction(transaction_id: str) -> tuple[str, dict]:
    """Prepare the exact reverse moves for a completed file transaction."""
    transaction = get_file_transaction(transaction_id)
    if transaction is None:
        return f"无法生成预览：未找到事务 {transaction_id}", {}
    if transaction["status"] != "completed":
        return (
            f"无法生成预览：事务 {transaction_id} 当前状态为 "
            f"{transaction['status']}，不能撤销。",
            {},
        )

    reverse_operations = [
        {
            "source": operation["destination"],
            "destination": operation["source"],
        }
        for operation in reversed(transaction["operations"])
    ]
    return (
        f"撤销预览：事务 {transaction_id} 将把 "
        f"{len(reverse_operations)} 个文件移回原位置。",
        {"prepared_operations": reverse_operations},
    )


def undo_file_transaction(
    transaction_id: str,
    confirmed: bool = False,
    prepared_operations: list[dict[str, str]] | None = None,
) -> str:
    """Undo a completed organization transaction with compensation on failure."""
    if not confirmed:
        return prepare_undo_file_transaction(transaction_id)[0] + " 操作尚未执行。"
    if prepared_operations is None:
        return "操作已取消：缺少与撤销预览绑定的文件操作清单。"

    transaction = get_file_transaction(transaction_id)
    if transaction is None:
        return f"操作已取消：未找到事务 {transaction_id}"
    if transaction["status"] != "completed":
        return f"操作已取消：事务状态为 {transaction['status']}，不能撤销。"

    expected_operations = [
        {
            "source": operation["destination"],
            "destination": operation["source"],
        }
        for operation in reversed(transaction["operations"])
    ]
    if prepared_operations != expected_operations:
        return "操作已取消：撤销清单与原事务不一致。"

    moves: list[tuple[Path, Path]] = []
    for operation in prepared_operations:
        source = Path(operation["source"]).resolve()
        destination = Path(operation["destination"]).resolve()
        try:
            safe_path(str(source), auto_confirm=False)
            safe_path(str(destination), auto_confirm=False)
        except PermissionError as exc:
            return f"安全限制：{exc}"
        if not source.exists():
            return f"操作已取消：待撤销文件已不存在：{source}"
        if destination.exists():
            return f"操作已取消：原位置已存在同名文件：{destination}"
        moves.append((source, destination))

    completed: list[tuple[Path, Path]] = []
    update_file_transaction(
        transaction_id,
        status="undoing",
        completed_count=transaction["completed_count"],
        result="正在撤销",
    )
    try:
        for source, destination in moves:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(destination))
            completed.append((source, destination))
    except Exception as exc:
        compensation_errors: list[str] = []
        for source, destination in reversed(completed):
            try:
                if source.exists():
                    raise FileExistsError(f"补偿目标已存在：{source}")
                if destination.exists():
                    shutil.move(str(destination), str(source))
            except Exception as compensation_exc:
                compensation_errors.append(str(compensation_exc))
        status = "rollback_failed" if compensation_errors else "completed"
        result = f"撤销事务 {transaction_id} 失败：{exc}"
        if compensation_errors:
            result += f"；恢复原状态失败：{'；'.join(compensation_errors)}"
        else:
            result += "；已恢复撤销前状态。"
        update_file_transaction(
            transaction_id,
            status=status,
            completed_count=transaction["completed_count"],
            result=result,
        )
        return result

    for source, _destination in moves:
        if source.parent.exists() and not any(source.parent.iterdir()):
            source.parent.rmdir()
    result = f"已撤销事务 {transaction_id}，{len(completed)} 个文件已移回原位置。"
    update_file_transaction(
        transaction_id,
        status="undone",
        completed_count=0,
        result=result,
    )
    return result
