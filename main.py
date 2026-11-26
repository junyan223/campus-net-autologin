"""程序入口。"""

from __future__ import annotations

import argparse
import ctypes
import logging
import queue
import sys
import tkinter.messagebox as messagebox
from pathlib import Path

from app import APP_TITLE, __version__
from app.config import APP_DIR, APP_NAME, CONFIG_PATH, AppConfig
from app.icons import save_ico
from app.instance import SingleInstanceGuard
from app.logging_setup import setup_logging
from app.network import (
    check_connectivity,
    get_local_ipv4,
    get_wifi_info,
)
from app.portal import PortalError, fetch_portal_form
from app.ui import MainWindow

log = logging.getLogger("campus")


def enable_dpi_awareness() -> None:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:  # noqa: BLE001
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:  # noqa: BLE001
            pass
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_NAME)
    except Exception:  # noqa: BLE001
        pass


def run_selftest(parse_url: str | None) -> int:
    setup_logging(APP_DIR)
    print(f"版本: {__version__}")
    print(f"配置: {CONFIG_PATH} (存在: {CONFIG_PATH.exists()})")
    cfg = AppConfig.load()
    profile = cfg.active_profile()
    if profile is None:
        print("账户列表: (空，等待添加)")
        print("账号: (空) / 密码: (空) / SSID: (空) / 登录页: 自动探测")
    else:
        pwd = profile.get_password()
        names = "、".join(p.name for p in cfg.profiles)
        print(f"账户列表: {names}（当前：{profile.name}）")
        print(
            f"账号: {profile.username or '(空)'} / 密码: "
            f"{'已保存(可解密)' if pwd else '(空)'} / SSID: {profile.ssid or '(空)'} / "
            f"登录页: {profile.portal_url or '自动探测'}"
        )
    save_ico(Path("assets/app.ico"))
    print("图标: assets/app.ico 已生成")
    wifi = get_wifi_info()
    print(f"WiFi: connected={wifi.connected} ssid={wifi.ssid!r} signal={wifi.signal} mac={wifi.mac}")
    print(f"本机IP: {get_local_ipv4()}")
    probe = check_connectivity()
    print(f"联网检测: online={probe.online} status={probe.status} portal={probe.portal_url} detail={probe.detail}")
    if parse_url:
        import requests

        session = requests.Session()
        session.trust_env = False
        try:
            form = fetch_portal_form(session, parse_url)
        except PortalError as exc:
            print(f"认证页解析失败: {exc}")
            return 2
        print(f"认证页标题: {form.title}")
        print(f"提交地址: {form.action_url}")
        print(f"用户字段: {form.username_field} / 密码字段: {form.password_field}")
        print(f"隐藏字段: {form.fields}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="校园网自动登录")
    parser.add_argument("--hidden", action="store_true", help="启动后隐藏到托盘")
    parser.add_argument("--no-tray", action="store_true", help="不启用托盘（调试用）")
    parser.add_argument("--exit-after", type=float, default=0, help="N 秒后自动退出（调试用）")
    parser.add_argument("--selftest", action="store_true", help="运行自检后退出")
    parser.add_argument("--parse-url", default=None, help="自检时解析指定认证页")
    parser.add_argument(
        "--apply-update",
        nargs=2,
        metavar=("TARGET", "PID"),
        default=None,
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args()

    if args.apply_update:
        from app.updater import run_apply_update

        return run_apply_update(args.apply_update[0], args.apply_update[1])

    if args.selftest:
        return run_selftest(args.parse_url)

    enable_dpi_awareness()
    APP_DIR.mkdir(parents=True, exist_ok=True)

    events: "queue.Queue[dict]" = queue.Queue()
    setup_logging(APP_DIR, events)

    from app.updater import cleanup_update_cache

    cleanup_update_cache()

    cfg = AppConfig.load()

    guard: SingleInstanceGuard | None = None
    if not args.no_tray:
        guard = SingleInstanceGuard(events)
        if not guard.acquire():
            log.info("已有实例在运行，已通知其显示窗口")
            return 0
    if ctypes.windll.shell32.IsUserAnAdmin():
        log.info("当前以管理员身份运行")

    use_tray = not args.no_tray
    start_hidden = bool(args.hidden and use_tray)

    window = MainWindow(
        cfg,
        events,
        start_hidden=start_hidden,
        use_tray=use_tray,
        exit_after=args.exit_after,
    )
    try:
        window.run()
    finally:
        if guard is not None:
            guard.release()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001
        logging.getLogger("campus").exception("程序启动失败")
        try:
            messagebox.showerror(APP_TITLE, "程序启动失败，详情请查看日志：\n" + str(APP_DIR / "app.log"))
        except Exception:  # noqa: BLE001
            pass
        raise