"""系统托盘。"""

from __future__ import annotations

import logging
import queue
import threading

import pystray

from . import APP_TITLE
from .icons import (
    COLOR_CAPTIVE,
    COLOR_ERROR,
    COLOR_OFFLINE,
    COLOR_ONLINE,
    COLOR_WORKING,
    tray_icon_image,
)

log = logging.getLogger("campus")


class TrayIcon:
    def __init__(self, actions: "queue.Queue[dict]") -> None:
        self._actions = actions
        self._auto_login = True
        self._state = "init"

        self._auto_item = pystray.MenuItem(
            "自动登录：已开启", lambda _icon, _item: self._send("toggle_auto")
        )
        self._menu = pystray.Menu(
            pystray.MenuItem(
                "打开主界面", lambda _icon, _item: self._send("show"), default=True
            ),
            pystray.MenuItem(
                "立即检测并登录", lambda _icon, _item: self._send("check")
            ),
            pystray.Menu.SEPARATOR,
            self._auto_item,
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("退出", lambda _icon, _item: self._send("quit")),
        )
        self._icon = pystray.Icon(
            "campus_net_autologin",
            tray_icon_image(COLOR_OFFLINE),
            f"{APP_TITLE} · 正在启动",
            self._menu,
        )
        self._thread: threading.Thread | None = None

    def _send(self, action: str) -> None:
        self._actions.put({"type": "action", "action": action})

    def start(self) -> None:
        try:
            # 用守护线程运行，避免托盘消息循环未退出时卡住整个进程
            self._thread = threading.Thread(
                target=self._icon.run, name="campus-tray", daemon=True
            )
            self._thread.start()
        except Exception:  # noqa: BLE001
            log.exception("托盘图标启动失败")

    def stop(self) -> None:
        try:
            self._icon.stop()
        except Exception:  # noqa: BLE001
            pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)

    def alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def set_status(self, state: str, title: str) -> None:
        color = {
            "online": COLOR_ONLINE,
            "success": COLOR_ONLINE,
            "captive": COLOR_CAPTIVE,
            "need_setup": COLOR_CAPTIVE,
            "logging_in": COLOR_WORKING,
            "checking": COLOR_WORKING,
            "failed": COLOR_ERROR,
            "network_error": COLOR_ERROR,
        }.get(state, COLOR_OFFLINE)
        try:
            self._icon.icon = tray_icon_image(color)
            self._icon.title = f"{APP_TITLE} · {title}"
        except Exception:  # noqa: BLE001
            pass
        self._state = state

    def set_auto_login(self, enabled: bool) -> None:
        self._auto_login = enabled
        try:
            self._auto_item.text = "自动登录：已开启" if enabled else "自动登录：已关闭"
            self._icon.update_menu()
        except Exception:  # noqa: BLE001
            pass

    def notify(self, title: str, message: str) -> None:
        try:
            self._icon.notify(message, title)
        except Exception:  # noqa: BLE001
            pass