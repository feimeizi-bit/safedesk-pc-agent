"""
应用控制工具模块
提供打开、关闭应用，列出进程等功能。
注意：关闭应用是强制终止进程，使用前应谨慎。
"""

import subprocess
import psutil
from typing import List, Optional

# 常用应用的启动命令映射（可扩展）
APP_COMMANDS = {
    "记事本": "notepad.exe",
    "计算器": "calc.exe",
    "命令提示符": "cmd.exe",
    "画图": "mspaint.exe",
    "控制面板": "control.exe",
    "任务管理器": "taskmgr.exe",
}

def open_app(app_name: str = None, **kwargs) -> str:
    """
    打开指定的应用程序。支持参数 app_name 或 app。
    支持直接输入可执行文件名（如 notepad.exe）或映射表中的名称。
    返回执行结果描述。
    """
    if app_name is None:
        app_name = kwargs.get('app')
    if not app_name:
        return "错误：未提供应用名称。"

    cmd = APP_COMMANDS.get(app_name)
    if cmd is None:
        supported = "、".join(APP_COMMANDS)
        return f"安全限制：不允许启动未在白名单中的应用。当前支持：{supported}"
    try:
        # 只执行白名单中的固定可执行文件，不经过 shell 解释。
        subprocess.Popen([cmd], shell=False)
        return f"已尝试启动应用: {app_name}"
    except Exception as e:
        return f"启动失败: {str(e)}"

def close_app(process_name: str, force: bool = True) -> str:
    """
    根据进程名关闭应用程序（可强制终止）。
    例如 close_app("notepad.exe") 关闭记事本。
    注意：进程名不区分大小写。
    """
    killed = []
    not_found = True
    for proc in psutil.process_iter(['pid', 'name']):
        try:
            if proc.info['name'].lower() == process_name.lower():
                proc.kill() if force else proc.terminate()
                killed.append(f"{proc.info['name']} (PID {proc.info['pid']})")
                not_found = False
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
    if not_found:
        return f"未找到运行中的进程: {process_name}"
    return f"已关闭 {len(killed)} 个进程: {', '.join(killed)}"

def list_running_processes(filter_name: Optional[str] = None) -> str:
    """
    列出当前运行中的进程。可选按名称过滤（包含即可）。
    返回格式化的字符串。
    """
    processes = []
    for proc in psutil.process_iter(['pid', 'name']):
        try:
            name = proc.info['name']
            if filter_name and filter_name.lower() not in name.lower():
                continue
            processes.append(f"{name} (PID {proc.info['pid']})")
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    if not processes:
        return "未找到匹配的进程。"
    # 限制返回行数，避免过长
    if len(processes) > 50:
        processes = processes[:50]
        processes.append("... (仅显示前50个)")
    return "当前运行中的进程：\n" + "\n".join(processes)


def get_top_processes(limit: int = 5) -> str:
    """Return the processes with the highest current memory usage."""
    limit = max(1, min(int(limit), 10))
    processes = []
    for proc in psutil.process_iter(["pid", "name", "memory_percent"]):
        try:
            processes.append(
                (
                    float(proc.info.get("memory_percent") or 0.0),
                    proc.info.get("name") or "unknown",
                    proc.info["pid"],
                )
            )
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
    processes.sort(reverse=True)
    selected = processes[:limit]
    if not selected:
        return "未能读取进程占用信息。"
    lines = [
        f"{name} (PID {pid})：内存占用 {memory_percent:.2f}%"
        for memory_percent, name, pid in selected
    ]
    return "内存占用最高的进程：\n" + "\n".join(lines)

# 可选：安全确认示例（高阶用法）
def safe_close_app(process_name: str, require_confirm: bool = True) -> str:
    """
    带确认的关闭应用，可在 Agent 中调用前先询问用户。
    这里仅作为示例，实际集成时可通过前端弹窗。
    """
    if require_confirm:
        # 实际项目中应通过前端交互，此处打印提示
        print(f"⚠️ 即将强制关闭进程: {process_name}，是否继续？(y/n)")
        confirm = input().strip().lower()
        if confirm != 'y':
            return "用户取消了关闭操作。"
    return close_app(process_name, force=True)

# 简单测试（直接运行此文件时执行）
# if __name__ == "__main__":
#     print("测试打开记事本：")
#     print(open_app("记事本"))
#     print("\n测试列出包含 'python' 的进程：")
#     print(list_running_processes("python"))
#     print("\n测试关闭记事本（如果已打开）：")
#     print(close_app("notepad.exe"))
