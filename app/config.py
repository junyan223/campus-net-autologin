"""配置读写、多账户模型、密码加密（Windows DPAPI）与开机自启。"""

from __future__ import annotations

import base64
import ctypes
import json
import logging
import os
import sys
import uuid
from ctypes import wintypes
from dataclasses import dataclass, field
from pathlib import Path

APP_NAME = "CampusNetAutoLogin"
APP_DIR = Path(os.environ.get("APPDATA") or Path.home()) / APP_NAME
CONFIG_PATH = APP_DIR / "config.json"

AUTOSTART_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
AUTOSTART_VALUE = APP_NAME

log = logging.getLogger("campus")

# ---------------------------------------------------------------- DPAPI


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_char)),
    ]


_crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
_crypt32.CryptProtectData.argtypes = [
    ctypes.POINTER(_DataBlob),
    wintypes.LPCWSTR,
    ctypes.POINTER(_DataBlob),
    ctypes.c_void_p,
    ctypes.c_void_p,
    wintypes.DWORD,
    ctypes.POINTER(_DataBlob),
]
_crypt32.CryptProtectData.restype = wintypes.BOOL
_crypt32.CryptUnprotectData.argtypes = [
    ctypes.POINTER(_DataBlob),
    ctypes.POINTER(wintypes.LPWSTR),
    ctypes.POINTER(_DataBlob),
    ctypes.c_void_p,
    ctypes.c_void_p,
    wintypes.DWORD,
    ctypes.POINTER(_DataBlob),
]
_crypt32.CryptUnprotectData.restype = wintypes.BOOL

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.LocalFree.argtypes = [ctypes.c_void_p]
_kernel32.LocalFree.restype = ctypes.c_void_p


def _blob(data: bytes) -> tuple[_DataBlob, ctypes.Array]:
    buf = ctypes.create_string_buffer(data)
    return _DataBlob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf


def _protect_dpapi(text: str) -> bytes:
    data = text.encode("utf-8")
    blob, _buf = _blob(data)
    out = _DataBlob()
    ok = _crypt32.CryptProtectData(
        ctypes.byref(blob),
        "CampusNetAutoLogin",
        None,
        None,
        None,
        0,
        ctypes.byref(out),
    )
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        if out.pbData:
            _kernel32.LocalFree(ctypes.cast(out.pbData, ctypes.c_void_p))


def _unprotect_dpapi(data: bytes) -> str:
    blob, _buf = _blob(data)
    out = _DataBlob()
    ok = _crypt32.CryptUnprotectData(
        ctypes.byref(blob),
        None,
        None,
        None,
        None,
        0,
        ctypes.byref(out),
    )
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(out.pbData, out.cbData).decode("utf-8")
    finally:
        if out.pbData:
            _kernel32.LocalFree(ctypes.cast(out.pbData, ctypes.c_void_p))


def encrypt_secret(text: str) -> str:
    if not text:
        return ""
    try:
        return "dpapi:" + base64.b64encode(_protect_dpapi(text)).decode("ascii")
    except Exception:  # noqa: BLE001
        log.error("Windows DPAPI 加密失败，密码不会被写入磁盘")
        return ""


def decrypt_secret(value: str) -> str:
    if not value:
        return ""
    try:
        if value.startswith("dpapi:"):
            return _unprotect_dpapi(base64.b64decode(value[len("dpapi:") :]))
        if value.startswith("b64:"):
            log.warning("检测到旧的不安全密码编码，建议在界面重新保存一次密码")
            return base64.b64decode(value[len("b64:") :]).decode("utf-8")
    except Exception:  # noqa: BLE001
        return ""
    return ""


# ---------------------------------------------------------------- 账户


@dataclass
class Profile:
    id: str
    name: str
    ssid: str = ""
    username: str = ""
    password_enc: str = ""
    portal_url: str = ""  # 留空 = 使用网络重定向自动探测

    @classmethod
    def create(cls, name: str) -> "Profile":
        return cls(id=uuid.uuid4().hex, name=name)

    @classmethod
    def from_dict(cls, raw: dict) -> "Profile | None":
        if not isinstance(raw, dict):
            return None
        name = str(raw.get("name") or "账户").strip() or "账户"
        return cls(
            id=str(raw.get("id") or uuid.uuid4().hex),
            name=name,
            ssid=str(raw.get("ssid") or "").strip(),
            username=str(raw.get("username") or ""),
            password_enc=str(raw.get("password_enc") or ""),
            portal_url=str(raw.get("portal_url") or "").strip(),
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "ssid": self.ssid,
            "username": self.username,
            "password_enc": self.password_enc,
            "portal_url": self.portal_url,
        }

    def set_password(self, plain: str) -> None:
        self.password_enc = encrypt_secret(plain)

    def get_password(self) -> str:
        return decrypt_secret(self.password_enc)

    def __repr__(self) -> str:
        return (
            f"Profile(id={self.id!r}, name={self.name!r}, ssid={self.ssid!r}, "
            f"username={self.username!r}, portal_url={self.portal_url!r}, "
            f"password={'***' if self.password_enc else ''})"
        )


# ---------------------------------------------------------------- 配置


@dataclass
class AppConfig:
    profiles: list[Profile] = field(default_factory=list)
    active_profile_id: str = ""
    auto_login: bool = True
    check_interval: int = 10
    start_with_windows: bool = False
    minimize_to_tray: bool = True
    notify: bool = True
    version: int = 2

    # ---- 账户
    def active_profile(self) -> Profile | None:
        for profile in self.profiles:
            if profile.id == self.active_profile_id:
                return profile
        if self.profiles:
            self.active_profile_id = self.profiles[0].id
            return self.profiles[0]
        return None

    def find_profile(self, profile_id: str) -> Profile | None:
        return next((p for p in self.profiles if p.id == profile_id), None)

    def unique_name(self, base: str, exclude_id: str = "") -> str:
        base = (base or "账户").strip() or "账户"
        taken = {p.name.casefold() for p in self.profiles if p.id != exclude_id}
        if base.casefold() not in taken:
            return base
        index = 2
        while f"{base} ({index})".casefold() in taken:
            index += 1
        return f"{base} ({index})"

    def add_profile(self, name: str) -> Profile:
        profile = Profile.create(self.unique_name(name or "新账户"))
        self.profiles.append(profile)
        return profile

    def delete_profile(self, profile_id: str) -> bool:
        if len(self.profiles) <= 1:
            return False
        remaining = [p for p in self.profiles if p.id != profile_id]
        if len(remaining) == len(self.profiles):
            return False
        self.profiles = remaining
        if self.active_profile_id == profile_id:
            self.active_profile_id = self.profiles[0].id
        return True

    # ---- 读写
    @classmethod
    def load(cls) -> "AppConfig":
        cfg = cls()
        try:
            raw = json.loads(CONFIG_PATH.read_text("utf-8"))
        except (OSError, ValueError):
            raw = {}
        if not isinstance(raw, dict):
            raw = {}

        for name in ("auto_login", "start_with_windows", "minimize_to_tray", "notify"):
            if isinstance(raw.get(name), bool):
                setattr(cfg, name, raw[name])
        try:
            cfg.check_interval = max(5, min(300, int(raw.get("check_interval", 10))))
        except (TypeError, ValueError):
            cfg.check_interval = 10

        migrated = False
        profiles_raw = raw.get("profiles")
        if isinstance(profiles_raw, list):
            seen: set[str] = set()
            for item in profiles_raw:
                profile = Profile.from_dict(item)
                if profile is None or profile.id in seen:
                    continue
                seen.add(profile.id)
                cfg.profiles.append(profile)

        # 全新安装不创建任何默认账户；仅迁移旧版单账户配置
        if not cfg.profiles:
            legacy_ssid = str(raw.get("ssid") or "").strip()
            legacy_user = str(raw.get("username") or "")
            legacy_enc = str(raw.get("password_enc") or "")
            if legacy_ssid or legacy_user or legacy_enc:
                cfg.profiles.append(
                    Profile(
                        id=uuid.uuid4().hex,
                        name=legacy_ssid or "我的账户",
                        ssid=legacy_ssid,
                        username=legacy_user,
                        password_enc=legacy_enc,
                        portal_url="",
                    )
                )
                migrated = True

        active = raw.get("active_profile_id")
        if cfg.profiles and isinstance(active, str) and any(p.id == active for p in cfg.profiles):
            cfg.active_profile_id = active
        elif cfg.profiles:
            cfg.active_profile_id = cfg.profiles[0].id
        else:
            cfg.active_profile_id = ""

        if migrated:
            try:
                cfg.save()
            except OSError:
                pass
        return cfg

    def to_dict(self) -> dict:
        return {
            "version": 2,
            "active_profile_id": self.active_profile_id,
            "profiles": [p.to_dict() for p in self.profiles],
            "auto_login": self.auto_login,
            "check_interval": self.check_interval,
            "start_with_windows": self.start_with_windows,
            "minimize_to_tray": self.minimize_to_tray,
            "notify": self.notify,
        }

    def save(self) -> None:
        APP_DIR.mkdir(parents=True, exist_ok=True)
        tmp = CONFIG_PATH.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(tmp, CONFIG_PATH)

    def __repr__(self) -> str:
        return (
            f"AppConfig(profiles={self.profiles!r}, active={self.active_profile_id!r}, "
            f"auto_login={self.auto_login!r}, interval={self.check_interval!r})"
        )


# ---------------------------------------------------------------- 开机自启


def autostart_command() -> str:
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --hidden'
    script = Path(sys.argv[0]).resolve()
    return f'"{sys.executable}" "{script}" --hidden'


def is_autostart_enabled() -> bool:
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_KEY) as key:
            value, _ = winreg.QueryValueEx(key, AUTOSTART_VALUE)
            return bool(value)
    except OSError:
        return False


def set_autostart(enabled: bool) -> None:
    import winreg

    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER, AUTOSTART_KEY, 0, winreg.KEY_SET_VALUE
    ) as key:
        if enabled:
            winreg.SetValueEx(
                key, AUTOSTART_VALUE, 0, winreg.REG_SZ, autostart_command()
            )
        else:
            try:
                winreg.DeleteValue(key, AUTOSTART_VALUE)
            except FileNotFoundError:
                pass