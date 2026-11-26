"""Tkinter 主界面（多账户版）。"""

from __future__ import annotations

import ctypes
import logging
import queue
import threading
import time
import tkinter as tk
import tkinter.messagebox as messagebox
import webbrowser
from tkinter import ttk

from PIL import Image, ImageTk

from . import APP_TITLE, __version__, ui_scale, updater
from .config import APP_DIR, AppConfig, Profile, is_autostart_enabled, set_autostart
from .controller import Controller
from .icons import draw_app_icon, ensure_window_icon
from .logging_setup import log_success
from .network import check_connectivity, list_wifi_networks
from .theme import (
    ACCENT,
    ACCENT_HOVER,
    BG,
    BORDER,
    CARD,
    DANGER,
    FONT,
    GRAY,
    HINT,
    INPUT,
    MONO,
    MUTED,
    STATE_COLORS,
    SUCCESS,
    TEXT,
    WARN,
)
from .tray import TrayIcon
from .update_ui import UpdateDialog

log = logging.getLogger("campus")


class Card(tk.Frame):
    def __init__(self, master: tk.Misc, **kwargs: object) -> None:
        super().__init__(
            master,
            bg=CARD,
            bd=0,
            highlightbackground=BORDER,
            highlightthickness=1,
            **kwargs,
        )
        self.inner = tk.Frame(self, bg=CARD)
        self.inner.pack(fill="both", expand=True, padx=12, pady=8)


class MainWindow:
    def __init__(
        self,
        config: AppConfig,
        events: "queue.Queue[dict]",
        start_hidden: bool = False,
        use_tray: bool = True,
        exit_after: float = 0.0,
    ) -> None:
        self.config = config
        self.profile: Profile | None = config.active_profile()
        self.events = events
        self.use_tray = use_tray
        self._closing = False
        self._hide_notified = False
        self._last_state = "init"
        self._current_state = "init"
        self._poll_job: str | None = None
        self._probe_busy = False
        self._wifi_busy = False
        self.tray: TrayIcon | None = None
        self._update_check_busy = False
        self._update_result: updater.CheckResult | None = None
        self._update_dialog: UpdateDialog | None = None
        self._scroll_visible = False
        self._content_height = 0
        self._metrics = ui_scale.get_screen_metrics()
        if config.ui_scale_mode == "manual" and config.ui_scale <= 0:
            config.ui_scale_mode = "auto"
        self._ui_scale = ui_scale.resolve_scale(
            config.ui_scale_mode, config.ui_scale, self._metrics
        )

        self.root = tk.Tk()
        self.root.title(APP_TITLE)
        self.root.configure(bg=BG)
        self.root.resizable(False, True)
        self._setup_scaling()
        self._place_window()
        self._setup_icon()
        self._setup_ttk_styles()

        profile = self.profile
        self.var_profile_select = tk.StringVar(value=profile.name if profile else "")
        self.var_profile_name = tk.StringVar(value=profile.name if profile else "")
        self.var_user = tk.StringVar(value=profile.username if profile else "")
        self.var_pwd = tk.StringVar(value=profile.get_password() if profile else "")
        self.var_ssid = tk.StringVar(value=profile.ssid if profile else "")
        self.var_portal_url = tk.StringVar(value=profile.portal_url if profile else "")
        self.var_auto = tk.BooleanVar(value=config.auto_login)
        self.var_startup = tk.BooleanVar(value=is_autostart_enabled())
        self.var_tray = tk.BooleanVar(value=config.minimize_to_tray)
        self.var_show_pwd = tk.BooleanVar(value=False)
        self.var_interval = tk.StringVar(value=str(config.check_interval))
        self.var_ui_scale = tk.StringVar(value=self._scale_choice_label())

        self._build_ui()
        ui_scale.scale_widget_tree(self.root)
        self.root.update_idletasks()
        self._sync_scroll()

        self.controller: Controller | None = None
        if self.profile is not None:
            self.controller = Controller(
                config, self.profile, self.var_pwd.get(), events
            )
            self.controller.start()

        if self.use_tray:
            self.tray = TrayIcon(events)
            self.tray.start()
            self.tray.set_auto_login(config.auto_login)

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._poll_job = self.root.after(100, self._poll_events)
        self.root.update_idletasks()
        self._apply_dark_titlebar()
        self.root.after(400, self._apply_dark_titlebar)
        self.root.after(500, self._refresh_wifi)
        self.root.after(900, self._maybe_daily_update_check)
        if exit_after > 0:
            self.root.after(int(exit_after * 1000), self.shutdown)
        if self.tray is not None:
            self.root.after(2500, self._check_tray_health)
        if start_hidden and self.tray is not None:
            self.root.withdraw()
            log.info("已隐藏到系统托盘，后台自动检测中")

        if self.profile is None:
            self._set_account_state(False)
            self._sync_profile_combo()
            self._apply_status(
                {
                    "state": "need_setup",
                    "title": "还没有账户",
                    "detail": "点击右上角『＋ 新建』添加一个网络账户",
                    "time": time.strftime("%H:%M:%S"),
                }
            )
            self.status_info.configure(text="账户 — · SSID —")
            log.info("界面已就绪，尚未创建账户，配置文件：%s", APP_DIR / "config.json")
        else:
            self._set_account_state(True)
            self._sync_profile_combo()
            log.info(
                "界面已就绪，当前账户：%s，配置文件：%s",
                self.profile.name,
                APP_DIR / "config.json",
            )

    # ------------------------------------------------------------ 外观

    def _setup_scaling(self) -> None:
        ui_scale.apply_tk_scaling(self.root, self._metrics, self._ui_scale)
        if abs(self.config.ui_scale - self._ui_scale) > 0.001:
            self.config.ui_scale = self._ui_scale
            try:
                self.config.save()
            except OSError:
                pass

    def _place_window(self) -> None:
        ui_scale.place_window(self.root, self._metrics, self._ui_scale)

    def _setup_icon(self) -> None:
        try:
            self.root.iconbitmap(str(ensure_window_icon(APP_DIR)))
        except (OSError, tk.TclError):
            pass
        icon_size = ui_scale.px(28)
        image = draw_app_icon(64).resize((icon_size, icon_size), Image.LANCZOS)
        self._header_photo = ImageTk.PhotoImage(image)

    def _setup_ttk_styles(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(
            "Dark.TCombobox",
            fieldbackground=INPUT,
            background="#2b303a",
            foreground=TEXT,
            arrowcolor=MUTED,
            bordercolor=BORDER,
            lightcolor=BORDER,
            darkcolor=BORDER,
            padding=ui_scale.px(4),
            selectbackground=INPUT,
            selectforeground=TEXT,
            font=(FONT, 9),
        )
        style.map(
            "Dark.TCombobox",
            fieldbackground=[("readonly", INPUT), ("!readonly", INPUT)],
            foreground=[("readonly", TEXT)],
            bordercolor=[("focus", ACCENT)],
            arrowcolor=[("active", TEXT)],
        )
        style.configure(
            "Dark.Vertical.TScrollbar",
            background=CARD,
            troughcolor=INPUT,
            bordercolor=BORDER,
            arrowcolor=MUTED,
            darkcolor=CARD,
            lightcolor=CARD,
            arrowsize=ui_scale.px(12),
        )
        style.map(
            "Dark.Vertical.TScrollbar",
            background=[("active", MUTED)],
            arrowcolor=[("active", TEXT)],
        )

        self.root.option_add("*TCombobox*Listbox.background", INPUT)
        self.root.option_add("*TCombobox*Listbox.foreground", TEXT)
        self.root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
        self.root.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")

    def _apply_dark_titlebar(self) -> None:
        try:
            hwnd = int(self.root.frame(), 16)
        except (ValueError, tk.TclError):
            return
        value = ctypes.c_int(1)
        dwm = ctypes.windll.dwmapi
        for attribute in (20, 19):  # Win11 / Win10 1809+
            if (
                dwm.DwmSetWindowAttribute(
                    hwnd, attribute, ctypes.byref(value), ctypes.sizeof(value)
                )
                == 0
            ):
                break

    @staticmethod
    def _entry(master: tk.Misc, textvariable: tk.StringVar, show: str = "") -> tk.Entry:
        return tk.Entry(
            master,
            textvariable=textvariable,
            show=show,
            bg=INPUT,
            fg=TEXT,
            insertbackground=TEXT,
            disabledbackground=INPUT,
            disabledforeground=MUTED,
            relief="flat",
            bd=4,
            highlightthickness=1,
            highlightbackground=BORDER,
            highlightcolor=ACCENT,
            font=(FONT, 10),
        )

    @staticmethod
    def _check(
        master: tk.Misc, text: str, variable: tk.BooleanVar, command=None
    ) -> tk.Checkbutton:
        return tk.Checkbutton(
            master,
            text=text,
            variable=variable,
            command=command,
            bg=CARD,
            fg=TEXT,
            activebackground=CARD,
            activeforeground=TEXT,
            selectcolor=INPUT,
            highlightthickness=0,
            bd=0,
            font=(FONT, 9),
            cursor="hand2",
        )

    def _button(
        self, master: tk.Misc, text: str, command, primary: bool = True
    ) -> tk.Button:
        bg = ACCENT if primary else "#2b303a"
        active = ACCENT_HOVER if primary else "#363c48"
        return tk.Button(
            master,
            text=text,
            command=command,
            bg=bg,
            fg="#ffffff" if primary else TEXT,
            activebackground=active,
            activeforeground="#ffffff" if primary else TEXT,
            relief="flat",
            bd=0,
            highlightthickness=0,
            padx=14,
            pady=7,
            font=(FONT, 10, "bold"),
            cursor="hand2",
        )

    def _small_button(
        self,
        master: tk.Misc,
        text: str,
        command,
        fg: str = MUTED,
        bg: str = CARD,
    ) -> tk.Button:
        return tk.Button(
            master,
            text=text,
            command=command,
            bg=bg,
            fg=fg,
            activebackground=INPUT,
            activeforeground=TEXT,
            relief="flat",
            bd=0,
            highlightthickness=0,
            padx=6,
            pady=2,
            font=(FONT, 8),
            cursor="hand2",
        )

    @staticmethod
    def _combo(
        master: tk.Misc,
        textvariable: tk.StringVar,
        values: list[str],
        width: int,
        readonly: bool = False,
    ) -> ttk.Combobox:
        return ttk.Combobox(
            master,
            textvariable=textvariable,
            values=values,
            state="readonly" if readonly else "normal",
            style="Dark.TCombobox",
            width=width,
            font=(FONT, 9),
        )

    def _build_ui(self) -> None:
        self._viewport = tk.Canvas(self.root, bg=BG, highlightthickness=0, bd=0)
        self._viewport.pack(side="left", fill="both", expand=True)
        self._vscroll = ttk.Scrollbar(
            self.root,
            orient="vertical",
            command=self._viewport.yview,
            style="Dark.Vertical.TScrollbar",
        )
        self._viewport.configure(
            yscrollcommand=self._vscroll.set,
            yscrollincrement=ui_scale.px(20),
        )
        self._content = tk.Frame(self._viewport, bg=BG)
        self._content_window = self._viewport.create_window(
            (0, 0), window=self._content, anchor="nw"
        )
        self._viewport.bind("<Configure>", self._on_viewport_configure)
        self._content.bind("<Configure>", self._on_content_configure)
        self.root.bind_all("<MouseWheel>", self._on_mousewheel, add="+")

        outer = tk.Frame(self._content, bg=BG)
        outer.pack(fill="both", expand=True, padx=14, pady=10)
        outer.grid_columnconfigure(0, weight=1)
        outer.grid_rowconfigure(5, weight=1)

        # 顶栏
        header = tk.Frame(outer, bg=BG)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        tk.Label(header, image=self._header_photo, bg=BG).pack(side="left")
        title_box = tk.Frame(header, bg=BG)
        title_box.pack(side="left", padx=(10, 0))
        title_row = tk.Frame(title_box, bg=BG)
        title_row.pack(anchor="w")
        tk.Label(
            title_row,
            text=APP_TITLE,
            bg=BG,
            fg=TEXT,
            font=(FONT, 14, "bold"),
            anchor="w",
        ).pack(side="left", anchor="w")
        self.update_btn = tk.Button(
            title_row,
            text="检查更新",
            command=self._on_update_click,
            bg=BG,
            fg=MUTED,
            activebackground=CARD,
            activeforeground=TEXT,
            relief="flat",
            bd=0,
            highlightthickness=0,
            padx=6,
            pady=1,
            font=(FONT, 8),
            cursor="hand2",
        )
        self.update_btn.pack(side="left", padx=(8, 0), pady=(1, 0))
        tk.Label(
            title_box,
            text="多网络账户 · 自动认证助手",
            bg=BG,
            fg=HINT,
            font=(FONT, 8),
            anchor="w",
        ).pack(anchor="w")
        self.github_btn = tk.Label(
            header,
            text="GitHub ↗",
            bg=BG,
            fg=MUTED,
            font=(FONT, 8),
            cursor="hand2",
        )
        self.github_btn.pack(side="right", anchor="ne", pady=(2, 0))
        self.github_btn.bind("<Button-1>", lambda _event: self._open_github())
        self.github_btn.bind(
            "<Enter>", lambda _event: self.github_btn.configure(fg=TEXT)
        )
        self.github_btn.bind(
            "<Leave>", lambda _event: self.github_btn.configure(fg=MUTED)
        )

        # 状态卡片
        status_card = Card(outer)
        status_card.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        dot_size = ui_scale.px(16)
        dot_pad = ui_scale.px(2)
        self.status_dot = tk.Canvas(
            status_card.inner,
            width=dot_size,
            height=dot_size,
            bg=CARD,
            highlightthickness=0,
        )
        self.status_dot.grid(row=0, column=0, sticky="w", padx=(0, 9))
        self._dot = self.status_dot.create_oval(
            dot_pad, dot_pad, dot_size - dot_pad, dot_size - dot_pad, fill=GRAY, outline=""
        )
        self.status_title = tk.Label(
            status_card.inner,
            text="正在启动…",
            bg=CARD,
            fg=TEXT,
            font=(FONT, 15, "bold"),
            anchor="w",
        )
        self.status_title.grid(row=0, column=1, sticky="w")
        self.status_time = tk.Label(
            status_card.inner,
            text="",
            bg=CARD,
            fg=HINT,
            font=(FONT, 8),
            anchor="e",
        )
        self.status_time.grid(row=0, column=2, sticky="e")
        status_card.inner.grid_columnconfigure(1, weight=1)
        self.status_detail = tk.Label(
            status_card.inner,
            text="正在读取网络状态",
            bg=CARD,
            fg=MUTED,
            font=(FONT, 9),
            anchor="w",
            justify="left",
            wraplength=530,
        )
        self.status_detail.grid(row=1, column=0, columnspan=3, sticky="w", pady=(4, 0))
        self.status_info = tk.Label(
            status_card.inner,
            text="账户 — · SSID —",
            bg=CARD,
            fg=HINT,
            font=(FONT, 8),
            anchor="w",
            justify="left",
            wraplength=530,
        )
        self.status_info.grid(row=2, column=0, columnspan=3, sticky="w", pady=(2, 0))

        # 账户卡片
        account = Card(outer)
        account.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        body = account.inner
        body.grid_columnconfigure(1, weight=1)
        label_font = (FONT, 8)

        tk.Label(
            body, text="登录账号", bg=CARD, fg=TEXT, font=(FONT, 9, "bold"), anchor="w", width=8
        ).grid(row=0, column=0, sticky="w", pady=(0, 4))
        self.profile_combo = self._combo(
            body,
            self.var_profile_select,
            [p.name for p in self.config.profiles],
            18,
            readonly=True,
        )
        self.profile_combo.grid(row=0, column=1, sticky="w", pady=(0, 4), padx=(0, 8))
        self.profile_combo.bind("<<ComboboxSelected>>", self._on_profile_selected)
        profile_actions = tk.Frame(body, bg=CARD)
        profile_actions.grid(row=0, column=2, sticky="e", pady=(0, 4))
        self.new_btn = self._small_button(
            profile_actions, "＋ 新建", self._new_profile, fg=MUTED
        )
        self.new_btn.pack(side="left")
        self.delete_btn = self._small_button(
            profile_actions, "删除", self._delete_profile, fg=DANGER
        )
        self.delete_btn.pack(side="left", padx=(6, 0))

        tk.Label(body, text="账户名称", bg=CARD, fg=MUTED, font=label_font, width=8, anchor="w").grid(
            row=1, column=0, sticky="w", pady=2
        )
        self.name_entry = self._entry(body, self.var_profile_name)
        self.name_entry.grid(row=1, column=1, sticky="ew", pady=2, padx=(0, 8))

        tk.Label(body, text="用户名", bg=CARD, fg=MUTED, font=label_font, width=8, anchor="w").grid(
            row=2, column=0, sticky="w", pady=2
        )
        self.user_entry = self._entry(body, self.var_user)
        self.user_entry.grid(row=2, column=1, sticky="ew", pady=2, padx=(0, 8))

        tk.Label(body, text="密码", bg=CARD, fg=MUTED, font=label_font, width=8, anchor="w").grid(
            row=3, column=0, sticky="w", pady=2
        )
        self.pwd_entry = self._entry(body, self.var_pwd, show="●")
        self.pwd_entry.grid(row=3, column=1, sticky="ew", pady=2, padx=(0, 8))
        self.show_check = self._check(
            body, "显示", self.var_show_pwd, command=self._toggle_password_visibility
        )
        self.show_check.grid(row=3, column=2, sticky="e", pady=2)

        tk.Label(body, text="WiFi", bg=CARD, fg=MUTED, font=label_font, width=8, anchor="w").grid(
            row=4, column=0, sticky="w", pady=2
        )
        self.ssid_combo = self._combo(
            body,
            self.var_ssid,
            [self.profile.ssid] if self.profile and self.profile.ssid else [],
            18,
        )
        self.ssid_combo.grid(row=4, column=1, sticky="ew", pady=2, padx=(0, 8))
        self.wifi_btn = self._small_button(
            body, "刷新", self._refresh_wifi, fg=TEXT, bg="#2b303a"
        )
        self.wifi_btn.grid(row=4, column=2, sticky="e", pady=2)

        tk.Label(body, text="登录页", bg=CARD, fg=MUTED, font=label_font, width=8, anchor="w").grid(
            row=5, column=0, sticky="w", pady=2
        )
        self.portal_entry = self._entry(body, self.var_portal_url)
        self.portal_entry.grid(row=5, column=1, sticky="ew", pady=2, padx=(0, 8))
        self.probe_btn = self._small_button(
            body, "探测", self._probe_portal, fg=TEXT, bg="#2b303a"
        )
        self.probe_btn.grid(row=5, column=2, sticky="e", pady=2)

        self.account_hint = tk.Label(
            body,
            text="",
            bg=CARD,
            fg=HINT,
            font=(FONT, 8),
        )
        self.account_hint.grid(row=6, column=1, columnspan=2, sticky="w", pady=(1, 0))

        # 设置卡片
        options = Card(outer)
        options.grid(row=3, column=0, sticky="ew", pady=(0, 8))
        body = options.inner
        body.grid_columnconfigure(4, weight=1)
        tk.Label(
            body, text="运行设置", bg=CARD, fg=TEXT, font=(FONT, 9, "bold"), anchor="w"
        ).grid(row=0, column=0, columnspan=5, sticky="w", pady=(0, 4))
        self._check(
            body, "自动登录", self.var_auto, command=self._on_auto_toggle
        ).grid(row=1, column=0, sticky="w")
        self._check(body, "开机自启", self.var_startup).grid(
            row=1, column=1, sticky="w", padx=(14, 0)
        )
        self._check(
            body, "关闭窗口时最小化到托盘", self.var_tray
        ).grid(row=1, column=2, columnspan=2, sticky="w", padx=(14, 0))
        tk.Label(body, text="检测间隔", bg=CARD, fg=MUTED, font=(FONT, 9)).grid(
            row=2, column=0, sticky="w", pady=(4, 0)
        )
        spin = tk.Spinbox(
            body,
            from_=5,
            to=300,
            increment=5,
            textvariable=self.var_interval,
            width=4,
            justify="center",
            bg=INPUT,
            fg=TEXT,
            buttonbackground=CARD,
            insertbackground=TEXT,
            relief="flat",
            bd=2,
            highlightthickness=1,
            highlightbackground=BORDER,
            highlightcolor=ACCENT,
            font=(FONT, 9),
        )
        spin.grid(row=2, column=1, sticky="w", pady=(4, 0), padx=(6, 4))
        tk.Label(body, text="秒", bg=CARD, fg=MUTED, font=(FONT, 9)).grid(
            row=2, column=2, sticky="w", pady=(4, 0)
        )
        tk.Label(
            body,
            text="后台按此间隔检测，发现需要认证就自动登录",
            bg=CARD,
            fg=HINT,
            font=(FONT, 8),
        ).grid(row=2, column=3, columnspan=2, sticky="w", pady=(4, 0), padx=(8, 0))

        tk.Label(body, text="界面缩放", bg=CARD, fg=MUTED, font=(FONT, 9)).grid(
            row=3, column=0, sticky="w", pady=(4, 0)
        )
        self.scale_combo = self._combo(
            body,
            self.var_ui_scale,
            [item[0] for item in ui_scale.manual_options()],
            8,
            readonly=True,
        )
        self.scale_combo.grid(row=3, column=1, sticky="w", pady=(4, 0), padx=(6, 4))
        self.scale_combo.bind("<<ComboboxSelected>>", self._on_ui_scale_changed)
        self.scale_hint = tk.Label(
            body,
            text=self._scale_hint_text(),
            bg=CARD,
            fg=HINT,
            font=(FONT, 8),
        )
        self.scale_hint.grid(
            row=3, column=3, columnspan=2, sticky="w", pady=(4, 0), padx=(8, 0)
        )

        # 按钮
        buttons = tk.Frame(outer, bg=BG)
        buttons.grid(row=4, column=0, sticky="ew", pady=(0, 8))
        buttons.grid_columnconfigure(0, weight=3)
        buttons.grid_columnconfigure(1, weight=2)
        self.check_button = self._button(
            buttons, "立即检测并登录", self._on_check, primary=True
        )
        self.check_button.grid(row=0, column=0, sticky="ew", padx=(0, 8), ipady=1)
        self.save_button = self._button(
            buttons, "保存设置", self._on_save, primary=False
        )
        self.save_button.grid(row=0, column=1, sticky="ew", ipady=1)

        # 日志
        log_card = Card(outer)
        log_card.grid(row=5, column=0, sticky="nsew")
        body = log_card.inner
        body.grid_rowconfigure(1, weight=1)
        body.grid_columnconfigure(0, weight=1)
        head = tk.Frame(body, bg=CARD)
        head.grid(row=0, column=0, sticky="ew", pady=(0, 5))
        tk.Label(
            head, text="运行日志", bg=CARD, fg=TEXT, font=(FONT, 9, "bold")
        ).pack(side="left")
        clear = tk.Label(
            head,
            text="清空",
            bg=CARD,
            fg=HINT,
            font=(FONT, 8),
            cursor="hand2",
        )
        clear.pack(side="right")
        clear.bind("<Button-1>", lambda _event: self._clear_log())
        clear.bind("<Enter>", lambda _event: clear.configure(fg=TEXT))
        clear.bind("<Leave>", lambda _event: clear.configure(fg=HINT))

        log_wrap = tk.Frame(body, bg=CARD)
        log_wrap.grid(row=1, column=0, sticky="nsew")
        log_wrap.grid_rowconfigure(0, weight=1)
        log_wrap.grid_columnconfigure(0, weight=1)
        self.log_text = tk.Text(
            log_wrap,
            height=7,
            wrap="word",
            bg="#101216",
            fg=TEXT,
            insertbackground=TEXT,
            selectbackground=ACCENT,
            relief="flat",
            bd=0,
            padx=9,
            pady=7,
            font=(MONO, 9),
            state="disabled",
        )
        self.log_text.grid(row=0, column=0, sticky="nsew")
        self.log_scroll = ttk.Scrollbar(
            log_wrap,
            orient="vertical",
            command=self.log_text.yview,
            style="Dark.Vertical.TScrollbar",
        )
        self.log_scroll.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=self.log_scroll.set)
        self.log_text.tag_configure("time", foreground=HINT)
        self.log_text.tag_configure("INFO", foreground=MUTED)
        self.log_text.tag_configure("SUCCESS", foreground=SUCCESS)
        self.log_text.tag_configure("WARNING", foreground=WARN)
        self.log_text.tag_configure("ERROR", foreground=DANGER)

        # 底部说明
        tk.Label(
            outer,
            text="关闭窗口会隐藏到系统托盘并继续后台检测 · 双击托盘图标可重新打开",
            bg=BG,
            fg=HINT,
            font=(FONT, 8),
            wraplength=580,
        ).grid(row=6, column=0, sticky="w", pady=(6, 0))

    # ------------------------------------------------------------ 布局与缩放

    def _scale_choice_label(self) -> str:
        if self.config.ui_scale_mode == "manual" and 1.0 <= self.config.ui_scale <= 2.5:
            return f"{int(round(self.config.ui_scale * 100))}%"
        return "自动"

    def _scale_hint_text(self) -> str:
        return f"当前 {int(round(self._ui_scale * 100))}% · 重启后生效"

    def _save_config(self) -> bool:
        try:
            self.config.save()
            return True
        except OSError as exc:
            log.error("保存配置失败：%s", exc)
            return False

    def _on_ui_scale_changed(self, _event=None) -> None:
        label = self.var_ui_scale.get()
        selected = next(
            (item for item in ui_scale.manual_options() if item[0] == label), None
        )
        if selected is None:
            self.var_ui_scale.set(self._scale_choice_label())
            return
        _, mode, value = selected
        self.config.ui_scale_mode = mode
        self.config.ui_scale = (
            value if mode == "manual" else round(self._ui_scale, 2)
        )
        if not self._save_config():
            self.var_ui_scale.set(self._scale_choice_label())
            return
        self.scale_hint.configure(text=self._scale_hint_text())
        log.info("界面缩放已设置为 %s，重启后生效", label)

    def _on_viewport_configure(self, event) -> None:
        try:
            self._viewport.itemconfigure(
                self._content_window, width=max(1, int(event.width))
            )
            request_height = self._content.winfo_reqheight()
            target = max(request_height, int(event.height))
            if target != self._content_height:
                self._content_height = target
                self._viewport.itemconfigure(self._content_window, height=target)
        except tk.TclError:
            pass
        self._sync_scroll()

    def _on_content_configure(self, _event) -> None:
        try:
            request_height = self._content.winfo_reqheight()
            viewport_height = max(1, self._viewport.winfo_height())
            target = max(request_height, viewport_height)
            if target != self._content_height:
                self._content_height = target
                self._viewport.itemconfigure(self._content_window, height=target)
        except tk.TclError:
            pass
        self._sync_scroll()

    def _sync_scroll(self) -> None:
        if self._closing:
            return
        try:
            bbox = self._viewport.bbox("all")
            if not bbox:
                return
            self._viewport.configure(scrollregion=bbox)
            need = (bbox[3] - bbox[1]) > self._viewport.winfo_height() + 1
            if need and not self._scroll_visible:
                self._vscroll.pack(side="right", fill="y")
                self._scroll_visible = True
            elif not need and self._scroll_visible:
                self._vscroll.pack_forget()
                self._scroll_visible = False
                self._viewport.yview_moveto(0)
        except tk.TclError:
            pass

    def _on_mousewheel(self, event) -> None:
        if not self._scroll_visible:
            return
        try:
            if event.widget.winfo_toplevel() is not self.root:
                return
        except tk.TclError:
            return
        if isinstance(
            event.widget,
            (
                tk.Text,
                tk.Listbox,
                tk.Spinbox,
                ttk.Scrollbar,
                ttk.Combobox,
            ),
        ):
            return
        delta = int(-event.delta / 120) if event.delta else 0
        if not delta:
            return
        try:
            self._viewport.yview_scroll(delta * 3, "units")
        except tk.TclError:
            pass

    # ------------------------------------------------------------ 更新

    def _open_github(self) -> None:
        webbrowser.open(f"https://github.com/{updater.GITHUB_REPO}")

    def _on_update_click(self) -> None:
        if self._update_dialog is not None:
            try:
                if self._update_dialog.winfo_exists():
                    self._update_dialog.lift()
                    self._update_dialog.focus_force()
                    return
            except tk.TclError:
                pass
            self._update_dialog = None
        self._update_dialog = UpdateDialog(self)

    def clear_update_dialog(self, dialog: UpdateDialog) -> None:
        if self._update_dialog is dialog:
            self._update_dialog = None

    def _maybe_daily_update_check(self) -> None:
        if self._closing or self._update_check_busy:
            return
        if self.config.last_update_check_date == updater.today_str():
            return
        self.start_update_check()

    def start_update_check(self, dialog: UpdateDialog | None = None) -> None:
        if dialog is not None:
            self._update_dialog = dialog
        if self._update_check_busy:
            return
        self._update_check_busy = True
        self._set_update_button("checking")

        def worker() -> None:
            result = updater.check_for_update(__version__)
            self.events.put({"type": "update_check", "result": result})

        threading.Thread(target=worker, name="update-check", daemon=True).start()

    def _handle_update_check(self, result: object) -> None:
        self._update_check_busy = False
        if not isinstance(result, updater.CheckResult):
            result = updater.CheckResult(
                current_version=__version__, error="检查更新失败"
            )
        self._update_result = result
        if not result.error:
            self.config.last_update_check_date = updater.today_str()
            self._save_config()

        if result.error:
            self._set_update_button("error")
            log.warning("检查更新失败：%s", result.error)
        elif result.has_update and result.latest is not None:
            self._set_update_button("available", result.latest.tag)
            log.info("发现新版本：%s", result.latest.tag)
        else:
            self._set_update_button("latest")
            log.info("检查更新完成：当前已是最新版本")

        dialog = self._update_dialog
        if dialog is not None:
            try:
                if dialog.winfo_exists():
                    dialog.show_result(result)
                else:
                    self._update_dialog = None
            except tk.TclError:
                self._update_dialog = None

    def _set_update_button(self, state: str, tag: str = "") -> None:
        if state == "checking":
            text, color, background = "检查中…", HINT, BG
        elif state == "available":
            text = f"发现新版本 {tag}" if tag else "发现新版本"
            color, background = ACCENT, "#16243a"
        elif state == "latest":
            text, color, background = "已是最新", MUTED, BG
        elif state == "error":
            text, color, background = "检查失败，重试", WARN, BG
        else:
            text, color, background = "检查更新", MUTED, BG
        try:
            self.update_btn.configure(
                text=text,
                fg=color,
                bg=background,
                activebackground=CARD,
                font=(FONT, 8, "bold" if state == "available" else "normal"),
            )
        except tk.TclError:
            pass

    # ------------------------------------------------------------ 事件

    def _poll_events(self) -> None:
        if self._closing:
            return
        for _ in range(300):
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            try:
                self._handle_event(event)
            except Exception:  # noqa: BLE001
                log.exception("处理界面事件失败")
        self._poll_job = self.root.after(100, self._poll_events)

    def _handle_event(self, event: dict) -> None:
        kind = event.get("type")
        if kind == "status":
            self._apply_status(event)
        elif kind == "info":
            self._apply_info(event)
        elif kind == "log":
            self._append_log(
                event.get("time", ""),
                event.get("level", "INFO"),
                event.get("message", ""),
            )
        elif kind == "notify":
            if self.tray is not None:
                self.tray.notify(event.get("title", APP_TITLE), event.get("message", ""))
        elif kind == "portal_probe":
            self._handle_portal_probe(event)
        elif kind == "wifi_list":
            self._handle_wifi_list(event)
        elif kind == "update_check":
            self._handle_update_check(event.get("result"))
        elif kind == "action":
            self._handle_action(event.get("action", ""))

    def _apply_status(self, event: dict) -> None:
        state = event.get("state", "init")
        title = event.get("title", "")
        detail = event.get("detail", "")
        color = STATE_COLORS.get(state, GRAY)
        self.status_dot.itemconfigure(self._dot, fill=color)
        self.status_title.configure(text=title, fg=color)
        self.status_detail.configure(text=detail)
        self.status_time.configure(text=event.get("time", ""))
        self._current_state = state
        if self.tray is not None and state != self._last_state:
            self.tray.set_status(state, title)
        self._last_state = state

    def _apply_info(self, event: dict) -> None:
        profile_name = event.get("profile_name") or (
            self.profile.name if self.profile else "—"
        )
        ssid = event.get("ssid") or ""
        parts = [f"账户 {profile_name}"]
        if event.get("connected"):
            parts.append(f"SSID {ssid or '—'}")
            if event.get("signal") is not None:
                parts.append(f"信号 {event['signal']}%")
        else:
            target = (self.profile.ssid if self.profile else "") or "未设置"
            parts.append(f"未连接 WiFi（目标 {target}）")
        parts.append(f"IP {event.get('ip') or '—'}")
        self.status_info.configure(text=" · ".join(parts))

        if ssid:
            values = list(self.ssid_combo.cget("values"))
            if ssid not in values:
                values.append(ssid)
                self.ssid_combo.configure(values=values)

    def _append_log(self, timestamp: str, level: str, message: str) -> None:
        self.log_text.configure(state="normal")
        if timestamp:
            self.log_text.insert("end", f"{timestamp}  ", ("time",))
        self.log_text.insert("end", f"{message}\n", (level,))
        line_count = int(self.log_text.index("end-1c").split(".")[0])
        if line_count > 700:
            self.log_text.delete("1.0", f"{line_count - 450}.0")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _clear_log(self) -> None:
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    # ------------------------------------------------------------ 账户操作

    def _load_profile(self, profile: Profile) -> None:
        self.profile = profile
        self.var_profile_name.set(profile.name)
        self.var_user.set(profile.username)
        self.var_pwd.set(profile.get_password())
        self.var_ssid.set(profile.ssid)
        self.var_portal_url.set(profile.portal_url)
        self.var_show_pwd.set(False)
        self.pwd_entry.configure(show="●")
        self.var_profile_select.set(profile.name)
        values = list(self.ssid_combo.cget("values"))
        if profile.ssid and profile.ssid not in values:
            values.append(profile.ssid)
            self.ssid_combo.configure(values=values)
        self._set_account_state(True)

    def _sync_profile_combo(self) -> None:
        names = [p.name for p in self.config.profiles]
        self.profile_combo.configure(values=names)
        self.var_profile_select.set(self.profile.name if self.profile else "")

    def _ensure_controller(self) -> None:
        if self.controller is None and self.profile is not None:
            self.controller = Controller(
                self.config, self.profile, self.var_pwd.get(), self.events
            )
            self.controller.start()

    def _set_account_state(self, enabled: bool) -> None:
        state = tk.NORMAL if enabled else tk.DISABLED
        for widget in (
            self.name_entry,
            self.user_entry,
            self.pwd_entry,
            self.portal_entry,
        ):
            widget.configure(state=state)
        self.show_check.configure(state=state)
        self.ssid_combo.configure(state="normal" if enabled else "disabled")
        self.profile_combo.configure(state="readonly" if enabled else "disabled")
        for button in (
            self.wifi_btn,
            self.probe_btn,
            self.delete_btn,
            self.check_button,
            self.save_button,
        ):
            button.configure(state=state)
        self.account_hint.configure(
            text=(
                "登录页留空则自动探测；仅在自动探测失败的网络才需填写完整地址（含参数）"
                if enabled
                else "还没有账户，点击右上角『＋ 新建』添加；每个账户绑定一个 WiFi 和登录页"
            )
        )

    def _capture_form(self) -> bool:
        if self.profile is None:
            log.warning("请先新建账户")
            return False
        name = self.var_profile_name.get().strip()
        if not name:
            log.warning("账户名称不能为空")
            return False
        if any(
            p.id != self.profile.id and p.name.casefold() == name.casefold()
            for p in self.config.profiles
        ):
            log.warning("账户名称「%s」已存在，请换一个名称", name)
            return False
        portal_url = self.var_portal_url.get().strip()
        if portal_url and not portal_url.lower().startswith(("http://", "https://")):
            portal_url = "http://" + portal_url
            self.var_portal_url.set(portal_url)
        self.profile.name = name
        self.profile.ssid = self.var_ssid.get().strip()
        self.profile.username = self.var_user.get().strip()
        self.profile.portal_url = portal_url
        self.profile.set_password(self.var_pwd.get())
        return True

    def _on_profile_selected(self, _event=None) -> None:
        if self.profile is None:
            return
        selected_name = self.var_profile_select.get()
        target = next(
            (p for p in self.config.profiles if p.name == selected_name), None
        )
        if target is None or target.id == self.profile.id:
            return
        if not self._save_settings():
            self.var_profile_select.set(self.profile.name)
            return
        self.config.active_profile_id = target.id
        self._load_profile(target)
        try:
            self.config.save()
        except OSError as exc:
            log.error("保存配置失败：%s", exc)
        self._sync_profile_combo()
        self.controller.update(self.config, target, self.var_pwd.get())
        self.controller.trigger()
        log.info("已切换到账户：%s", target.name)

    def _new_profile(self) -> None:
        if self.profile is not None and not self._save_settings():
            return
        profile = self.config.add_profile("新账户")
        self.config.active_profile_id = profile.id
        try:
            self.config.save()
        except OSError as exc:
            log.error("保存配置失败：%s", exc)
        self._load_profile(profile)
        self._sync_profile_combo()
        self._ensure_controller()
        if self.controller is not None:
            self.controller.update(self.config, profile, self.var_pwd.get())
            self.controller.trigger()
        self.name_entry.focus_set()
        log.info("已创建账户「%s」，请填写 WiFi、账号和密码", profile.name)

    def _delete_profile(self) -> None:
        if self.profile is None:
            return
        if len(self.config.profiles) <= 1:
            log.warning("至少需要保留一个账户")
            return
        if not messagebox.askyesno(
            "删除账户",
            f"确定删除账户「{self.profile.name}」吗？\n该账户保存的账号密码也会一并删除。",
            parent=self.root,
        ):
            return
        deleted_name = self.profile.name
        if not self.config.delete_profile(self.profile.id):
            log.warning("删除账户失败")
            return
        try:
            self.config.save()
        except OSError as exc:
            log.error("保存配置失败：%s", exc)
        target = self.config.active_profile()
        self._load_profile(target)
        self._sync_profile_combo()
        self.controller.update(self.config, target, self.var_pwd.get())
        self.controller.trigger()
        log.info("已删除账户「%s」，当前账户：%s", deleted_name, target.name)

    # ------------------------------------------------------------ 操作

    def _toggle_password_visibility(self) -> None:
        self.pwd_entry.configure(show="" if self.var_show_pwd.get() else "●")

    def _on_auto_toggle(self) -> None:
        enabled = self.var_auto.get()
        self.config.auto_login = enabled
        try:
            self.config.save()
        except OSError as exc:
            log.error("保存配置失败：%s", exc)
        if self.controller is not None:
            self.controller.update(self.config, self.profile, self.var_pwd.get())
        if self.tray is not None:
            self.tray.set_auto_login(enabled)
        log.info("自动登录已%s", "开启" if enabled else "关闭")

    def _on_save(self) -> None:
        self._save_settings()

    def _save_settings(self) -> bool:
        if self.profile is None:
            log.warning("请先新建账户")
            return False
        if not self._capture_form():
            return False
        try:
            interval = int(float(self.var_interval.get()))
        except ValueError:
            interval = self.config.check_interval
        interval = max(5, min(300, interval))
        self.var_interval.set(str(interval))

        previous_startup = self.config.start_with_windows
        self.config.active_profile_id = self.profile.id
        self.config.auto_login = self.var_auto.get()
        self.config.check_interval = interval
        self.config.minimize_to_tray = self.var_tray.get()
        self.config.start_with_windows = self.var_startup.get()

        try:
            self.config.save()
        except OSError as exc:
            log.error("保存配置失败：%s", exc)
            return False

        if self.config.start_with_windows != previous_startup or self.config.start_with_windows:
            try:
                set_autostart(self.config.start_with_windows)
            except OSError as exc:
                log.error("设置开机自启失败：%s", exc)
                self.var_startup.set(is_autostart_enabled())
                self.config.start_with_windows = self.var_startup.get()

        self._sync_profile_combo()
        if self.controller is not None:
            self.controller.update(self.config, self.profile, self.var_pwd.get())
        if self.tray is not None:
            self.tray.set_auto_login(self.config.auto_login)

        if self.var_pwd.get() and not self.profile.password_enc:
            log.error("密码未能通过 Windows DPAPI 加密，未安全保存到磁盘；当前会话仍可使用")
        elif not self.profile.ssid:
            log.warning("账户「%s」还没有选择 WiFi", self.profile.name)
        elif not self.profile.username.strip() or not self.var_pwd.get():
            log.warning("账户「%s」的账号或密码为空，自动登录不会执行", self.profile.name)
        else:
            log_success("设置已保存（账户：%s）", self.profile.name)
        return True

    def _on_check(self) -> None:
        if self.profile is None or self.controller is None:
            log.warning("请先新建账户")
            return
        if not self._save_settings():
            return
        log.info("手动检测已触发（账户：%s）", self.profile.name)
        self.controller.trigger()

    def _probe_portal(self) -> None:
        if self._probe_busy:
            return
        self._probe_busy = True
        self.probe_btn.configure(text="探测中…", state="disabled")
        log.info("正在探测登录页…（若当前已在线，请先在浏览器里注销认证）")

        def worker() -> None:
            try:
                probe = check_connectivity(timeout=3.5)
                self.events.put(
                    {
                        "type": "portal_probe",
                        "url": probe.portal_url,
                        "online": probe.online,
                        "status": probe.status,
                        "detail": probe.detail,
                    }
                )
            except Exception as exc:  # noqa: BLE001
                self.events.put(
                    {
                        "type": "portal_probe",
                        "url": None,
                        "online": False,
                        "status": "error",
                        "detail": f"{type(exc).__name__}: {exc}",
                    }
                )

        threading.Thread(target=worker, name="portal-probe", daemon=True).start()

    def _handle_portal_probe(self, event: dict) -> None:
        self._probe_busy = False
        self.probe_btn.configure(
            text="探测", state="normal" if self.profile is not None else "disabled"
        )
        url = event.get("url")
        if url:
            self.var_portal_url.set(url)
            log_success("已探测到登录页并填入：%s", url)
        elif event.get("online"):
            log.info("当前网络已在线，未探测到认证页；可先断开认证后再试")
        else:
            log.warning("未探测到登录页：%s", event.get("detail") or "未知原因")

    def _refresh_wifi(self) -> None:
        if self._wifi_busy:
            return
        self._wifi_busy = True
        self.wifi_btn.configure(text="刷新中…", state="disabled")

        def worker() -> None:
            try:
                items = list_wifi_networks()
            except Exception as exc:  # noqa: BLE001
                log.warning("扫描 WiFi 失败：%s", exc)
                items = []
            self.events.put({"type": "wifi_list", "items": items})

        threading.Thread(target=worker, name="wifi-scan", daemon=True).start()

    def _handle_wifi_list(self, event: dict) -> None:
        self._wifi_busy = False
        self.wifi_btn.configure(
            text="刷新", state="normal" if self.profile is not None else "disabled"
        )
        values = list(self.ssid_combo.cget("values"))
        profile_ssid = self.profile.ssid if self.profile else ""
        for item in list(event.get("items") or []) + [profile_ssid, self.var_ssid.get()]:
            if item and item not in values:
                values.append(item)
        self.ssid_combo.configure(values=values)

    def _handle_action(self, action: str) -> None:
        if action == "show":
            self._show_window()
        elif action == "check":
            self._on_check()
        elif action == "toggle_auto":
            self.var_auto.set(not self.var_auto.get())
            self._on_auto_toggle()
        elif action == "quit":
            self.shutdown()

    def _show_window(self) -> None:
        self.root.deiconify()
        self.root.state("normal")
        self.root.lift()
        try:
            self.root.attributes("-topmost", True)
            self.root.after(250, lambda: self.root.attributes("-topmost", False))
        except tk.TclError:
            pass
        self.root.focus_force()

    def _check_tray_health(self) -> None:
        if self.tray is not None and not self.tray.alive():
            log.error("系统托盘不可用，已切换为普通窗口模式")
            self.tray = None
            if self.root.state() == "withdrawn":
                self.root.deiconify()
                self.root.lift()

    def _on_close(self) -> None:
        if self.config.minimize_to_tray and self.tray is not None and self.tray.alive():
            self.root.withdraw()
            if not self._hide_notified:
                self._hide_notified = True
                self.tray.notify(
                    APP_TITLE, "已最小化到系统托盘，仍在后台自动检测。"
                )
            log.info("窗口已隐藏到托盘")
        else:
            self.shutdown()

    def shutdown(self) -> None:
        if self._closing:
            return
        self._closing = True
        if self._poll_job is not None:
            try:
                self.root.after_cancel(self._poll_job)
            except tk.TclError:
                pass
            self._poll_job = None
        log.info("正在退出…")
        try:
            if self.controller is not None:
                self.controller.stop()
        finally:
            if self.tray is not None:
                self.tray.stop()
            try:
                self.root.destroy()
            except tk.TclError:
                pass

    def run(self) -> None:
        self.root.mainloop()