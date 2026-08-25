"""异步 TCP 转发引擎。

线程模型：
- 引擎持有独立事件循环线程，所有规则变更协程在其中串行执行；
- UI 线程通过 snapshot() 获取只读快照，通过 submit()/便捷方法提交变更；
- 规则表结构访问由 threading.RLock 保护，锁不跨 await 持有。

对应参考项目 internal/proxy(Runner) 与 internal/service(Manager) 两层。

@author ai-lhg
"""

from __future__ import annotations

import asyncio
import logging
import threading
import uuid
from concurrent.futures import Future
from pathlib import Path

from portproxy import store
from portproxy.model import Rule, RuleView, RuntimeStatus, utc_now
from portproxy.validators import split_host_port, validate_address

logger = logging.getLogger(__name__)

_READ_CHUNK = 64 * 1024          # 单次泵转发的数据块大小
_STOP_GRACE_SECONDS = 2.0        # 优雅停止时等待存量连接结束的上限


class EngineError(RuntimeError):
    """业务层错误，消息可直接展示给用户。"""


class ConnectionPair:
    """一条 客户端↔目标 连接的双向泵任务。"""

    def __init__(
        self,
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
        target_getter,
    ) -> None:
        self._client_reader = client_reader
        self._client_writer = client_writer
        self._target_getter = target_getter  # 返回当前目标地址（支持热改）
        self._target_writer: asyncio.StreamWriter | None = None

    async def run(self) -> None:
        """连接目标并双向搬运数据，任一方向结束即收口。"""
        try:
            host, port = split_host_port(self._target_getter())
            target_reader, self._target_writer = await asyncio.open_connection(host, port)
        except OSError as exc:
            logger.warning("connect target failed: %s", exc)
            # 无法建立目标连接：关闭客户端连接（FIN），计入丢弃
            self._close_client()
            return

        await asyncio.gather(
            self._pump(self._client_reader, self._target_writer),
            self._pump(target_reader, self._client_writer),
            return_exceptions=True,
        )

    async def _pump(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            while True:
                data = await reader.read(_READ_CHUNK)
                if not data:
                    break
                writer.write(data)
                await writer.drain()
            # 半关：告知对侧本方向结束，另一方向继续
            writer.write_eof()
        except (ConnectionError, TimeoutError, asyncio.CancelledError):
            raise
        except OSError as exc:  # pragma: no cover - 平台相关套接字错误
            logger.debug("pump error: %s", exc)

    def abort(self) -> None:
        """强制断开两侧连接（发送 RST）。"""
        for writer in (self._client_writer, self._target_writer):
            if writer is not None and not writer.transport.is_closing():
                writer.transport.abort()

    def _close_client(self) -> None:
        try:
            self._client_writer.close()
        except OSError:  # pragma: no cover
            pass


class RuleRunner:
    """单条规则的生命周期管理：listen → accept → 双向泵。

    状态字段可能被引擎线程写、UI 线程读，用轻量锁保护。
    """

    def __init__(self, rule: Rule) -> None:
        self.rule_id = rule.id
        self.listen_addr = rule.listen_addr
        self.target_addr = rule.target_addr  # 仅引擎线程读写（支持热改目标）
        self._server: asyncio.Server | None = None
        self._connections: set[ConnectionPair] = set()
        self._state_lock = threading.Lock()
        self._status = RuntimeStatus.STOPPED
        self._last_error = ""

    # ---------- 引擎线程调用 ----------

    async def start(self) -> None:
        """启动监听；失败置 ERROR 并抛 EngineError。"""
        with self._state_lock:
            running = self._status is RuntimeStatus.RUNNING
        if running and self._server is not None:
            return

        host, port = split_host_port(self.listen_addr)
        try:
            server = await asyncio.start_server(self._on_client, host=host, port=port)
        except OSError as exc:
            self._set_state(RuntimeStatus.ERROR, f"监听失败: {exc}")
            raise EngineError(f"监听 {self.listen_addr} 失败: {exc}") from exc

        self._server = server
        self._set_state(RuntimeStatus.RUNNING, "")
        logger.info(
            "runner[%s] listening on %s -> %s", self.rule_id, self.listen_addr, self.target_addr
        )

    async def stop(self, kill: bool = False) -> None:
        """停止规则。

        kill=False 优雅模式：停止接受新连接，存量连接最多宽限期内自然结束；
        kill=True  立即断开全部存量连接。
        """
        if kill:
            # 先断存量连接，避免关闭 listener 后仍需等待 handler 收尾
            for pair in list(self._connections):
                pair.abort()

        server, self._server = self._server, None
        if server is not None:
            server.close()
            # 注意：不调用 wait_closed()——Python 3.12+ 起它会等待全部客户端
            # handler 结束，与“存量连接自然消亡”的优雅停机语义互相等待造成死锁。
            await asyncio.sleep(0)

        await self._wait_connections(timeout=_STOP_GRACE_SECONDS)
        self._set_state(RuntimeStatus.STOPPED, "")
        logger.info("runner[%s] stopped (kill=%s)", self.rule_id, kill)

    async def retarget(self, new_target: str) -> None:
        """热改目标地址：仅影响之后新建的连接。"""
        self.target_addr = new_target

    # ---------- 内部 ----------

    async def _on_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        pair = ConnectionPair(reader, writer, lambda: self.target_addr)
        self._connections.add(pair)
        try:
            await pair.run()
        finally:
            self._connections.discard(pair)

    async def _wait_connections(self, timeout: float) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while self._connections and loop.time() < deadline:
            await asyncio.sleep(0.05)
        # 超时后强制清场，避免停止操作无限挂起
        for pair in list(self._connections):
            pair.abort()

    # ---------- 线程安全读取 ----------

    def snapshot(self) -> tuple[RuntimeStatus, str, int]:
        """返回 (状态, 最后错误, 活跃连接数)。"""
        with self._state_lock:
            return self._status, self._last_error, len(self._connections)

    def _set_state(self, status: RuntimeStatus, last_error: str) -> None:
        with self._state_lock:
            self._status = status
            self._last_error = last_error


class Engine:
    """转发引擎：规则权威持有者与批量操作入口。"""

    def __init__(self, rules_path: str | Path) -> None:
        self._path = Path(rules_path)
        self._rules: dict[str, Rule] = {}
        self._runners: dict[str, RuleRunner] = {}
        self._lock = threading.RLock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self.load_error: str = ""

    # ==================== 生命周期（任意线程可调） ====================

    def start(self) -> None:
        """加载配置并启动引擎事件循环线程。"""
        if self._thread is not None:
            raise EngineError("引擎已启动")
        try:
            loaded = store.load_rules(self._path)
        except store.StoreError as exc:
            # 配置损坏不应导致软件不可用：记录错误后以空表运行
            self.load_error = str(exc)
            logger.error("load rules failed: %s", exc)
            loaded = []
        with self._lock:
            self._rules = {rule.id: rule for rule in loaded}
            self._runners = {}

        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._loop.run_forever, name="portproxy-engine", daemon=True
        )
        self._thread.start()

    def shutdown(self, timeout: float = 5.0) -> None:
        """停止全部规则并退出引擎线程（UI 关闭时调用）。"""
        if self._loop is None:
            return
        try:
            future = asyncio.run_coroutine_threadsafe(self._shutdown_all(), self._loop)
            future.result(timeout=timeout)
        except Exception as exc:  # noqa: BLE001 - 退出路径尽力而为
            logger.warning("shutdown runners error: %s", exc)
        finally:
            self._loop.call_soon_threadsafe(self._loop.stop)
            if self._thread is not None:
                self._thread.join(timeout=2.0)
            self._loop.close()
            self._loop = None
            self._thread = None

    async def _shutdown_all(self) -> None:
        # 退出路径用 kill 快速清场，避免优雅宽限拖慢关窗
        for runner in list(self._runners.values()):
            try:
                await runner.stop(kill=True)
            except Exception:  # noqa: BLE001
                pass

    # ==================== UI 只读快照 ====================

    def snapshot(self, keyword: str = "") -> list[RuleView]:
        """返回按稳定顺序排列的规则快照（可按地址或备注关键词过滤）。"""
        text = keyword.strip().lower()
        with self._lock:
            rules = store.sort_rules(list(self._rules.values()))
            items = [(rule, self._runners.get(rule.id)) for rule in rules]
        views: list[RuleView] = []
        for rule, runner in items:
            if text:
                haystack = (
                    f"{rule.listen_addr} {rule.target_addr} {rule.remark}".lower()
                )
                if text not in haystack:
                    continue
            if runner is not None:
                status, last_error, connections = runner.snapshot()
            else:
                status, last_error, connections = RuntimeStatus.STOPPED, "", 0
            views.append(
                RuleView(
                    rule=rule,
                    status=status,
                    last_error=last_error,
                    active_connections=connections,
                )
            )
        return views

    # ==================== 变更提交入口 ====================

    def submit(self, coro) -> Future:
        """把变更协程提交到引擎线程执行，返回 concurrent Future。"""
        if self._loop is None:
            raise EngineError("引擎未启动")
        return asyncio.run_coroutine_threadsafe(coro, self._loop)

    # ---- 便捷封装：全部返回 Future，UI 用 add_done_callback 处理结果 ----

    def create_rule(
        self, listen_addr: str, target_addr: str, autostart: bool = True, remark: str = ""
    ) -> Future:
        """新增规则；返回 Future[(Rule, warning)]，autostart 失败时 warning 非空。"""
        return self.submit(self._create_rule(listen_addr, target_addr, autostart, remark))

    def update_rule(
        self, rule_id: str, listen_addr: str, target_addr: str, remark: str = ""
    ) -> Future:
        return self.submit(self._update_rule(rule_id, listen_addr, target_addr, remark))

    def delete_rules(self, rule_ids: list[str]) -> Future:
        """批量删除；返回 Future[list[tuple[rule_id, error]]]，error 为 "" 表示成功。"""
        return self.submit(self._delete_rules(list(rule_ids)))

    def start_rules(self, rule_ids: list[str]) -> Future:
        """批量启动；返回 Future[list[tuple[rule_id, error]]]。"""
        return self.submit(self._start_rules(list(rule_ids)))

    def stop_rules(self, rule_ids: list[str], kill: bool = False) -> Future:
        """批量停止；返回 Future[list[tuple[rule_id, error]]]。"""
        return self.submit(self._stop_rules(list(rule_ids), kill))

    # ==================== 业务协程（仅引擎线程执行） ====================

    async def _create_rule(
        self, listen_addr: str, target_addr: str, autostart: bool, remark: str = ""
    ):
        validate_address(listen_addr)
        validate_address(target_addr)

        self._ensure_listen_free(listen_addr, exclude_id="")
        now = utc_now()
        rule = Rule(
            id=uuid.uuid4().hex[:12],
            listen_addr=listen_addr.strip(),
            target_addr=target_addr.strip(),
            created_at=now,
            updated_at=now,
            remark=remark.strip(),
        )
        with self._lock:
            self._rules[rule.id] = rule
        try:
            self._save()
        except store.StoreError:
            with self._lock:
                del self._rules[rule.id]
            raise

        warning = ""
        if autostart:
            try:
                await self._start_rule(rule.id)
            except EngineError as exc:
                warning = f"规则已创建，但启动失败：{exc}"
        return (rule, warning)

    async def _update_rule(
        self, rule_id: str, listen_addr: str, target_addr: str, remark: str = ""
    ) -> None:
        validate_address(listen_addr)
        validate_address(target_addr)
        listen_addr = listen_addr.strip()
        target_addr = target_addr.strip()
        remark = remark.strip()

        with self._lock:
            old = self._rules.get(rule_id)
        if old is None:
            raise EngineError("规则不存在")
        self._ensure_listen_free(listen_addr, exclude_id=rule_id)

        listen_changed = old.listen_addr.lower() != listen_addr.lower()
        target_changed = old.target_addr != target_addr
        runner = self._runner_of(rule_id)
        # snapshot() 返回 (状态, 最后错误, 活跃连接数)
        status, _, _ = runner.snapshot() if runner else (RuntimeStatus.STOPPED, "", 0)
        if listen_changed and status is RuntimeStatus.RUNNING:
            raise EngineError("规则运行中，请先停止后再修改监听地址")

        updated = Rule(
            id=old.id,
            listen_addr=listen_addr,
            target_addr=target_addr,
            created_at=old.created_at,
            updated_at=utc_now(),
            remark=remark,
        )
        with self._lock:
            self._rules[rule_id] = updated
        try:
            self._save()
        except store.StoreError:
            with self._lock:
                self._rules[rule_id] = old
            raise

        if target_changed and runner is not None and status is RuntimeStatus.RUNNING:
            await runner.retarget(target_addr)

    async def _delete_rules(self, rule_ids: list[str]) -> list[tuple[str, str]]:
        results: list[tuple[str, str]] = []
        for rule_id in rule_ids:
            try:
                await self._delete_one(rule_id)
                results.append((rule_id, ""))
            except (EngineError, store.StoreError) as exc:
                results.append((rule_id, str(exc)))
        return results

    async def _delete_one(self, rule_id: str) -> None:
        with self._lock:
            rule = self._rules.get(rule_id)
        if rule is None:
            raise EngineError("规则不存在")
        runner = self._runner_of(rule_id)
        if runner is not None:
            await runner.stop(kill=False)
        with self._lock:
            self._rules.pop(rule_id, None)
            self._runners.pop(rule_id, None)
        self._save()

    async def _start_rules(self, rule_ids: list[str]) -> list[tuple[str, str]]:
        results: list[tuple[str, str]] = []
        for rule_id in rule_ids:
            try:
                await self._start_rule(rule_id)
                results.append((rule_id, ""))
            except (EngineError, store.StoreError) as exc:
                results.append((rule_id, str(exc)))
        return results

    async def _start_rule(self, rule_id: str) -> None:
        with self._lock:
            rule = self._rules.get(rule_id)
        if rule is None:
            raise EngineError("规则不存在")
        self._ensure_listen_not_running(rule.listen_addr, exclude_id=rule_id)

        runner = self._runner_of(rule_id)
        if runner is None:
            runner = RuleRunner(rule)
            with self._lock:
                self._runners[rule_id] = runner
        elif runner.listen_addr != rule.listen_addr:
            runner.listen_addr = rule.listen_addr
        try:
            await runner.start()
        except EngineError:
            raise
        return None

    async def _stop_rules(self, rule_ids: list[str], kill: bool) -> list[tuple[str, str]]:
        results: list[tuple[str, str]] = []
        for rule_id in rule_ids:
            runner = self._runner_of(rule_id)
            if runner is None:
                results.append((rule_id, ""))
                continue
            try:
                await runner.stop(kill=kill)
                results.append((rule_id, ""))
            except Exception as exc:  # noqa: BLE001
                results.append((rule_id, str(exc)))
        return results

    # ==================== 内部工具 ====================

    def _runner_of(self, rule_id: str) -> RuleRunner | None:
        with self._lock:
            return self._runners.get(rule_id)

    def _ensure_listen_free(self, listen_addr: str, exclude_id: str) -> None:
        """同监听地址（忽略大小写）不允许存在两条规则。"""
        with self._lock:
            for rid, rule in self._rules.items():
                if rid != exclude_id and rule.listen_addr.lower() == listen_addr.lower():
                    raise EngineError(f"监听地址已被其他规则使用: {listen_addr}")

    def _ensure_listen_not_running(self, listen_addr: str, exclude_id: str) -> None:
        """同监听地址不允许两条规则同时运行。"""
        with self._lock:
            pairs = [
                (rid, rule, self._runners.get(rid))
                for rid, rule in self._rules.items()
                if rid != exclude_id and rule.listen_addr.lower() == listen_addr.lower()
            ]
        for _, _, runner in pairs:
            if runner is None:
                continue
            # snapshot() 返回 (状态, 最后错误, 活跃连接数)
            status, _, _ = runner.snapshot()
            if status is RuntimeStatus.RUNNING:
                raise EngineError(f"监听地址正在被其他运行中的规则使用: {listen_addr}")

    def _save(self) -> None:
        """落盘（引擎线程内同步执行，文件小、频率低）。"""
        with self._lock:
            rules = list(self._rules.values())
        store.save_rules(self._path, rules)
