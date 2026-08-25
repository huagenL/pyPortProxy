"""共享测试 fixtures（echo server、引擎、跨 loop await 辅助、自定义 tmp_path）。

@author ai-lhg
"""

from __future__ import annotations

import asyncio
import itertools
import os
import socket
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest

from portproxy.engine import Engine

# 计数器保证同进程内多次用例的临时目录互不重叠
_TMP_COUNTER = itertools.count()


@pytest.fixture
def tmp_path() -> Path:
    """接管内置 tmp_path：普通 mkdir 实现。

    内置实现基于 tempfile.mkdtemp，其在 Windows 上设置的 owner-only ACL
    会导致目录在受限环境下不可清理；这里改用普通目录且不做强制删除。

    @author ai-lhg
    """
    base = Path(__file__).resolve().parent.parent / ".tmp" / "runs"
    target = base / f"t{next(_TMP_COUNTER):04d}-{os.getpid()}"
    target.mkdir(parents=True, exist_ok=True)
    return target


async def _echo_cb(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """回环 echo 回调：收到什么原样返回。"""
    try:
        while True:
            data = await reader.read(64 * 1024)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except (ConnectionError, asyncio.CancelledError):
        pass
    finally:
        writer.close()


@pytest.fixture
async def echo_server() -> AsyncGenerator[str, None]:
    """启动随机端口回环 echo server，yield 地址字符串如 ``127.0.0.1:54321``。"""
    server = await asyncio.start_server(_echo_cb, "127.0.0.1", 0)
    addr = server.sockets[0].getsockname()
    addr_str = f"{addr[0]}:{addr[1]}"
    yield addr_str
    server.close()
    await server.wait_closed()


@pytest.fixture
def free_port() -> str:
    """分配一个空闲端口，关闭后返回地址字符串供测试 Runner 使用。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    return f"127.0.0.1:{port}"


@pytest.fixture
def engine(tmp_path) -> Engine:
    """创建并启动临时 Engine 实例，测试结束自动 shutdown。"""
    eng = Engine(tmp_path / "rules.json")
    eng.start()
    yield eng
    eng.shutdown(timeout=5.0)


async def call(engine: Engine, coro) -> object:
    """把引擎协程提交后 await 结果（跨 loop bridge）。

    @author ai-lhg
    """
    future = engine.submit(coro)
    return await asyncio.wrap_future(future)
