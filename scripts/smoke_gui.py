"""GUI 冒烟测试：无人工干预地启动界面、建规则、校验渲染后自动退出。

@author ai-lhg
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import tkinter as tk  # noqa: E402


def main() -> int:
    from portproxy.engine import Engine
    from portproxy.ui import PortProxyApp

    config = Path(".tmp/smoke-gui-rules.json")
    if config.exists():
        config.unlink()
    config.parent.mkdir(parents=True, exist_ok=True)

    root = tk.Tk()
    engine = Engine(config)
    engine.start()

    app = PortProxyApp(root, engine)
    results: dict[str, object] = {}

    def step_create():
        # 通过引擎创建一条回环规则（127.0.0.1 高位端口自转发），带备注
        future = engine.create_rule(
            "127.0.0.1:58101", "127.0.0.1:58101", autostart=True, remark="冒烟备注"
        )
        app._attach(future, on_done=lambda _r: results.setdefault("created", True))

    def step_verify():
        rows = app.tree.get_children()
        results["rows"] = len(rows)
        if len(rows) >= 1:
            values = app.tree.item(rows[0], "values")
            print(f"[gui-smoke] row values = {values}")
            results["remark"] = values[1]  # 列序: id, remark, listen, target...
            status_text = values[4]
            results["status"] = status_text

    def step_finish():
        expect_created = results.get("created") is True
        rows_ok = results.get("rows", 0) >= 1
        status_ok = "运行中" in str(results.get("status", ""))
        remark_ok = results.get("remark") == "冒烟备注"
        print(
            f"[gui-smoke] created={expect_created} rows_ok={rows_ok} "
            f"status_ok={status_ok} remark_ok={remark_ok}"
        )
        ok = expect_created and rows_ok and status_ok and remark_ok
        print("GUI SMOKE PASS" if ok else "GUI SMOKE FAIL")
        root.destroy()
        smoke_result.append(ok)

    smoke_result: list[bool] = []
    root.after(800, step_create)
    root.after(2000, step_verify)
    root.after(2600, step_finish)
    root.mainloop()

    engine.shutdown(timeout=5.0)
    return 0 if smoke_result and smoke_result[0] else 1


if __name__ == "__main__":
    raise SystemExit(main())
