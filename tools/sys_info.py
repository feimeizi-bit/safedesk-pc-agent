import psutil

def get_cpu_usage() -> str:
    
    cpu_percent = psutil.cpu_percent(interval=1)
    return f"CPU使用率:{cpu_percent}%"

def get_memory_usage() -> str:

    mem = psutil.virtual_memory()
    total_gb = mem.total /(1024**3)
    used_gb = mem.used / (1024**3)
    available_gb = mem.available / (1024**3)
    return (f"内存使用:{mem.percent}%"
            f"(已用 {used_gb:.1f}GB / 总计 {total_gb:.1f}GB,"
            f"可用 {available_gb:.1f}GB)")

def get_disk_usage(path: str='/') -> str:

    try:
        usage=psutil.disk_usage(path)
        total_gb = usage.total /(1024**3)
        used_gb = usage.used /(1024**3)
        free_gb = usage.free / (1024**3)
        return (f"磁盘使用 ({path}): {usage.percent}% "
                f"(已用 {used_gb:.1f}GB / 总计 {total_gb:.1f}GB, "
                f"剩余 {free_gb:.1f}GB)")
    except FileNotFoundError:
        return f"错误:路径{path}不存在"
    except Exception as e:
        return f"获取磁盘信息失败:{e}"
    
def get_system_summary() -> str:
    """
    获取系统状态摘要（CPU、内存、磁盘）。
    返回格式化的多行字符串。
    """
    cpu = get_cpu_usage()
    mem = get_memory_usage()
    import os
    system_drive = os.environ.get("SystemDrive", "C:") + "\\"
    disk = get_disk_usage(system_drive)
    return f"{cpu}\n{mem}\n{disk}"

if __name__ == "__main__":
    print("=== 系统状态摘要 ===")
    print(get_system_summary())
