"""包命令行入口：python -m portproxy。

@author ai-lhg
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path


def _base_dir() -> Path:
    """主程序所在目录：打包后为 exe 目录（便携模式），开发态为仓库根。"""
    if getattr(sys, "frozen", False):  # PyInstaller 打包后由 bootloader 注入
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent.parent  # src/portproxy/__main__.py → 项目根


def _dir_writable(directory: Path) -> bool:
    """探测目录是否可写（探针文件法）。"""
    try:
        with tempfile.NamedTemporaryFile(prefix=".pp-write-test-", dir=directory):
            pass
        return True
    except OSError:
        return False


def default_config_path() -> Path:
    """规则文件路径：优先与主程序同目录（便携模式）。

    - 已存在 rules.json → 直接使用（无论目录是否可写，至少可读）；
    - 不存在且目录可写   → 使用它（首次保存时自动新建）；
    - 不存在且目录只读   → 回退 %APPDATA%/portproxy/rules.json。

    @author ai-lhg
    """
    candidate = _base_dir() / "rules.json"
    if candidate.exists() or _dir_writable(candidate.parent):
        return candidate

    appdata = os.environ.get("APPDATA")
    fallback_base = Path(appdata) if appdata else Path.home() / ".config"
    return fallback_base / "portproxy" / "rules.json"


def _version() -> str:
    from portproxy import __version__

    return __version__


def main(argv=None) -> int:
    """程序主入口：构建引擎、托盘与 GUI 并进入事件循环。"""
    parser = argparse.ArgumentParser(
        prog="pyPortProxy", description="pyPortProxy - Windows TCP 端口转发可视化管理工具"
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=default_config_path(),
        help="规则文件路径（默认与主程序同目录的 rules.json，只读时回退 %%APPDATA%%）",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {_version()}")
    args = parser.parse_args(argv)

    # 延迟导入，保证 --version 不必加载 GUI 栈
    import tkinter as tk
    from tkinter import messagebox

    from portproxy.engine import Engine
    from portproxy.tray import TrayIcon
    from portproxy.ui import PortProxyApp

    root = tk.Tk()
    root.withdraw()  # 初始化期间隐藏窗口，避免空白闪烁

    engine = Engine(args.config)
    try:
        engine.start()
    except Exception as exc:  # noqa: BLE001 - 顶层兜底
        messagebox.showerror("启动失败", f"引擎启动失败：\n{exc}")
        root.destroy()
        return 1

    tray = TrayIcon(tooltip="pyPortProxy — TCP 端口转发管理器")
    tray.start()
    PortProxyApp(root, engine, tray=tray)
    root.deiconify()

    try:
        root.mainloop()
    except Exception:  # noqa: BLE001 - 顶层兜底
        import traceback

        traceback.print_exc()
        messagebox.showerror("错误", "发生未处理的异常，详情见控制台输出。")
        return 1
    finally:
        # mainloop 正常退出路径已在 really_quit 中停过托盘，这里兜底防泄漏
        tray.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
