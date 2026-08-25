"""引擎最小冒烟脚本：脱离 pytest 直接验证 Engine 全链路。

@author ai-lhg
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from portproxy.engine import Engine  # noqa: E402


async def main() -> int:
    # 1. 起 echo server（本 loop）
    async def echo_cb(r, w):
        try:
            while True:
                data = await r.read(65536)
                if not data:
                    break
                w.write(data)
                await w.drain()
        finally:
            w.close()

    echo = await asyncio.start_server(echo_cb, "127.0.0.1", 0)
    echo_port = echo.sockets[0].getsockname()[1]

    # 2. 分配空闲监听端口
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        listen_port = s.getsockname()[1]

    # 3. 引擎创建规则并自启
    eng = Engine(Path(".tmp/smoke-rules.json"))
    eng.start()
    fut = eng.create_rule(f"127.0.0.1:{listen_port}", f"127.0.0.1:{echo_port}", autostart=True)
    rule, warning = await asyncio.wrap_future(fut)
    print(f"[1] rule={rule.id} warning={warning!r}")

    # 4. 经转发收发
    reader, writer = await asyncio.open_connection("127.0.0.1", listen_port)
    writer.write(b"hello-proxy")
    await writer.drain()
    got = await asyncio.wait_for(reader.readexactly(11), timeout=5)
    print(f"[2] roundtrip got={got!r}")
    assert got == b"hello-proxy", "echo mismatch"
    writer.close()

    # 5. 停止并确认端口释放
    results = await asyncio.wrap_future(eng.stop_rules([rule.id]))
    print(f"[3] stop results={results}")
    try:
        await asyncio.wait_for(asyncio.open_connection("127.0.0.1", listen_port), timeout=1.5)
        print("[4] FAIL: port still open")
        return 1
    except (ConnectionRefusedError, OSError):
        print("[4] port released OK")

    # 6. 关闭引擎
    eng.shutdown(timeout=5.0)
    print("[5] engine shutdown OK")

    echo.close()
    await echo.wait_closed()
    print("SMOKE PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
