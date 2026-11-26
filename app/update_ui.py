"""更新弹窗：检查结果、更新日志、下载进度与自更新入口。"""

from __future__ import annotations

import queue
import threading
import tkinter as tk
import tkinter.messagebox as messagebox
import webbrowser
from tkinter import ttk
from typing import TYPE_CHECKING

from . import __version__, updater
from .config import APP_DIR
from .icons import ensure_window_icon
from .theme import (
    ACCENT,
    ACCENT_HOVER,
    BG,
    BORDER,
    CARD,
    DANGER,
    FONT,
    HINT,
    INPUT,
    MUTED,
    SUCCESS,
    TEXT,
    WARN,
)
from . import ui_scale
from .ui_scale import px

if TYPE_CHECKING:
    from .ui import MainWindow


def _button(
    master: tk.Misc,
    text: str,
    command,
    *,
    primary: bool = False,
    danger: bool = False,
) -> tk.Button:
    if primary:
        bg, active, fg = ACCENT, ACCENT_HOVER, "#ffffff"
    elif danger:
        bg, active, fg = "#3a2328", "#4a2b31", DANGER
    else:
        bg, active, fg = "#2b303a", "#363c48", TEXT
    return tk.Button(
        master,
        text=text,
        command=command,
        bg=bg,
        fg=fg,
        activebackground=active,
        activeforeground=fg,
        relief="flat",
        bd=0,
        highlightthickness=0,
        padx=px(14),
        pady=px(6),
        font=(FONT, 9, "bold" if primary else "normal"),
        cursor="hand2",
    )


class UpdateDialog(tk.Toplevel):
    def __init__(self, master: "MainWindow") -> None:
        super().__init__(master.root)
        self.app = master
        self._queue: "queue.Queue[tuple]" = queue.Queue()
        self._poll_job: str | None = None
        self._closed = False
        self._downloading = False
        self._cancel = threading.Event()
        self._result: updater.CheckResult | None = None
        self._update_target: str = ""

        self.title("检查更新")
        self.configure(bg=BG)
        self.transient(master.root)
        self.resizable(False, True)
        try:
            self.iconbitmap(str(ensure_window_icon(APP_DIR)))
        except Exception:  # noqa: BLE001
            pass

        metrics = ui_scale.get_screen_metrics()
        width = min(px(520), max(px(360), metrics.work_w - px(32)))
        height = min(px(500), max(px(360), metrics.work_h - px(80)))
        self.geometry(f"{width}x{height}")
        self.minsize(width, min(height, px(360)))

        self._build()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(60, self._center)
        self.after(120, self._grab)
        self._poll_job = self.after(100, self._poll)

        self.show_checking()
        self.app.start_update_check(dialog=self)

    # ------------------------------------------------------------ 构建

    def _build(self) -> None:
        outer = tk.Frame(self, bg=BG)
        outer.pack(fill="both", expand=True, padx=px(16), pady=px(14))
        outer.grid_columnconfigure(1, weight=1)
        outer.grid_rowconfigure(4, weight=1)

        tk.Label(
            outer,
            text="检查更新",
            bg=BG,
            fg=TEXT,
            font=(FONT, 14, "bold"),
            anchor="w",
        ).grid(row=0, column=0, columnspan=2, sticky="w")

        self.current_label = tk.Label(
            outer,
            text=f"当前版本：v{__version__}",
            bg=BG,
            fg=MUTED,
            font=(FONT, 9),
            anchor="w",
        )
        self.current_label.grid(row=1, column=0, columnspan=2, sticky="w", pady=(px(4), px(10)))

        info = tk.Frame(outer, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
        info.grid(row=2, column=0, columnspan=2, sticky="ew")
        info.grid_columnconfigure(1, weight=1)
        fields = [
            ("最新版本", "latest_value"),
            ("更新源", "source_value"),
            ("校验", "sha_value"),
        ]
        self._value_labels: dict[str, tk.Label] = {}
        for index, (name, key) in enumerate(fields):
            tk.Label(
                info,
                text=name,
                bg=CARD,
                fg=HINT,
                font=(FONT, 8),
                anchor="w",
                width=8,
            ).grid(row=index, column=0, sticky="w", padx=(px(10), px(6)), pady=(px(5), px(3)))
            label = tk.Label(
                info,
                text="—",
                bg=CARD,
                fg=TEXT,
                font=(FONT, 9),
                anchor="w",
                justify="left",
            )
            label.grid(row=index, column=1, sticky="w", padx=(0, px(10)), pady=(px(5), px(3)))
            self._value_labels[key] = label

        change_head = tk.Frame(outer, bg=BG)
        change_head.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(px(12), px(4)))
        tk.Label(
            change_head,
            text="更新日志",
            bg=BG,
            fg=TEXT,
            font=(FONT, 9, "bold"),
            anchor="w",
        ).pack(side="left")
        self.release_link = tk.Label(
            change_head,
            text="打开发布页",
            bg=BG,
            fg=HINT,
            font=(FONT, 8),
            cursor="hand2",
        )
        self.release_link.pack(side="right")
        self.release_link.bind("<Button-1>", lambda _event: self._open_release_page())
        self.release_link.bind(
            "<Enter>", lambda _event: self.release_link.configure(fg=TEXT)
        )
        self.release_link.bind(
            "<Leave>", lambda _event: self.release_link.configure(fg=HINT)
        )

        log_wrap = tk.Frame(outer, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
        log_wrap.grid(row=4, column=0, columnspan=2, sticky="nsew")
        log_wrap.grid_rowconfigure(0, weight=1)
        log_wrap.grid_columnconfigure(0, weight=1)
        self.log_text = tk.Text(
            log_wrap,
            height=9,
            wrap="word",
            bg="#101216",
            fg=MUTED,
            insertbackground=TEXT,
            selectbackground=ACCENT,
            relief="flat",
            bd=0,
            padx=px(9),
            pady=px(7),
            font=(FONT, 9),
            state="disabled",
        )
        self.log_text.grid(row=0, column=0, sticky="nsew")
        log_scroll = ttk.Scrollbar(
            log_wrap,
            orient="vertical",
            command=self.log_text.yview,
            style="Dark.Vertical.TScrollbar",
        )
        log_scroll.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=log_scroll.set)

        self.status_label = tk.Label(
            outer,
            text="",
            bg=BG,
            fg=MUTED,
            font=(FONT, 8),
            anchor="w",
            justify="left",
            wraplength=px(480),
        )
        self.status_label.grid(
            row=5, column=0, columnspan=2, sticky="w", pady=(px(8), px(6))
        )

        self.progress = ttk.Progressbar(
            outer,
            mode="determinate",
            maximum=100,
            style="Update.Horizontal.TProgressbar",
        )

        buttons = tk.Frame(outer, bg=BG)
        buttons.grid(row=7, column=0, columnspan=2, sticky="ew", pady=(px(4), 0))
        buttons.grid_columnconfigure(0, weight=1)
        self.update_btn = _button(buttons, "更新", self._on_update_click, primary=True)
        self.update_btn.grid(row=0, column=1, padx=(0, px(8)))
        self.cancel_btn = _button(buttons, "取消", self._on_close)
        self.cancel_btn.grid(row=0, column=2)

        style = ttk.Style(self)
        try:
            style.configure(
                "Update.Horizontal.TProgressbar",
                troughcolor=INPUT,
                background=ACCENT,
                bordercolor=BORDER,
                lightcolor=ACCENT,
                darkcolor=ACCENT,
            )
            style.configure(
                "Dark.Vertical.TScrollbar",
                background=CARD,
                troughcolor=INPUT,
                bordercolor=BORDER,
                arrowcolor=MUTED,
                darkcolor=CARD,
                lightcolor=CARD,
                arrowsize=px(12),
            )
            style.map(
                "Dark.Vertical.TScrollbar",
                background=[("active", MUTED)],
                arrowcolor=[("active", TEXT)],
            )
        except tk.TclError:
            pass

    # ------------------------------------------------------------ 状态

    def show_checking(self) -> None:
        self._update_target = ""
        self._value_labels["latest_value"].configure(text="正在检查…", fg=MUTED)
        self._value_labels["source_value"].configure(text="GitHub（官方）", fg=MUTED)
        self._value_labels["sha_value"].configure(text="—", fg=MUTED)
        self._set_log("正在从 GitHub 检查最新正式版本…")
        self.status_label.configure(text="正在检查更新…", fg=MUTED)
        self.update_btn.configure(text="检查中…", state="disabled", bg="#2b303a")
        self.cancel_btn.configure(text="取消")
        self.progress.grid_forget()

    def show_result(self, result: updater.CheckResult) -> None:
        if self._closed:
            return
        self._result = result
        if result.error:
            self._value_labels["latest_value"].configure(text="检查失败", fg=DANGER)
            self._value_labels["source_value"].configure(text="GitHub（官方）", fg=MUTED)
            self._value_labels["sha_value"].configure(text="—", fg=MUTED)
            self._set_log(result.error)
            self.status_label.configure(text=f"检查更新失败：{result.error}", fg=DANGER)
            self.update_btn.configure(text="关闭", state="normal", command=self._on_close)
            self.cancel_btn.configure(text="关闭")
            return

        latest = result.latest
        if latest is None:
            self.show_result(
                updater.CheckResult(
                    current_version=result.current_version, error="没有获取到版本信息"
                )
            )
            return

        self.release_link.configure(text="打开发布页")
        self._value_labels["latest_value"].configure(
            text=f"{latest.name}（{latest.tag}）", fg=TEXT
        )
        sources = "GitHub（官方）"
        if updater.MIRROR_URL_TEMPLATES:
            sources = "GitHub / 镜像（自动测速）"
        self._value_labels["source_value"].configure(text=sources, fg=MUTED)

        if latest.expected_sha256:
            self._value_labels["sha_value"].configure(
                text=f"已获取 SHA256：{latest.expected_sha256[:12]}…", fg=SUCCESS
            )
        else:
            self._value_labels["sha_value"].configure(
                text="该版本未提供 SHA256，无法自动校验", fg=WARN
            )

        changelog = latest.body.strip() or "（该版本没有提供更新日志）"
        if len(changelog) > 12000:
            changelog = changelog[:12000] + "\n…（日志过长，已截断）"
        self._set_log(changelog)

        if not result.has_update:
            self.status_label.configure(text="当前版本已是最新。", fg=SUCCESS)
            self.update_btn.configure(
                text="已是最新", state="disabled", bg="#2b303a", command=self._on_close
            )
            self.cancel_btn.configure(text="关闭")
            self.progress.grid_forget()
            return

        if not latest.asset:
            self.status_label.configure(
                text="发现新版本，但该 Release 没有可下载的 Windows 程序，请打开发布页手动下载。",
                fg=WARN,
            )
            self.update_btn.configure(
                text="打开发布页", state="normal", bg="#2b303a", command=self._open_release_page
            )
            self.cancel_btn.configure(text="关闭")
            return

        if not latest.expected_sha256:
            self.status_label.configure(
                text="发现新版本，但缺少 SHA256，出于安全考虑不自动更新，请打开发布页手动下载。",
                fg=WARN,
            )
            self.update_btn.configure(
                text="打开发布页", state="normal", bg="#2b303a", command=self._open_release_page
            )
            self.cancel_btn.configure(text="关闭")
            return

        if not updater.is_frozen():
            self.status_label.configure(
                text="当前为源码运行模式，自动替换不可用；可打开发布页手动下载新版本。",
                fg=WARN,
            )
            self.update_btn.configure(
                text="打开发布页", state="normal", bg="#2b303a", command=self._open_release_page
            )
            self.cancel_btn.configure(text="关闭")
            return

        self.status_label.configure(
            text=f"发现新版本 {latest.tag}，可点击「更新」下载并自动替换。", fg=WARN
        )
        self.update_btn.configure(
            text="更新", state="normal", bg=ACCENT, command=self._on_update_click
        )
        self.cancel_btn.configure(text="取消")

    def _set_log(self, text: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.insert("1.0", text)
        self.log_text.configure(state="disabled")

    # ------------------------------------------------------------ 动作

    def _on_update_click(self) -> None:
        if self._downloading or self._result is None:
            return
        latest = self._result.latest
        if latest is None or not self._result.has_update:
            return
        if not updater.is_frozen() or not latest.asset or not latest.expected_sha256:
            self._open_release_page()
            return
        self._start_download(latest)

    def _start_download(self, release: updater.ReleaseInfo) -> None:
        self._downloading = True
        self._cancel.clear()
        self.update_btn.configure(state="disabled", text="下载中…")
        self.cancel_btn.configure(text="取消下载")
        self.status_label.configure(text="正在下载更新…", fg=MUTED)
        self.progress.configure(value=0)
        self.progress.grid(row=6, column=0, columnspan=2, sticky="ew", pady=(0, px(6)))

        def worker() -> None:
            try:
                path = updater.download_update(
                    release,
                    progress=lambda done, total: self._queue.put(("progress", done, total)),
                    cancel=self._cancel,
                )
                self._queue.put(("download_done", path))
            except updater.UpdateCancelled:
                self._queue.put(("cancelled",))
            except Exception as exc:  # noqa: BLE001
                self._queue.put(("download_error", str(exc)))

        threading.Thread(target=worker, name="update-download", daemon=True).start()

    def _on_download_done(self, path) -> None:
        self._downloading = False
        self.progress.configure(value=100)
        self.status_label.configure(text="下载完成并通过校验，正在重启更新…", fg=SUCCESS)
        try:
            updater.launch_apply_update(path)
        except Exception as exc:  # noqa: BLE001
            self._reset_after_download()
            self.status_label.configure(text=f"无法启动自动更新：{exc}", fg=DANGER)
            messagebox.showerror("自动更新失败", f"{exc}\n\n请打开发布页手动下载。", parent=self)
            return
        self.app.shutdown()

    def _reset_after_download(self) -> None:
        self._downloading = False
        self.progress.grid_forget()
        if self._result is not None and self._result.has_update:
            self.update_btn.configure(text="更新", state="normal", bg=ACCENT)
            self.cancel_btn.configure(text="取消")

    def _on_cancelled(self) -> None:
        self._downloading = False
        self.progress.grid_forget()
        self.status_label.configure(text="已取消下载。", fg=MUTED)
        if self._result is not None and self._result.has_update:
            self.update_btn.configure(text="更新", state="normal", bg=ACCENT)
            self.cancel_btn.configure(text="取消")

    def _on_download_error(self, message: str) -> None:
        self._downloading = False
        self.progress.grid_forget()
        self.status_label.configure(text=f"下载失败：{message}", fg=DANGER)
        if self._result is not None and self._result.has_update:
            self.update_btn.configure(text="重试更新", state="normal", bg=ACCENT)
            self.cancel_btn.configure(text="取消")

    def _open_release_page(self) -> None:
        url = updater.release_page_url()
        if self._result is not None and self._result.latest is not None:
            url = self._result.latest.html_url or url
        webbrowser.open(url)

    # ------------------------------------------------------------ 生命周期

    def _grab(self) -> None:
        if self._closed:
            return
        try:
            self.grab_set()
            self.focus_force()
        except tk.TclError:
            pass

    def _center(self) -> None:
        if self._closed:
            return
        try:
            master = self.master
            master.update_idletasks()
            width = self.winfo_width()
            height = self.winfo_height()
            x = master.winfo_rootx() + max(0, (master.winfo_width() - width) // 2)
            y = master.winfo_rooty() + max(0, (master.winfo_height() - height) // 2)
            self.geometry(f"+{x}+{y}")
        except tk.TclError:
            pass

    def _poll(self) -> None:
        if self._closed:
            return
        try:
            while True:
                message = self._queue.get_nowait()
                kind = message[0]
                if kind == "progress":
                    _, done, total = message
                    if total:
                        percent = min(100, done * 100 / total)
                        self.progress.configure(value=percent)
                        self.status_label.configure(
                            text=(
                                f"正在下载更新… {percent:.0f}% "
                                f"（{done / 1024 / 1024:.1f} / {total / 1024 / 1024:.1f} MB）"
                            ),
                            fg=MUTED,
                        )
                    else:
                        self.status_label.configure(
                            text=f"正在下载更新… {done / 1024 / 1024:.1f} MB",
                            fg=MUTED,
                        )
                elif kind == "download_done":
                    self._on_download_done(message[1])
                elif kind == "cancelled":
                    self._on_cancelled()
                elif kind == "download_error":
                    self._on_download_error(str(message[1]))
        except queue.Empty:
            pass
        if not self._closed:
            self._poll_job = self.after(100, self._poll)

    def _on_close(self) -> None:
        if self._downloading:
            if not messagebox.askyesno(
                "取消更新", "正在下载更新，确定取消并关闭吗？", parent=self
            ):
                return
            self._cancel.set()
        self._closed = True
        if self._poll_job is not None:
            try:
                self.after_cancel(self._poll_job)
            except tk.TclError:
                pass
            self._poll_job = None
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.app.clear_update_dialog(self)
        try:
            self.destroy()
        except tk.TclError:
            pass