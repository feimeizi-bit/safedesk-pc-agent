# sandbox.py
"""
安全沙箱模块 - 白名单+黑名单+人工确认方案
提供文件系统路径限制、操作审计、高危操作确认功能。
"""

import os
import time
import sqlite3
import json
from pathlib import Path
from functools import wraps
from typing import Optional, Callable, Any, List

# ========== 配置 ==========
# 白名单目录（直接允许访问，无需确认）
# 可根据需要修改或添加
USER_WHITELIST = [
    Path.home() / "Desktop",
    Path.home() / "Downloads",
    Path.home() / "Documents",
    Path.home() / "Pictures",
    Path.home() / "Videos",
    Path.home() / "Music",
    Path.home() / "PC_Agent_Sandbox",   # 自己的沙箱目录
]

# 黑名单目录（绝对禁止访问，即使确认也不行）
SYSTEM_BLACKLIST = [
    Path("C:/Windows"),
    Path("C:/Program Files"),
    Path("C:/Program Files (x86)"),
    Path("/etc"),
    Path("/usr"),
    Path("/bin"),
    Path("/sbin"),
    Path("/System"),
    Path("/Library"),
]

# 高危工具列表（执行前需要用户确认）
DANGEROUS_TOOLS = {
    "organize_by_extension",  # 移动文件
    "delete_file",            # 删除文件
    "move_file",              # 移动文件
    "clean_temp_files",       # 清理临时文件
}

# 审计日志数据库路径。允许部署环境覆盖，默认保存在项目运行数据目录中。
_DEFAULT_RUNTIME_DIR = Path(__file__).resolve().parent / ".pc_agent"
AUDIT_DB_PATH = Path(
    os.environ.get("PC_AGENT_AUDIT_DB", str(_DEFAULT_RUNTIME_DIR / "audit.db"))
)

# 全局确认回调函数（由前端设置）
_confirm_callback: Optional[Callable[[Path], bool]] = None

# ========== 辅助函数 ==========
def is_path_blacklisted(path: Path) -> bool:
    """检查路径是否在黑名单中（或其子路径）"""
    abs_path = path.resolve()
    for banned in SYSTEM_BLACKLIST:
        try:
            abs_path.relative_to(banned.resolve())
            return True
        except (ValueError, FileNotFoundError):
            continue
    return False

def is_path_whitelisted(path: Path) -> bool:
    """检查路径是否在白名单中（或其子路径）"""
    abs_path = path.resolve()
    for allowed in USER_WHITELIST:
        try:
            abs_path.relative_to(allowed.resolve())
            return True
        except (ValueError, FileNotFoundError):
            continue
    return False

def set_confirm_callback(callback: Callable[[Path], bool]):
    """设置路径确认回调函数，用于前端弹窗确认"""
    global _confirm_callback
    _confirm_callback = callback

def safe_path(user_path: str, auto_confirm: bool = False) -> Path:
    """
    路径安全检查：
    - 黑名单直接拒绝
    - 白名单直接允许
    - 其他路径通过回调函数询问用户确认
    返回安全后的 Path 对象，若拒绝则抛出 PermissionError
    """
    requested = Path(user_path).expanduser().resolve()
    
    # 1. 黑名单检查
    if is_path_blacklisted(requested):
        raise PermissionError(f"禁止访问系统敏感目录: {requested}")
    
    # 2. 白名单检查
    if is_path_whitelisted(requested):
        return requested
    
    # 3. 其他路径：需要用户确认
    if auto_confirm:
        # 自动确认模式（用于内部已确认的场景）
        return requested
    
    if _confirm_callback is not None:
        if _confirm_callback(requested):
            return requested
        else:
            raise PermissionError(f"用户拒绝访问路径: {requested}")
    else:
        # 没有确认回调时，默认拒绝（安全优先）
        raise PermissionError(f"路径不在白名单内且未提供确认机制: {requested}")

# ========== 审计日志 ==========
def init_audit_db():
    """初始化审计数据库（首次调用时自动创建表）"""
    AUDIT_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(AUDIT_DB_PATH))
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            tool_name TEXT,
            params TEXT,
            result TEXT,
            user_confirmed INTEGER
        )
    ''')
    conn.commit()
    conn.close()

def log_audit(tool_name: str, params: dict, result: str, user_confirmed: bool = False):
    """记录工具调用日志"""
    init_audit_db()
    conn = sqlite3.connect(str(AUDIT_DB_PATH))
    c = conn.cursor()
    c.execute('''
        INSERT INTO audit_log (timestamp, tool_name, params, result, user_confirmed)
        VALUES (?, ?, ?, ?, ?)
    ''', (
        time.strftime("%Y-%m-%d %H:%M:%S"),
        tool_name,
        json.dumps(params, ensure_ascii=False, default=str),
        result,
        1 if user_confirmed else 0,
    ))
    conn.commit()
    conn.close()

def get_audit_log(limit: int = 50) -> List[tuple]:
    """获取最近的审计日志"""
    limit = max(1, min(int(limit), 500))
    init_audit_db()
    conn = sqlite3.connect(str(AUDIT_DB_PATH))
    c = conn.cursor()
    c.execute('SELECT timestamp, tool_name, params, result, user_confirmed FROM audit_log ORDER BY id DESC LIMIT ?', (limit,))
    rows = c.fetchall()
    conn.close()
    return rows

# ========== 高危操作确认装饰器 ==========
def require_confirmation(func: Callable) -> Callable:
    """
    装饰器：对于高风险工具，在执行前请求用户确认。
    确认通过后执行并记录日志；拒绝则返回取消信息。
    """
    @wraps(func)
    def wrapper(*args, **kwargs):
        tool_name = func.__name__
        if tool_name in DANGEROUS_TOOLS:
            # 尝试使用全局确认回调（前端弹窗）
            confirmed = False
            if _confirm_callback is not None:
                # 注意：这里我们期望回调接收路径参数，但危险工具可能没有路径参数
                # 简化处理：对于高危工具，直接调用通用确认（可扩展）
                # 更好的做法：提取第一个参数作为路径，如果没有路径则仅询问是否执行
                # 这里简化为弹出通用对话框
                # 实际项目中可以根据参数内容定制
                confirmed = _confirm_callback(None)  # 无路径参数时仅询问
            else:
                # 命令行回退
                print(f"\n⚠️  即将执行高危操作: {tool_name}")
                print(f"   参数: {args} {kwargs}")
                resp = input("是否继续？(y/N): ").strip().lower()
                if resp == 'y':
                    confirmed = True
            
            if not confirmed:
                result = "用户取消了操作。"
                log_audit(tool_name, {"args": args, "kwargs": kwargs}, result, user_confirmed=False)
                return result
        
        # 执行原函数
        try:
            result = func(*args, **kwargs)
            log_audit(tool_name, {"args": args, "kwargs": kwargs}, str(result), user_confirmed=True)
            return result
        except Exception as e:
            error_msg = f"执行失败: {str(e)}"
            log_audit(tool_name, {"args": args, "kwargs": kwargs}, error_msg, user_confirmed=True)
            return error_msg
    return wrapper

# 可选：为需要路径确认的装饰器扩展
def require_path_confirmation(func: Callable) -> Callable:
    """
    增强版装饰器：提取第一个参数作为路径，使用白名单+黑名单+确认机制。
    适用于工具函数第一个参数为目录或文件路径的场景。
    """
    @wraps(func)
    def wrapper(*args, **kwargs):
        # 尝试获取路径参数（假设第一个参数是路径字符串）
        if len(args) > 0:
            path_str = args[0]
        elif "directory" in kwargs:
            path_str = kwargs["directory"]
        elif "file_path" in kwargs:
            path_str = kwargs["file_path"]
        else:
            # 没有路径参数，退化为普通确认
            return require_confirmation(func)(*args, **kwargs)
        
        # 使用 safe_path 进行安全检查（可能会触发确认回调）
        try:
            safe_path(path_str, auto_confirm=False)
        except PermissionError as e:
            return f"安全限制: {e}"
        
        # 通过安全检查，执行原函数
        return require_confirmation(func)(*args, **kwargs)
    return wrapper
