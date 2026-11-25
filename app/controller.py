"""后台检测与自动登录调度（支持多账户）。"""

from __future__ import annotations

import logging
import queue
import threading
import time
from urllib.parse import urlparse

from .config import AppConfig, Profile
from .logging_setup import log_success
from .network import check_connectivity, get_local_ipv4, get_wifi_info
from .portal import PortalError, attempt_login

log = logging.getLogger("campus")


def _mask_user(name: str) -> str:
    name = name.strip()
    if not name:
        return "(空)"
    if len(name) <= 5:
        return name[0] + "*" * (len(name) - 1)
    return name[:3] + "****" + name[-4:]


class Controller:
    def __init__(
        self,
        config: AppConfig,
        profile: Profile,
        password: str,
        events: "queue.Queue[dict]",
    ) -> None:
        self.config = config
        self.profile = profile
        self.password = password
        self.events = events

        self._stop = threading.Event()
        self._wake = threading.Event()
        self._manual_pending = False
        self._thread: threading.Thread | None = None

        self._fail_count = 0
        self._next_login_at = 0.0
        self._last_state = "init"
        self._last_ssid = ""
        self._last_ip = ""

    # ------------------------------------------------------------ 生命周期

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._run, name="campus-controller", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)

    def trigger(self) -> None:
        """手动检测：忽略自动登录开关，立即执行一次。"""
        self._manual_pending = True
        self._fail_count = 0
        self._next_login_at = 0.0
        self._wake.set()

    def update(
        self,
        config: AppConfig,
        profile: Profile,
        password: str,
        *,
        wake: bool = True,
    ) -> None:
        self.config = config
        self.profile = profile
        self.password = password
        if wake:
            self._fail_count = 0
            self._next_login_at = 0.0
            self._wake.set()

    # ------------------------------------------------------------ 内部

    def _emit(self, event: dict) -> None:
        self.events.put(event)

    def _status(self, state: str, title: str, detail: str = "") -> None:
        self._last_state = state
        self._emit(
            {
                "type": "status",
                "state": state,
                "title": title,
                "detail": detail,
                "time": time.strftime("%H:%M:%S"),
                "ssid": self._last_ssid,
                "ip": self._last_ip,
                "profile": self.profile.name,
            }
        )

    def _notify(self, title: str, message: str) -> None:
        if self.config.notify:
            self._emit({"type": "notify", "title": title, "message": message})

    def _wait(self, seconds: float) -> None:
        if seconds > 0 and not self._stop.is_set():
            self._wake.wait(seconds)
        self._wake.clear()

    def _run(self) -> None:
        log.info("后台检测已启动（账户：%s）", self.profile.name)
        self._status("init", "正在启动…", f"账户：{self.profile.name}")
        paused_notified = False
        while not self._stop.is_set():
            manual = self._manual_pending
            self._manual_pending = False
            if not self.config.auto_check and not manual:
                if not paused_notified:
                    log.info("自动检测已关闭，等待手动检测")
                    paused_notified = True
                self._status(
                    "paused",
                    "自动检测已关闭",
                    "点击「立即检测并登录」或重新开启自动检测",
                )
                self._wake.wait()
                self._wake.clear()
                continue
            paused_notified = False
            try:
                self._tick(manual)
            except Exception as exc:  # noqa: BLE001
                log.exception("检测流程异常")
                self._status("network_error", "程序异常", str(exc)[:160])
                self._wait(10)
        log.info("后台检测已停止")

    def _tick(self, manual: bool) -> None:
        profile = self.profile
        wifi = get_wifi_info()
        target_host = (
            urlparse(profile.portal_url).hostname if profile.portal_url else ""
        ) or "8.8.8.8"
        ip = get_local_ipv4(target_host)
        self._last_ssid = wifi.ssid
        self._last_ip = ip
        self._emit(
            {
                "type": "info",
                "connected": wifi.connected,
                "ssid": wifi.ssid,
                "signal": wifi.signal,
                "ip": ip,
                "mac": wifi.mac,
                "profile_id": profile.id,
                "profile_name": profile.name,
            }
        )

        target = profile.ssid.strip().casefold()
        if not target:
            self._status(
                "need_setup",
                "请为该账户选择 WiFi",
                f"账户「{profile.name}」还没有绑定 WiFi",
            )
            self._wait(max(5, self.config.check_interval))
            return

        if not wifi.connected:
            self._status(
                "no_wifi", "未连接无线网络", f"请先连接「{profile.ssid}」"
            )
            self._wait(self.config.check_interval)
            return

        if wifi.ssid.strip().casefold() != target:
            self._status(
                "other_wifi",
                f"账户「{profile.name}」与当前网络不匹配",
                f"当前：{wifi.ssid} · 该账户需要：{profile.ssid}",
            )
            self._wait(self.config.check_interval)
            return

        if not profile.username.strip() or not self.password:
            self._status(
                "need_setup",
                "请填写该账户的账号和密码",
                f"当前账户：{profile.name}",
            )
            self._wait(max(5, self.config.check_interval))
            return

        if manual:
            self._status("checking", "正在检测…", f"账户：{profile.name}")

        probe = check_connectivity(timeout=3.5)
        if probe.online:
            recovered = self._last_state in (
                "captive",
                "failed",
                "logging_in",
                "network_error",
            )
            self._fail_count = 0
            self._next_login_at = 0.0
            if recovered:
                log_success("网络已恢复在线")
            self._status("online", "在线", "网络正常，无需登录")
            self._wait(self.config.check_interval)
            return

        custom = profile.portal_url.strip()
        # 优先使用本次网络实时重定向到的认证页，避免自定义地址里的 IP/MAC 参数过期
        portal_url = probe.portal_url or custom
        using_custom = not probe.portal_url and bool(custom)
        can_attempt = probe.status == "captive" or (
            custom and probe.status in ("offline", "unknown")
        )
        if not portal_url or not can_attempt:
            self._status(
                "network_error",
                "网络异常",
                f"未获取到认证页：{probe.detail}",
            )
            self._wait(self.config.check_interval)
            return

        self._status(
            "captive",
            "需要登录",
            "使用自定义登录页" if using_custom else "已检测到校园网认证页面",
        )
        if not (self.config.auto_login or manual):
            self._wait(self.config.check_interval)
            return

        now = time.time()
        if now < self._next_login_at:
            remaining = int(self._next_login_at - now) + 1
            self._status(
                "captive", "需要登录", f"为避免账号被锁，{remaining} 秒后重试"
            )
            self._wait(remaining)
            return

        self._status("logging_in", "正在登录…", f"账户：{profile.name}")
        log.info(
            "开始自动登录（账户 %s / 账号 %s）",
            profile.name,
            _mask_user(profile.username),
        )
        try:
            result = attempt_login(portal_url, profile.username, self.password)
        except PortalError as exc:
            self._login_failed(str(exc))
            return

        if result.okish or result.outcome == "unknown":
            if result.outcome == "unknown":
                log.info("认证响应无法直接判断，正在验证网络连通性…")
            if self._verify_online():
                self._fail_count = 0
                self._next_login_at = 0.0
                log_success("登录成功，网络已恢复")
                self._status(
                    "success", "登录成功", f"账户「{profile.name}」已联网"
                )
                self._notify(
                    "校园网自动登录",
                    f"账户「{profile.name}」登录成功，网络已恢复。",
                )
                self._wait(max(3, self.config.check_interval))
                return
            self._login_failed(result.message or "认证后网络仍不可用")
            return

        self._login_failed(result.message or "登录失败")

    def _login_failed(self, message: str) -> None:
        self._fail_count += 1
        delay = min(15 * (2 ** (self._fail_count - 1)), 600)
        self._next_login_at = time.time() + delay
        log.warning("登录失败：%s（%s 秒后重试）", message, delay)
        self._status("failed", "登录失败", f"{message}（{delay} 秒后重试）")
        if self._fail_count == 1:
            self._notify(
                "校园网自动登录失败",
                f"账户「{self.profile.name}」：{message}\n将在 {delay} 秒后重试。",
            )
        self._wait(delay)

    def _verify_online(self) -> bool:
        # 认证通过后 AC 放行可能需要几秒，最多等待约 15 秒
        for attempt in range(7):
            if self._stop.is_set():
                return False
            self._wait(1.5 if attempt else 1.0)
            if check_connectivity(timeout=3.0).online:
                return True
        return False