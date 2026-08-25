"""Tkinter 图形界面：主窗口与新增/编辑对话框。

线程约定：
- 所有控件操作只在 Tk 主线程进行；
- 引擎返回的 concurrent Future 通过 add_done_callback + root.after(0) 切回主线程。

@author ai-lhg
"""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from portproxy.engine import Engine, EngineError
from portproxy.model import RuleView, RuntimeStatus
from portproxy.validators import ValidationError, validate_address


class RuleDialog(tk.Toplevel):
    """新增/编辑规则的模态对话框；关闭后 result 为 (listen, target) 或 None。"""

    def __init__(
        self,
        master,
        title: str,
        initial_listen: str = "",
        initial_target: str = "",
        initial_remark: str = "",
    ) -> None:
        super().__init__(master)
        self.title(title)
        self.resizable(False, False)
        self.result: tuple[str, str, str] | None = None

        self.listen_var = tk.StringVar(value=initial_listen)
        self.target_var = tk.StringVar(value=initial_target)
        self.remark_var = tk.StringVar(value=initial_remark)

        frame = ttk.Frame(self, padding=14)
        frame.pack(fill="both", expand=True)

        ttk.Label(frame, text="监听地址").grid(row=0, column=0, sticky="w", pady=4)
        listen_entry = ttk.Entry(frame, textvariable=self.listen_var, width=34)
        listen_entry.grid(row=0, column=1, pady=4, sticky="w")
        ttk.Label(frame, text="目标地址").grid(row=1, column=0, sticky="w", pady=4)
        target_entry = ttk.Entry(frame, textvariable=self.target_var, width=34)
        target_entry.grid(row=1, column=1, pady=4, sticky="w")
        ttk.Label(frame, text="备注").grid(row=2, column=0, sticky="w", pady=4)
        ttk.Entry(frame, textvariable=self.remark_var, width=34).grid(
            row=2, column=1, pady=4, sticky="w"
        )

        hint = ttk.Label(frame, text="", foreground="#dc2626")
        hint.grid(row=3, column=0, columnspan=2, sticky="w")
        ttk.Label(
            frame,
            text="格式: host:port，如 0.0.0.0:8080",
            foreground="#6b7280",
        ).grid(row=4, column=0, columnspan=2, sticky="w")

        buttons = ttk.Frame(frame)
        buttons.grid(row=5, column=0, columnspan=2, sticky="e", pady=(10, 0))
        self._save_btn = ttk.Button(buttons, text="保存", command=self._on_save, state="disabled")
        self._save_btn.pack(side="left", padx=4)
        ttk.Button(buttons, text="取消", command=self._on_cancel).pack(side="left")

        # 即时校验：输入变化即检查合法性，非法则禁用保存键
        self.listen_var.trace_add("write", lambda *_: self._validate(hint))
        self.target_var.trace_add("write", lambda *_: self._validate(hint))
        self._validate(hint)

        self.bind("<Return>", self._on_return)
        self.bind("<Escape>", lambda _e: self._on_cancel())
        self.protocol("WM_DELETE_WINDOW", self._on_cancel)

        self.transient(master)
        self.grab_set()
        listen_entry.focus_set()

    def _on_return(self, _event=None) -> None:
        if "disabled" not in self._save_btn.state():
            self._on_save()

    def _validate(self, hint_label: ttk.Label) -> None:
        fields = (("监听地址", self.listen_var.get()), ("目标地址", self.target_var.get()))
        for label, value in fields:
            try:
                validate_address(value)
            except ValidationError as exc:
                hint_label.config(text=f"{label}无效：{exc}")
                self._save_btn.state(["disabled"])
                return
        hint_label.config(text="")
        self._save_btn.state(["!disabled"])

    def _on_save(self) -> None:
        self.result = (
            self.listen_var.get().strip(),
            self.target_var.get().strip(),
            self.remark_var.get().strip()[:100],
        )
        self.destroy()

    def _on_cancel(self) -> None:
        self.result = None
        self.destroy()


class PortProxyApp:
    """主窗口：规则表格、过滤、批量操作与定时状态刷新。"""

    REFRESH_MS = 500
    COLUMNS = (
        # (列 id, 标题, 宽度, 锚点, 是否拉伸)
        ("id", "ID", 110, "w", False),
        ("remark", "备注", 130, "w", False),
        ("listen", "监听地址", 165, "w", False),
        ("target", "目标地址", 165, "w", False),
        ("status", "状态", 90, "center", False),
        ("conn", "连接数", 65, "center", False),
        ("updated", "更新时间", 145, "center", False),
        ("error", "最后错误", 220, "w", True),
    )
    STATUS_TEXT = {
        RuntimeStatus.RUNNING: "● 运行中",
        RuntimeStatus.STOPPED: "○ 已停止",
        RuntimeStatus.ERROR: "✕ 异常",
    }

    def __init__(self, root: tk.Tk, engine: Engine, tray=None) -> None:
        self.root = root
        self.engine = engine
        self.tray = tray  # TrayIcon 实例；None 时关闭窗口即退出
        self._minimize_notified = False
        self._row_cache: dict[str, tuple] = {}  # rule_id -> 上次渲染 values（diff 用）

        style = ttk.Style(root)
        if "vista" in style.theme_names():
            style.theme_use("vista")

        root.title("pyPortProxy — TCP 端口转发管理器")
        root.geometry("1080x600")
        root.minsize(860, 480)

        self._build_widgets()
        root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._schedule_poll()

    # ==================== 控件构建 ====================

    def _build_widgets(self) -> None:
        top = ttk.Frame(self.root, padding=(8, 8, 8, 2))
        top.pack(fill="x")
        ttk.Label(top, text="过滤:").pack(side="left")
        self.filter_var = tk.StringVar()
        self.filter_var.trace_add("write", lambda *_: self._refresh_now())
        ttk.Entry(top, textvariable=self.filter_var, width=28).pack(side="left", padx=(4, 8))
        ttk.Button(top, text="刷新", command=self._refresh_now).pack(side="left")

        tree_frame = ttk.Frame(self.root)
        tree_frame.pack(fill="both", expand=True, padx=8, pady=4)
        columns = [c[0] for c in self.COLUMNS]
        self.tree = ttk.Treeview(
            tree_frame, columns=columns, show="headings", selectmode="extended"
        )
        for cid, title, width, anchor, stretch in self.COLUMNS:
            self.tree.heading(cid, text=title)
            self.tree.column(cid, width=width, anchor=anchor, stretch=stretch)
        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        # 状态配色 tag
        self.tree.tag_configure("running", foreground="#16a34a")
        self.tree.tag_configure("stopped", foreground="#6b7280")
        self.tree.tag_configure("error", foreground="#dc2626")
        self.tree.bind("<Double-1>", lambda _e: self._edit_rule())

        buttons = ttk.Frame(self.root, padding=(8, 4))
        buttons.pack(fill="x")
        ttk.Button(buttons, text="全选", command=self._select_all).pack(side="left", padx=2)
        ttk.Button(
            buttons, text="取消全选", command=self._clear_selection
        ).pack(side="left", padx=2)
        ttk.Button(buttons, text="新增", command=self._add_rule).pack(side="left", padx=(18, 2))
        ttk.Button(buttons, text="编辑", command=self._edit_rule).pack(side="left", padx=2)
        ttk.Button(buttons, text="删除", command=self._delete_rules).pack(side="left", padx=2)
        ttk.Button(buttons, text="启动", command=self._start_rules).pack(side="left", padx=(18, 2))
        ttk.Button(buttons, text="停止", command=self._stop_rules).pack(side="left", padx=2)

        self.status_var = tk.StringVar(value="就绪")
        status_label = ttk.Label(
            self.root, textvariable=self.status_var, anchor="w", padding=(8, 4)
        )
        status_label.pack(fill="x")

    # ==================== 数据刷新 ====================

    def _schedule_poll(self) -> None:
        """定时轮询引擎快照并做 diff 渲染，顺带处理托盘事件。"""
        try:
            self._drain_tray_commands()
            self._refresh_now()
        finally:
            self.root.after(self.REFRESH_MS, self._schedule_poll)

    def _drain_tray_commands(self) -> None:
        """处理托盘线程投递的事件（show / quit）。"""
        if self.tray is None:
            return
        for command in self.tray.drain():
            if command == "show":
                self.show_window()
            elif command == "quit":
                self.really_quit()
                return

    def _refresh_now(self) -> None:
        views = self.engine.snapshot(self.filter_var.get())
        self._render(views)
        running = sum(1 for v in views if v.status is RuntimeStatus.RUNNING)
        load_note = f" · ⚠ {self.engine.load_error}" if self.engine.load_error else ""
        self.status_var.set(f"共 {len(views)} 条规则 · 运行中 {running} 条{load_note}")

    def _render(self, views: list[RuleView]) -> None:
        tree = self.tree
        new_ids = {v.rule.id for v in views}
        for iid in tree.get_children():
            if iid not in new_ids:
                tree.delete(iid)
                self._row_cache.pop(iid, None)

        for idx, view in enumerate(views):
            iid = view.rule.id
            values = (
                iid,
                view.rule.remark,
                view.rule.listen_addr,
                view.rule.target_addr,
                self.STATUS_TEXT.get(view.status, str(view.status)),
                view.active_connections,
                view.rule.updated_at.astimezone().strftime("%Y-%m-%d %H:%M:%S"),
                view.last_error,
            )
            tags = (str(view.status),)
            exists = tree.exists(iid)
            if self._row_cache.get(iid) != values or not exists:
                if exists:
                    tree.item(iid, values=values, tags=tags)
                else:
                    tree.insert("", idx, iid=iid, values=values, tags=tags)
                self._row_cache[iid] = values
            if exists and tree.index(iid) != idx:
                tree.move(iid, "", idx)

    # ==================== 用户操作 ====================

    def _selected_ids(self) -> list[str]:
        return list(self.tree.selection())

    def _select_all(self) -> None:
        self.tree.selection_set(self.tree.get_children())

    def _clear_selection(self) -> None:
        self.tree.selection_set([])

    def _add_rule(self) -> None:
        dlg = RuleDialog(self.root, "新增规则", initial_listen="0.0.0.0:8080")
        self.root.wait_window(dlg)
        if not dlg.result:
            return
        listen_addr, target_addr, remark = dlg.result
        self._attach(
            self.engine.create_rule(listen_addr, target_addr, autostart=True, remark=remark),
            on_done=self._after_create,
        )

    @staticmethod
    def _after_create(result) -> None:
        _rule, warning = result
        if warning:
            messagebox.showwarning("提示", warning)

    def _edit_rule(self, *_args) -> None:
        selected = self._selected_ids()
        if len(selected) != 1:
            messagebox.showinfo("提示", "请先选择一条规则（双击行可直接编辑）")
            return
        rid = selected[0]
        view = next((v for v in self.engine.snapshot() if v.rule.id == rid), None)
        if view is None:
            return
        dlg = RuleDialog(
            self.root,
            "编辑规则",
            initial_listen=view.rule.listen_addr,
            initial_target=view.rule.target_addr,
            initial_remark=view.rule.remark,
        )
        self.root.wait_window(dlg)
        if not dlg.result:
            return
        listen_addr, target_addr, remark = dlg.result
        self._attach(self.engine.update_rule(rid, listen_addr, target_addr, remark=remark))

    def _delete_rules(self) -> None:
        ids = self._selected_ids()
        if not ids:
            messagebox.showinfo("提示", "请先选择规则（支持多选）")
            return
        if not messagebox.askyesno("确认", f"确认删除选中的 {len(ids)} 条规则？"):
            return
        self._attach(self.engine.delete_rules(ids), on_done=self._batch_reporter("删除"))

    def _start_rules(self) -> None:
        ids = self._selected_ids()
        if not ids:
            messagebox.showinfo("提示", "请先选择规则（支持多选）")
            return
        self._attach(self.engine.start_rules(ids), on_done=self._batch_reporter("启动"))

    def _stop_rules(self) -> None:
        ids = self._selected_ids()
        if not ids:
            messagebox.showinfo("提示", "请先选择规则（支持多选）")
            return
        self._attach(self.engine.stop_rules(ids), on_done=self._batch_reporter("停止"))

    @staticmethod
    def _batch_reporter(action: str):
        """批量操作的失败聚合弹窗。"""

        def handler(results) -> None:
            failures = [(rid, err) for rid, err in results if err]
            if failures:
                detail = "\n".join(f"{rid}: {err}" for rid, err in failures)
                messagebox.showwarning("部分失败", f"{action}时部分规则失败：\n{detail}")

        return handler

    # ==================== Future 桥接 ====================

    def _attach(self, future, on_done=None) -> None:
        """把引擎 Future 的结果/异常安全调度回 Tk 主线程处理。"""

        def callback(fut):
            def deliver():
                try:
                    exc = fut.exception()
                    if exc is not None:
                        if isinstance(exc, EngineError):
                            messagebox.showerror("操作失败", str(exc))
                        else:
                            messagebox.showerror("错误", f"内部错误：{exc}")
                    elif on_done is not None:
                        on_done(fut.result())
                except Exception as inner:  # 兜底防回调炸掉 UI
                    messagebox.showerror("错误", f"内部错误：{inner}")

            self.root.after(0, deliver)

        future.add_done_callback(callback)

    # ==================== 关闭 / 托盘 ====================

    def _on_close(self) -> None:
        """点窗口关闭按钮：有托盘时隐藏到托盘，否则直接退出。"""
        if self.tray is None:
            self.really_quit()
            return
        self.root.withdraw()
        # 首次最小化时气泡提示一次，避免用户误以为程序已退出
        if not self._minimize_notified:
            self.tray.notify("pyPortProxy", "程序已最小化到系统托盘，点击图标可恢复窗口")
            self._minimize_notified = True

    def show_window(self) -> None:
        """从托盘恢复主窗口。"""
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def really_quit(self) -> None:
        """真正退出：停托盘 → 停引擎 → 销毁窗口。"""
        try:
            if self.tray is not None:
                self.tray.stop()
        finally:
            try:
                self.engine.shutdown(timeout=5.0)
            finally:
                self.root.destroy()
