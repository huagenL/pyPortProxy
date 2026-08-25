# pyPortProxy 项目状态：✅ 已完成交付

> 本文档原为交接清单，现更新为最终完成状态记录。
> @author ai-lhg

## 最终验收结果（2026-08-25）

| 验收项 | 结果 |
|---|---|
| pytest 测试套件 | ✅ 52 passed（含回环转发收发、启停幂等、监听冲突、kill 断连、Go 数据兼容等） |
| 引擎冒烟 scripts/smoke_engine.py | ✅ PASS（转发→收发→停止→端口释放→关停） |
| GUI 冒烟 scripts/smoke_gui.py | ✅ PASS（自动建规则、表格渲染"● 运行中"、自动退出码 0） |
| PyInstaller 打包 | ✅ `dist\PortProxyGUI.exe` 11.2MB（onefile 无窗口）；`-OneDir` 目录版同验通过 |
| exe 实机启动 | ✅ OneDir 版实跑：进程稳定、主窗口正常弹出、便携模式行为正确（rules.json 首次保存才创建） |

## 关键实现事实（维护必读）

1. **配置便携模式**（用户需求变更）：默认与主程序同目录 `rules.json`；存在即读、目录可写则首次保存时新建、只读时回退 `%APPDATA%\portproxy\rules.json`。逻辑在 `src/portproxy/__main__.py::default_config_path`，打包态经 `sys.frozen` 判定取 exe 目录。
2. **系统托盘**（用户需求）：点 × 隐藏到托盘（首次有气泡提示），左键单击/双击恢复，右键菜单"显示主窗口/退出"。实现在 `src/portproxy/tray.py`——纯 ctypes 直调 Shell_NotifyIcon，独立线程消息循环，事件经 commands 队列由 Tk 轮询 drain（避免跨线程操作 Tk）。注意 Python 3.12+ 的 wintypes 已移除 WNDPROC/LRESULT，需自定义 WINFUNCTYPE 原型（tray.py 内已处理）。TrackPopupMenu 前必须 SetForegroundWindow，否则菜单不消散（代码已处理）。
3. **Python 3.12+ 停止语义坑**：`asyncio.Server.wait_closed()` 会等全部客户端 handler 结束，与优雅停机互锁 → `RuleRunner.stop()` 已弃用它（见 engine.py 注释），改 close + sleep(0) + 有界宽限轮询。
4. **snapshot 解构顺序**：`(status, last_error, connections)`，历史 bug 源，勿改序。
5. **构建脚本三个 PS5.1 坑**（均已内置处理）：① ps1 必须带 UTF-8 BOM 否则中文注释致语法错乱；② `ErrorActionPreference=Stop` 下外部命令的重定向 stderr（如 `2>$null`）会误杀脚本；③ 受限环境系统 Temp 可能不可写 → 脚本开头把 `TMP/TEMP/TMPDIR` 重定向到 `.tmp\build`。
6. **DSH 沙箱限制备忘**（仅本开发会话，真实用户环境无此问题）：mkdtemp 目录内写入被拒 → pytest 用 conftest 自定义 tmp_path（普通 mkdir）；pip 装 PyInstaller 与 onefile exe 自解压均需提权/正常权限环境。**onefile exe 在正常 Windows 上可直接运行**，OneDir 版是杀软误报或受限环境时的备选。
7. **软件命名**：最终定名 pyPortProxy（用户确认），产物 `pyPortProxy.exe`，窗口标题 "pyPortProxy — TCP 端口转发管理器"。曾候选 localPortForwarding 后被否。托盘行为已通过自动化验证：向主窗口 PostMessage WM_CLOSE 后进程存活 = 缩到托盘不退出。

## 后续可选增强（未排期）

- UDP 转发支持
- 代码签名消除杀软误报
- version_info 资源嵌入 exe 属性面板
- 托盘菜单增加"开机自启"开关

---

## 附：原始待办清单的完成对照

| 原待办 | 状态 |
|---|---|
| 实现 ui.py + __main__.py + 根入口 main.py | ✅ 完成 |
| 编写 pytest 测试套件并跑绿 | ✅ 52 例全绿 |
| 打包配置（icon、spec、build.ps1、README.md） | ✅ 完成 |
| 冒烟验证：pytest 全绿 + GUI 启动 + PyInstaller 实际出包 | ✅ 全部通过 |
