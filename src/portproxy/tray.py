"""系统托盘图标（Win32 Shell_NotifyIcon，纯标准库 ctypes 实现，零第三方依赖）。

线程模型：
- 托盘拥有独立线程与原生消息循环（GetMessage）；
- 托盘事件（显示/退出）写入 commands 队列，由 Tk 主线程在轮询中 drain 后处理，
  避免跨线程直接操作 Tk 控件。

@author ai-lhg
"""

from __future__ import annotations

import ctypes
import sys
import threading
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"


def resolve_icon_path() -> Path:
    """定位 icon.ico：打包后在解包目录(_MEIPASS)，开发态在项目 assets 下。"""
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    else:
        base = Path(__file__).resolve().parent.parent.parent / "assets"
    return base / "icon.ico"


class TrayIcon:
    """系统托盘图标外壳；非 Windows 平台为无害空操作。"""

    #: 托盘线程产生的事件队列，元素为 "show" / "quit"（UI 侧 drain 处理）
    def __init__(self, icon_path: Path | None = None, tooltip: str = "pyPortProxy") -> None:
        self.tooltip = tooltip
        self.icon_path = str(icon_path or resolve_icon_path())
        self._lock = threading.Lock()
        self._commands: list[str] = []
        self._hwnd = None
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        if IS_WINDOWS:
            self._init_win32()

    # ==================== 公开 API（任意线程） ====================

    def start(self) -> None:
        """启动托盘线程（幂等）。"""
        if not IS_WINDOWS or self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="pyportproxy-tray", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """停止托盘并移除图标（幂等）。"""
        if not IS_WINDOWS or self._thread is None:
            return
        if self._hwnd:
            ctypes.windll.user32.PostMessageW(self._hwnd, 0x0010, 0, 0)  # WM_CLOSE
        self._thread.join(timeout=2.0)
        self._thread = None

    def notify(self, title: str, message: str) -> None:
        """显示托盘气泡通知。"""
        if IS_WINDOWS and self._hwnd:
            self._show_balloon(title, message)

    def drain(self) -> list[str]:
        """取走全部待处理的托盘事件（UI 主线程调用）。"""
        with self._lock:
            commands, self._commands = self._commands, []
        return commands

    def _emit(self, command: str) -> None:
        with self._lock:
            self._commands.append(command)

    # ==================== Win32 实现 ====================

    def _init_win32(self) -> None:
        from ctypes import wintypes

        self._wintypes = wintypes
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        # ---- 结构体 ----
        # Python 3.12+ 的 wintypes 不再提供 WNDPROC，需自定义回调原型
        wndproc_type = ctypes.WINFUNCTYPE(
            ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
        )
        self._WNDPROC_TYPE = wndproc_type

        class NOTIFYICONDATAW(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD),
                ("hWnd", wintypes.HWND),
                ("uID", wintypes.UINT),
                ("uFlags", wintypes.UINT),
                ("uCallbackMessage", wintypes.UINT),
                ("hIcon", wintypes.HICON),
                ("szTip", wintypes.WCHAR * 128),
                ("dwState", wintypes.DWORD),
                ("dwStateMask", wintypes.DWORD),
                ("szInfo", wintypes.WCHAR * 256),
                ("uVersion", wintypes.UINT),
                ("szInfoTitle", wintypes.WCHAR * 64),
                ("dwInfoFlags", wintypes.DWORD),
            ]

        class WNDCLASSEXW(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.UINT),
                ("style", wintypes.UINT),
                ("lpfnWndProc", wndproc_type),
                ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int),
                ("hInstance", wintypes.HINSTANCE),
                ("hIcon", wintypes.HICON),
                ("hCursor", wintypes.HANDLE),
                ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR),
                ("lpszClassName", wintypes.LPCWSTR),
                ("hIconSm", wintypes.HICON),
            ]

        class POINT(ctypes.Structure):
            _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]

        class MSG(ctypes.Structure):
            _fields_ = [
                ("hwnd", wintypes.HWND),
                ("message", wintypes.UINT),
                ("wParam", wintypes.WPARAM),
                ("lParam", wintypes.LPARAM),
                ("time", wintypes.DWORD),
                ("pt", POINT),
            ]

        self._NID_TYPE = NOTIFYICONDATAW
        self.NIM_ADD, self.NIM_MODIFY, self.NIM_DELETE = 0, 1, 2
        self.NIF_MESSAGE, self.NIF_ICON, self.NIF_TIP, self.NIF_INFO = 0x1, 0x2, 0x4, 0x10
        self.NIIF_INFO = 0x1
        self.WM_TRAYICON = 0x0400 + 1  # WM_USER+1 托盘回调消息
        self.WM_CLOSE, self.WM_DESTROY = 0x0010, 0x0002
        self.WM_LBUTTONUP, self.WM_LBUTTONDBLCLK, self.WM_RBUTTONUP = 0x0202, 0x0203, 0x0205
        self.TPM_RIGHTBUTTON, self.TPM_RETURNCMD = 0x2, 0x100
        self.MF_STRING, self.MF_SEPARATOR = 0x0, 0x800
        self.ID_SHOW, self.ID_QUIT = 2001, 2002
        self.IMAGE_ICON, self.LR_LOADFROMFILE = 1, 0x10

        self._user32 = user32
        self._kernel32 = kernel32
        user32.CreateWindowExW.argtypes = [
            wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
        ]
        user32.CreateWindowExW.restype = wintypes.HWND
        user32.DefWindowProcW.argtypes = [
            wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
        ]
        user32.DefWindowProcW.restype = ctypes.c_ssize_t  # LRESULT
        user32.GetMessageW.argtypes = [
            ctypes.POINTER(MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT,
        ]
        user32.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]
        user32.RegisterClassExW.restype = wintypes.ATOM
        kernel32.GetCurrentThreadId.restype = wintypes.DWORD

        # 保存结构体定义供 _run 使用
        self._MSG_TYPE = MSG
        self._POINT_TYPE = POINT
        self._WNDCLASSEX_TYPE = WNDCLASSEXW

    def _run(self) -> None:
        """托盘线程主体：注册窗口类 → 创建消息窗口 → 添加图标 → 消息循环。"""
        ctypes.windll.shell32.Shell_NotifyIconW.restype = ctypes.c_int

        # WNDPROC 回调必须保存在实例属性，防止 GC 后崩溃
        wndproc = self._WNDPROC_TYPE(self._wndproc)
        self._wndproc_ref = wndproc

        hinstance = self._kernel32.GetModuleHandleW(None)
        class_name = "PyPortProxyTrayWnd"
        wcex = self._WNDCLASSEX_TYPE()
        wcex.cbSize = ctypes.sizeof(wcex)
        wcex.lpfnWndProc = wndproc
        wcex.hInstance = hinstance
        wcex.lpszClassName = class_name
        if not self._user32.RegisterClassExW(ctypes.byref(wcex)):
            return

        style = 0x80000000 | 0x40000000  # WS_POPUP | WS_SYSMENU（不可见工具窗口）
        self._hwnd = self._user32.CreateWindowExW(
            0, class_name, "pyPortProxy tray", style,
            0, 0, 0, 0, None, None, hinstance, None,
        )
        if not self._hwnd:
            return
        self._thread_id = self._kernel32.GetCurrentThreadId()

        # 任务栏重建广播（explorer 重启后自动恢复图标）
        self._taskbar_created_msg = self._user32.RegisterWindowMessageW("TaskbarCreated")

        self._load_icon()
        self._add_or_modify_icon(self.NIM_ADD)

        msg = self._MSG_TYPE()
        while True:
            result = self._user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if result <= 0:  # WM_QUIT 或错误
                break
            self._user32.TranslateMessage(ctypes.byref(msg))
            self._user32.DispatchMessageW(ctypes.byref(msg))

        self._remove_icon()

    def _load_icon(self) -> None:
        """从 ico 文件加载小尺寸图标；失败退回默认应用图标。"""
        hicon = self._user32.LoadImageW(
            None, self.icon_path, self.IMAGE_ICON,
            16, 16, self.LR_LOADFROMFILE,
        )
        if not hicon:
            hicon = self._user32.LoadIconW(None, 32512)  # IDI_APPLICATION
        self._hicon = hicon

    def _make_nid(self, flags: int) -> any:
        nid = self._NID_TYPE()
        nid.cbSize = ctypes.sizeof(nid)
        nid.hWnd = self._hwnd
        nid.uID = 1
        nid.uFlags = flags
        nid.uCallbackMessage = self.WM_TRAYICON
        nid.hIcon = self._hicon
        tip = self.tooltip[:127]
        nid.szTip = tip
        return nid

    def _add_or_modify_icon(self, mode: int) -> None:
        nid = self._make_nid(self.NIF_MESSAGE | self.NIF_ICON | self.NIF_TIP)
        ctypes.windll.shell32.Shell_NotifyIconW(mode, ctypes.byref(nid))

    def _remove_icon(self) -> None:
        nid = self._NID_TYPE()
        nid.cbSize = ctypes.sizeof(nid)
        nid.hWnd = self._hwnd
        nid.uID = 1
        ctypes.windll.shell32.Shell_NotifyIconW(self.NIM_DELETE, ctypes.byref(nid))

    def _show_balloon(self, title: str, message: str) -> None:
        nid = self._make_nid(self.NIF_INFO)
        nid.szInfo = message[:255]
        nid.szInfoTitle = title[:63]
        nid.dwInfoFlags = self.NIIF_INFO
        ctypes.windll.shell32.Shell_NotifyIconW(self.NIM_MODIFY, ctypes.byref(nid))

    def _wndproc(self, hwnd, message, wparam, lparam):
        """托盘窗口过程：分发托盘鼠标事件 / 任务栏重建 / 关闭销毁。"""
        umsg = message & 0xFFFFFFFF
        if umsg == self.WM_TRAYICON:
            event = lparam & 0xFFFF
            if event in (self.WM_LBUTTONUP, self.WM_LBUTTONDBLCLK):
                self._emit("show")
            elif event == self.WM_RBUTTONUP:
                self._popup_menu(hwnd)
            return 0
        if hasattr(self, "_taskbar_created_msg") and umsg == self._taskbar_created_msg:
            self._add_or_modify_icon(self.NIM_ADD)  # explorer 重启后重挂图标
            return 0
        if umsg == self.WM_CLOSE:
            self._user32.DestroyWindow(hwnd)
            return 0
        if umsg == self.WM_DESTROY:
            ctypes.windll.user32.PostQuitMessage(0)
            return 0
        return self._user32.DefWindowProcW(hwnd, umsg, wparam, lparam)

    def _popup_menu(self, hwnd) -> None:
        """右键弹出菜单；TPM_RETURNCMD 直接返回所选命令 ID。"""
        pt = self._POINT_TYPE()
        self._user32.GetCursorPos(ctypes.byref(pt))
        hmenu = self._user32.CreatePopupMenu()
        self._user32.AppendMenuW(hmenu, self.MF_STRING, self.ID_SHOW, "显示主窗口")
        self._user32.AppendMenuW(hmenu, self.MF_SEPARATOR, 0, None)
        self._user32.AppendMenuW(hmenu, self.MF_STRING, self.ID_QUIT, "退出")
        # TrackPopupMenu 前必须置前台，否则菜单无法自动消失（Win32 经典坑）
        self._user32.SetForegroundWindow(hwnd)
        chosen = self._user32.TrackPopupMenu(
            hmenu, self.TPM_RIGHTBUTTON | self.TPM_RETURNCMD, pt.x, pt.y, 0, hwnd, None
        )
        self._user32.DestroyMenu(hmenu)
        self._user32.PostMessageW(hwnd, 0x0093, 0, 0)  # WM_NULL 收尾，确保菜单消散

        if chosen == self.ID_SHOW:
            self._emit("show")
        elif chosen == self.ID_QUIT:
            self._emit("quit")

