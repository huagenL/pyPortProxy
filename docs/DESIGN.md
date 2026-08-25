# pyPortProxy 详细设计文档

> 参考：dbirder/portproxy（Go + Walk 版）
> 目标：用 Python 复刻其全部功能并修复已知缺陷，最终交付可双击运行的 Windows exe。
>
> @author ai-lhg

## 1. 技术选型（基于本机环境实测）

| 项 | 选择 | 理由 |
|---|---|---|
| 运行时 | 本机 Python 3.13.0 | 实测可用，无需新装 |
| GUI | Tkinter/ttk（标准库 8.6.14） | 实测可用；零第三方依赖；PyInstaller 支持最成熟；ttk.Treeview 完整覆盖表格需求 |
| 转发引擎 | asyncio（标准库） | `asyncio.start_server` 对应 Go 的 net.Listen；流式双向泵对应 io.Copy；优雅停止语义完整 |
| 并发桥接 | `asyncio.run_coroutine_threadsafe` + Tk `after()` | UI 主线程与引擎线程解耦 |
| 测试 | pytest 9 + pytest-asyncio 1.3 | 本机已安装 |
| 代码质量 | ruff + mypy | 本机已安装，CI 可选 |
| 打包 | PyInstaller ≥ 6.10（唯一需新装的开发工具） | 6.10+ 支持 Python 3.13；onefile + windowed |

**明确不做**：UDP 转发（一期范围外）、系统服务化、开机自启（列为后续增强项）。

## 2. 系统架构

```text
┌────────────────────────── Tk 主线程 ──────────────────────────┐
│  ui.py                                                        │
│  ├─ MainWindow（ttk.Treeview 规则表 / 过滤框 / 操作按钮 / 状态栏）│
│  └─ RuleDialog（新增/编辑共用 Toplevel）                        │
│        │ 每 500ms after() 拉取快照刷新        ↑ 提交协程任务     │
└────────┼───────────────────────────────────────┬──────────────┘
         │ snapshot() 同步只读                    │ run_coroutine_threadsafe
┌────────▼─────────────── 引擎线程（daemon） ──────▼──────────────┐
│  engine.py                                                     │
│  ├─ Engine：规则表权威持有者，所有变更在 loop 内串行执行           │
│  ├─ RuleRunner：单条规则 listen→accept→双向 pump，状态机         │
│  └─ ConnectionPair：活跃连接跟踪（计数/统计/停止时可强制断开）     │
└────────┬───────────────────────────────────────────────────────┘
         │ 变更后落盘（loop 内串行调用，天然无锁竞争）
┌────────▼─────────────── 任意线程（受文件锁保护） ────────────────┐
│  store.py   FileStore：JSON 原子持久化（tmp + os.replace）       │
│  model.py   Rule / RuleView / RuntimeStatus                     │
└────────────────────────────────────────────────────────────────┘
```

**并发模型核心决策**：不引入大而全的互斥锁。规则表的**一切变更**（增删改启停）都以协程形式提交到引擎 loop 串行执行；UI 只通过两条通道交互：

1. `snapshot()`：同步只读快照（`threading.Lock` 保护的一次浅拷贝），UI 定时器轮询；
2. `submit(coro)`：`run_coroutine_threadsafe` 提交变更，返回 `concurrent.futures.Future` 供弹窗报错。

## 3. 模块设计

### 3.1 model.py — 数据模型

```python
class RuntimeStatus(StrEnum):
    STOPPED = "stopped"
    RUNNING = "running"
    ERROR = "error"

@dataclass
class Rule:
    id: str                 # uuid4().hex[:12]
    listen_addr: str        # "0.0.0.0:8080"
    target_addr: str        # "172.19.12.245:8080"
    created_at: datetime
    updated_at: datetime

@dataclass(frozen=True)
class RuleView:            # UI 快照行（不可变，防 UI 侧误改）
    rule: Rule
    status: RuntimeStatus
    last_error: str
    active_connections: int    # 增强：活跃连接数
```

存储序列化字段名与 Go 版保持一致（`id / listenAddr / targetAddr / createdAt / updatedAt`），**可直接读取 Go 版 `%APPDATA%\portproxy\rules.json` 无缝迁移**。

### 3.2 store.py — JSON 原子持久化

- `load_rules(path) -> list[Rule]`：文件不存在返回 `[]`；空文件返回 `[]`；解析失败抛 `StoreError`（带文件路径上下文）。
- `save_rules(path, rules)`：先写 `*.tmp` 再 `os.replace()`（Windows 原子语义），失败回滚删除 tmp。
- 时间戳 ISO-8601 存储，兼容 Go 版 `RFC3339` 格式的解析（`datetime.fromisoformat` 原生支持）。

### 3.3 engine.py — 转发引擎

#### RuleRunner 状态机

```text
STOPPED --start(listen成功)--> RUNNING --stop/accept异常--> STOPPED
   │                              │
   └--start(listen失败)--> ERROR  └--accept意外异常--> ERROR（保留last_error，UI可见）
```

关键行为（对照 Go 版逐项核对）：

| 行为 | Go 版 | 本设计 |
|---|---|---|
| 重复 start | 幂等返回 nil | 幂等返回 None |
| stop 时存量连接 | 不处理，自然消亡 | **默认优雅关闭：停止接受新连接，存量连接继续服务至自然结束**；`stop(kill=True)` 时立即断开全部存量连接（增强项） |
| 连接转发 | 双 goroutine io.Copy | 两个 `StreamReader→StreamWriter` 泵任务 + 半关（`write_eof()`），`asyncio.gather` 收口 |
| 目标连不上 | 日志记录后关客户端连接 | 向客户端连接回 `ConnectionResetError`（等效 RST），计入 `dropped_connections` 统计 |
| 监听套接字选项 | 默认 | 显式 `reuse_address=True`（Windows 下 SO_REUSEADDR 语义不同，asyncio 已按平台处理，无需额外设置） |

#### Engine（对应 Go 版 service.Manager）

对外 API（UI 线程视角）：

```python
class Engine:
    def start(self) -> None                      # 启动后台 loop 线程 + 加载规则
    def snapshot(self, keyword: str = "") -> list[RuleView]   # 过滤后的只读快照
    def submit(self, coro) -> Future             # 通用提交入口
    # 以下均为便捷封装，内部转成协程 submit 到 loop：
    def create_rule(self, listen, target, autostart=True) -> Future[Rule]
    def update_rule(self, rid, listen, target) -> Future[None]
    def delete_rule(self, rids: list[str]) -> Future[None]
    def start_rule(self, rid) -> Future[None]
    def stop_rule(self, rid, kill=False) -> Future[None]
    def shutdown(self, timeout=5.0) -> None      # 退出前停全部 runner 并关 loop
```

业务规则（与 Go 版对齐）：

1. 地址校验：`host:port` 格式 + `socket.getaddrinfo` 可解析 + 端口 1–65535；
2. 监听地址查重：大小写不敏感（域名场景），新增与编辑均检查；
3. 运行中禁止修改监听地址（目标地址允许热改：停旧泵、后续新连接走新目标——增强项，Go 版需先停）；
4. 新增规则默认自动启动（autostart 参数）；
5. 启动时若同监听地址已有 running 规则则拒绝；
6. 所有变更成功后立即落盘；落盘失败则回滚内存态并把错误抛回 UI。

### 3.4 ui.py — Tkinter 界面

布局（1080×600，ttk 控件 + vista 主题）：

```text
┌ 过滤 [___________] [刷新]                                    ┐
├──────────────────────────────────────────────────────────────┤
│ Treeview(multiselect, show="headings")                       │
│  ID | 监听地址 | 目标地址 | 状态 | 连接数 | 更新时间 | 最后错误   │
├──────────────────────────────────────────────────────────────┤
│ [全选][取消全选] [新增][编辑][删除] [启动][停止]                │
├──────────────────────────────────────────────────────────────┤
│ 状态栏：共 N 条规则 · 运行中 M 条 · 引擎 OK                     │
└──────────────────────────────────────────────────────────────┘
```

交互细节：

- 状态列渲染：running=绿色圆点 ●，stopped=灰 ○，error=红 ✕（Treeview tag 配色）；
- **500ms 定时快照刷新**（修复 Go 版需手动点刷新的缺陷）：仅当快照内容变化时重建行，避免闪烁与选中丢失（按 id diff 更新）；
- 双击行 → 编辑对话框（增强项）；
- 删除前 `askyesno` 确认；批量操作失败聚合成一条消息框列出失败 ID；
- 新增/编辑对话框：两输入框 + 即时校验（格式错误红字提示，禁用保存键）；
- 关窗时 `WM_DELETE_WINDOW` 拦截 → `engine.shutdown()` 后退出，确保端口释放干净。

### 3.5 __main__.py — 入口

- 解析可选参数：`--config <path>`（默认**便携模式**：主程序同目录 `rules.json`——打包后为 exe 旁、开发态为项目根；已存在直接读取，不存在且目录可写则首次保存时新建，目录只读时回退 `%APPDATA%\portproxy\rules.json`）、`--version`；
- 异常兜底：顶层 try/except 弹 `messagebox.showerror` 后以非零码退出。

## 4. 关键流程

### 4.1 启动规则

```text
UI 点击"启动" ─→ engine.start_rule(rid) ─→ run_coroutine_threadsafe
   loop 内: 查重(同监听 running?) ─→ await runner.start()
       └─ asyncio.start_server(pump_factory, ...) 成功 → 状态 RUNNING → save_rules
UI Future.add_done_callback ─→ 失败则 messagebox 显示 last_error
下一轮 500ms tick ─→ 表格该行变绿 ●
```

### 4.2 停止规则（kill=False 优雅模式）

```text
server.close()          # 停止 accept
await server.wait_closed()
存量连接保持 → 各自 pump 自然结束（对端 FIN）→ 计数归零
状态置 STOPPED → 落盘
```

kill=True 时额外对每个 ConnectionPair 两侧 `transport.abort()`（等效 Go 版缺失的能力）。

### 4.3 编辑运行中规则的监听地址

拒绝并提示"请先停止"；修改目标地址：取消现有 pump 任务组、保留 listener，新连接拨号到新目标。

## 5. 测试方案（对齐 Go 版测试语义）

| 文件 | 覆盖 |
|---|---|
| tests/test_store.py | 往返读写；坏 JSON 报错；tmp 残留清理；时间戳兼容 Go 格式 |
| tests/test_model.py | 地址校验矩阵（合法/空/端口越界/不可解析主机） |
| tests/test_engine.py | 回环 echo 转发收发（复刻 Go runner_test）；启停幂等；同监听冲突；stop(kill=True) 断开存量连接；error 态 last_error 透出 |
| tests/test_manager.py | CRUD + 查重 + 运行中改监听拒绝 + 落盘失败回滚（monkeypatch save 抛错） |

pytest-asyncio 驱动 async 用例；测试内 echo server 用 `asyncio.start_server` 自建，随机空闲端口。

## 6. 打包方案

- `requirements-dev.txt`：`pyinstaller>=6.10`
- `scripts/build.ps1`：校验 PyInstaller → 执行 spec → 输出 `dist/PortProxyGUI.exe` → 打印体积与哈希
- `portproxy.spec` 要点：
  - `onefile=True, console=False (windowed)`
  - `name='PortProxyGUI'`，`icon='assets/icon.ico'`
  - `version_file`（产品名/版本/公司信息，改善右键属性观感）
  - excludes：`unittest, pydoc_data, xmlrpc` 等无用模块瘦身
- 预期产物 ~12–15MB；杀软误报时的备选：onedir + zip 分发（脚本提供 `--onedir` 开关）。

## 7. 目录结构

```text
D:\Git_lhg\pyPortProxy\
├── src/portproxy/
│   ├── __init__.py
│   ├── __main__.py        # 入口
│   ├── model.py
│   ├── store.py
│   ├── engine.py          # Engine + RuleRunner + ConnectionPair
│   ├── validators.py      # 地址校验（独立便于测试）
│   └── ui.py              # MainWindow + RuleDialog
├── tests/
│   ├── conftest.py        # 事件循环 fixture、echo server fixture
│   ├── test_store.py
│   ├── test_model.py
│   ├── test_engine.py
│   └── test_manager.py
├── assets/icon.ico
├── scripts/build.ps1
├── docs/DESIGN.md         # 本文档
├── portproxy.spec
├── requirements-dev.txt   # pyinstaller（唯一开发依赖）
└── README.md
```

## 8. 与 Go 版功能对照

| 能力 | Go 版 | Python 版 |
|---|---|---|
| TCP 转发 CRUD / 批量启停 / 多选删除 | ✅ | ✅ |
| JSON 持久化 + 原子写 | ✅ | ✅ 且兼容其数据文件 |
| 过滤 / 新增默认自启 / 冲突检测 | ✅ | ✅ |
| 状态实时刷新 | ❌ 手动 | ✅ 500ms 自动 |
| 活跃连接数可见 | ❌ | ✅ |
| 停止时断开存量连接 | ❌ | ✅ 可选 |
| 运行中热改目标地址 | ❌ | ✅ |
| 单 exe 分发 | ✅ ~10MB | ✅ ~12–15MB |

## 9. 风险与对策

| 风险 | 对策 |
|---|---|
| Tk 观感朴素 | vista 主题 + 合理留白；后续可无痛换 ttkbootstrap（同为 tk 家族，打包不变） |
| PyInstaller 未装 | build.ps1 自动 `pip install -r requirements-dev.txt` |
| 杀软误报 onefile | 提供 --onedir 备选；必要时代码签名 |
| Python 3.13 兼容 | 锁定 PyInstaller≥6.10；CI 冒烟跑一遍打包产物 |
| 端口占用权限（<1024/被占） | error 态 + last_error 直显 UI，README 说明 |

---

*评审通过后即按第 7 节结构开工实现，预计交付顺序：model/store → engine → tests → ui → 打包脚本。*
