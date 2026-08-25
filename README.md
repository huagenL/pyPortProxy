# pyPortProxy

一个基于 Python 标准库开发的 Windows 桌面 TCP 端口转发工具，支持多条转发策略的可视化管理（增删改查、批量启停、持久化存储），可打包为**单文件 exe** 双击即用。

> 参考项目：dbirder/portproxy（Go + Walk 版），本项目的功能对照与增强见 `docs/DESIGN.md` 第 8 节。

## 功能特性

- TCP 本地端口转发（如 `0.0.0.0:8080 -> 172.19.12.245:8080`）
- 转发策略管理：新增、编辑、删除、查询，支持**备注**区分不同用途
- 批量操作：多选 / 全选后批量启动、停止、删除
- 新增策略后默认自动启动
- 规则持久化到本地 JSON 文件（与程序同目录，便携模式）
- 状态每 0.5 秒自动刷新，实时显示运行状态与活跃连接数
- 停止规则时可选立即断开存量连接；支持运行中热改目标地址
- **关闭窗口最小化到系统托盘**：点 × 不退出，后台继续转发；左键单击/双击托盘图标恢复窗口，右键菜单可选"显示主窗口 / 退出"
- 桌面端 GUI（Tkinter/ttk，Windows 原生观感）

## 运行环境

- Windows 10/11
- Python 3.11+（开发运行）；打包产物无需 Python

## 快速启动

开发态直接运行：

```powershell
python main.py
```

常用参数：

```powershell
python main.py --config D:\my\rules.json   # 指定规则文件
python main.py --version                   # 查看版本
```

## 打包为 exe

一键构建（自动安装 PyInstaller、生成图标、输出单文件 exe）：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build.ps1
```

产物：`dist\pyPortProxy.exe`（无控制台窗口，双击即用）。

杀软误报时的备选目录版：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build.ps1 -OneDir
# 产物：dist\pyPortProxy\pyPortProxy.exe
```

## 界面使用说明

### 过滤区

- 在“过滤”输入框输入关键词，按监听/目标地址或**备注**实时筛选；
- 点击“刷新”立即重新拉取状态。

### 列表区

- 列：`ID`、`备注`、`监听地址`、`目标地址`、`状态`、`连接数`、`更新时间`、`最后错误`
- 状态标识：● 运行中（绿） / ○ 已停止（灰） / ✕ 异常（红）
- 支持多选（Ctrl / Shift），双击行直接编辑

### 操作按钮

| 按钮 | 行为 |
|---|---|
| 全选 / 取消全选 | 选中全部 / 清空选择 |
| 新增 | 弹窗填写监听、目标地址与备注（地址即时校验），保存后默认自动启动 |
| 编辑 | 编辑选中的一条规则；运行中可热改目标地址与备注，改监听地址需先停止 |
| 删除 | 删除选中的一条或多条规则 |
| 启动 / 停止 | 批量启停选中策略 |

> 批量操作部分失败时会弹窗列出失败规则及原因。

## 转发规则示例

- 监听：`0.0.0.0:8080`
- 目标：`172.19.12.245:8080`

表示外部连接到本机 `8080` 后，流量被转发到 `172.19.12.245:8080`。

## 数据存储位置

默认**便携模式**——规则文件保存在主程序同目录：

```text
rules.json   （exe 旁 / 开发态在项目根）
```

- 文件不存在时不预创建，首次保存规则时自动新建；
- 目录只读（如放在 Program Files）时自动回退到 `%APPDATA%\portproxy\rules.json`；
- 兼容 Go 版 dbirder/portproxy 的数据文件格式，可直接复制其 `rules.json` 无缝继承。

## 项目结构

```text
main.py                      开发/打包通用入口
src/portproxy/
  ├── model.py               规则与状态模型
  ├── validators.py          地址语法校验（纯语法级，无网络 IO）
  ├── store.py               JSON 持久化（临时文件 + 原子替换）
  ├── engine.py              asyncio 转发引擎（独立线程串行执行变更）
  ├── ui.py                  Tkinter 主窗口与对话框
  ├── tray.py                系统托盘（ctypes 直调 Shell_NotifyIcon，零依赖）
  └── __main__.py            python -m portproxy 入口
tests/                       pytest 测试套件（52 例）
scripts/build.ps1            一键打包脚本
scripts/make_icon.py         图标生成（纯标准库）
portproxy.spec               PyInstaller 配置
docs/DESIGN.md               详细设计文档
```

## 开发与测试

```powershell
python -m pytest tests -q          # 运行测试
python scripts\smoke_engine.py     # 引擎冒烟（脱离 pytest）
python scripts\smoke_gui.py        # GUI 冒烟（自动启停界面）
```

## 常见问题

### 1) 启动某条规则失败

常见原因：端口被占用 / 需要管理员权限（<1024）/ 目标不可达。可在“最后错误”列查看详细信息。

### 2) 杀毒软件报毒

Python 单文件 exe 偶发被误报，属常见现象。可改用 `-OneDir` 目录分发版，或对 exe 做代码签名。

### 3) 为什么配置文件出现在 exe 旁边

便携模式设计：配置随软件走，方便 U 盘/绿色部署。删除 `rules.json` 即完全重置。

## 说明

- 当前版本仅支持 TCP 转发
- 当前为桌面应用模式，未做系统服务化
