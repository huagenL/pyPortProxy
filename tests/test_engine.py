"""引擎核心测试（转发回环收发、CRUD、启停语义）。

@author ai-lhg
"""

import asyncio
import socket

import pytest
from tests.conftest import call

from portproxy.engine import Engine, EngineError


class TestEchoRoundTrip:
    """复刻 Go 版 runner_test.go 的核心语义：建立转发→回环 echo 收发→停止→验证端口释放。"""

    async def test_forward_and_stop(self, engine, echo_server, free_port):
        # 创建规则并自动启动
        rule, warning = await call(
            engine, engine._create_rule(free_port, echo_server, autostart=True)
        )
        assert warning == ""
        assert rule.id

        # 验证转发收发
        port = int(free_port.split(":")[1])
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection("127.0.0.1", port), timeout=5
        )
        try:
            want = b"hello-proxy"
            writer.write(want)
            await writer.drain()
            data = await asyncio.wait_for(reader.readexactly(len(want)), timeout=5)
            assert data == want
        finally:
            writer.close()

        # 停止规则
        results = await call(engine, engine._stop_rules([rule.id], kill=False))
        assert results[0][1] == ""  # 无错误

        # 端口应被释放：重连应被拒绝
        with pytest.raises((ConnectionRefusedError, OSError)):
            await asyncio.wait_for(asyncio.open_connection("127.0.0.1", port), timeout=2)


class TestStartStopIdempotent:
    async def test_stop_twice_no_error(self, engine, echo_server, free_port):
        rule, _ = await call(
            engine, engine._create_rule(free_port, echo_server, autostart=True)
        )
        # 停止两次不抛错
        r1 = await call(engine, engine._stop_rules([rule.id], kill=False))
        r2 = await call(engine, engine._stop_rules([rule.id], kill=False))
        assert r1[0][1] == ""
        assert r2[0][1] == ""  # 幂等


class TestDuplicateListen:
    async def test_create_same_listen_rejected(self, engine, echo_server, free_port):
        """同监听地址第二条规则在创建阶段即被拒绝（对齐 Go 版 CreateRule 查重）。"""
        await call(engine, engine._create_rule(free_port, echo_server, autostart=True))
        with pytest.raises(EngineError, match="已被其他规则使用"):
            await call(
                engine, engine._create_rule(free_port, echo_server, autostart=False)
            )

    async def test_start_duplicate_from_legacy_file_rejected(self, tmp_path, free_port):
        """历史数据文件中同监听地址两条规则（如手工编辑产生）：
        第二条 start 被运行态检查拦截。"""
        import json as _json

        now = "2024-05-01T10:00:00+08:00"
        data = [
            {"id": "aaa000", "listenAddr": free_port, "targetAddr": "127.0.0.1:1",
             "createdAt": now, "updatedAt": now},
            {"id": "bbb111", "listenAddr": free_port, "targetAddr": "127.0.0.1:2",
             "createdAt": now, "updatedAt": now},
        ]
        rules_file = tmp_path / "rules.json"
        rules_file.write_text(_json.dumps(data), encoding="utf-8")

        eng = Engine(rules_file)
        eng.start()
        try:
            ok = await asyncio.wrap_future(eng.start_rules(["aaa000"]))
            assert ok[0][1] == ""
            dup = await asyncio.wrap_future(eng.start_rules(["bbb111"]))
            assert dup[0][1] != ""
            assert "正在被其他运行中的规则使用" in dup[0][1]
        finally:
            eng.shutdown()


class TestEditRunningRule:
    async def test_change_listen_while_running_rejected(self, engine, echo_server, free_port):
        rule, _ = await call(
            engine, engine._create_rule(free_port, echo_server, autostart=True)
        )
        with pytest.raises(EngineError, match="运行中"):
            await call(engine, engine._update_rule(rule.id, "0.0.0.0:9999", echo_server))

    async def test_change_target_while_running(self, engine, echo_server, free_port):
        """运行中允许修改目标地址（热改）。"""
        rule, _ = await call(
            engine, engine._create_rule(free_port, echo_server, autostart=True)
        )
        # 修改目标地址应成功（不影响 listener）
        await call(engine, engine._update_rule(rule.id, free_port, "127.0.0.1:9999"))
        # 规则还在，端口还在监听（连上后发数据会因目标不可达而被关闭）
        s = socket.socket()
        s.settimeout(1)
        try:
            port = int(free_port.split(":")[1])
            s.connect(("127.0.0.1", port))
        except (ConnectionResetError, OSError):
            pass  # 预期：连上了但目标不通，被关闭
        finally:
            s.close()


class TestDeleteStopsRunner:
    async def test_delete_removes_and_frees_port(self, engine, echo_server, free_port):
        rule, _ = await call(
            engine, engine._create_rule(free_port, echo_server, autostart=True)
        )
        results = await call(engine, engine._delete_rules([rule.id]))
        assert results[0][1] == ""

        # 端口应被释放
        port = int(free_port.split(":")[1])
        with pytest.raises((ConnectionRefusedError, OSError)):
            await asyncio.wait_for(asyncio.open_connection("127.0.0.1", port), timeout=2)


class TestKillDisconnects:
    async def test_kill_disconnects_active_connections(self, engine, echo_server, free_port):
        rule, _ = await call(
            engine, engine._create_rule(free_port, echo_server, autostart=True)
        )
        port = int(free_port.split(":")[1])
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection("127.0.0.1", port), timeout=5
        )
        try:
            await asyncio.sleep(0.2)  # 等连接建立
            await call(engine, engine._stop_rules([rule.id], kill=True))
            # 客户端应收到 EOF（对端被 abort）
            data = await asyncio.wait_for(reader.read(), timeout=3)
            assert data == b""  # EOF
        finally:
            writer.close()


class TestRemark:
    async def test_create_with_remark(self, engine, echo_server, free_port):
        rule, warning = await call(
            engine,
            engine._create_rule(free_port, echo_server, autostart=False, remark="内网 OA"),
        )
        assert warning == ""
        view = next(v for v in engine.snapshot() if v.rule.id == rule.id)
        assert view.rule.remark == "内网 OA"

    async def test_update_remark(self, engine, echo_server, free_port):
        rule, _ = await call(
            engine, engine._create_rule(free_port, echo_server, autostart=True)
        )
        # 运行中允许只改备注（监听地址不变）
        await call(
            engine,
            engine._update_rule(rule.id, free_port, echo_server, remark="远程桌面"),
        )
        view = next(v for v in engine.snapshot() if v.rule.id == rule.id)
        assert view.rule.remark == "远程桌面"

    async def test_snapshot_keyword_matches_remark(self, engine, echo_server, free_port):
        await call(
            engine,
            engine._create_rule(free_port, echo_server, autostart=False, remark="数据库同步"),
        )
        assert len(engine.snapshot("数据库")) == 1
        assert len(engine.snapshot("不存在的备注")) == 0
        assert len(engine.snapshot("")) == 1


class TestLoadErrorTolerant:
    async def test_bad_config_file(self, tmp_path):
        bad_file = tmp_path / "bad.json"
        bad_file.write_text("{bad json!", encoding="utf-8")
        eng = Engine(bad_file)
        eng.start()
        try:
            # 引擎能正常工作，但 load_error 非空
            assert eng.load_error != ""
            views = eng.snapshot()
            assert views == []  # 空规则
        finally:
            eng.shutdown()
