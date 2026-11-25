"""WiFi 状态读取与联网/认证检测。"""

from __future__ import annotations

import ipaddress
import re
import socket
import subprocess
import uuid
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import requests

CREATE_NO_WINDOW = 0x08000000

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36"
)

HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.6",
    "Cache-Control": "no-cache",
}

PROBE_URLS = (
    "http://www.msftconnecttest.com/redirect",
    "http://connect.rom.miui.com/generate_204",
    "http://detectportal.firefox.com/success.txt",
)

_PORTAL_KEYWORDS = (
    "portal",
    "fradius",
    "login",
    "auth",
    "captive",
    "wlan",
    "srun",
    "drcom",
    "eportal",
    "webauth",
    "redirect",
)


@dataclass
class WifiInfo:
    connected: bool
    ssid: str = ""
    signal: int | None = None
    profile: str = ""
    mac: str = ""


@dataclass
class ProbeResult:
    online: bool
    status: str  # online / captive / offline / unknown
    portal_url: str | None
    detail: str


def _decode_netsh(raw: bytes) -> str:
    # netsh 通过管道输出时使用系统 OEM 编码；中文 Windows 是 GBK
    for enc in ("gbk", "utf-8"):
        try:
            text = raw.decode(enc)
        except UnicodeDecodeError:
            continue
        if "SSID" in text or "无线" in text:
            return text
    return raw.decode("gbk", errors="replace")


def _parse_interface_blocks(text: str) -> list[dict[str, str]]:
    blocks: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for line in text.splitlines():
        if not line.strip():
            if current:
                blocks.append(current)
                current = {}
            continue
        if re.match(r"^\s*(名称|Name)\s*:", line) and current.get("ssid"):
            blocks.append(current)
            current = {}
        m = re.match(r"^\s*(SSID)\s*:\s*(.+?)\s*$", line)
        if m:
            current["ssid"] = m.group(2)
            continue
        m = re.match(r"^\s*(状态|State)\s*:\s*(.+?)\s*$", line)
        if m:
            current["state"] = m.group(2)
            continue
        m = re.match(r"^\s*(信号|Signal)\s*:\s*(\d+)%", line)
        if m:
            current["signal"] = m.group(2)
            continue
        m = re.match(r"^\s*(配置文件|Profile)\s*:\s*(.+?)\s*$", line)
        if m:
            current["profile"] = m.group(2)
            continue
        m = re.match(
            r"^\s*(物理地址|物理地址\(MAC\)|Physical address)\s*:\s*([0-9a-fA-F:\-]{11,})\s*$",
            line,
        )
        if m:
            current["mac"] = m.group(2).replace("-", ":").lower()
            continue
    if current:
        blocks.append(current)
    return blocks


def get_wifi_info() -> WifiInfo:
    try:
        raw = subprocess.check_output(
            ["netsh", "wlan", "show", "interfaces"],
            creationflags=CREATE_NO_WINDOW,
            timeout=6,
        )
    except (OSError, subprocess.SubprocessError):
        return WifiInfo(False)

    text = _decode_netsh(raw)
    for block in _parse_interface_blocks(text):
        ssid = block.get("ssid", "").strip()
        state = block.get("state", "").strip()
        connected = bool(ssid) and (
            "已连接" in state or state.lower() in ("connected", "已连接")
        )
        if connected:
            signal_raw = block.get("signal")
            return WifiInfo(
                True,
                ssid=ssid,
                signal=int(signal_raw) if signal_raw and signal_raw.isdigit() else None,
                profile=block.get("profile", "").strip(),
                mac=block.get("mac", "").strip(),
            )
    return WifiInfo(False)


def get_local_ipv4(target: str = "8.8.8.8") -> str:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect((target or "8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        pass
    finally:
        sock.close()
    try:
        return socket.gethostbyname(socket.gethostname())
    except OSError:
        return ""


def get_local_mac() -> str:
    try:
        node = uuid.getnode()
        if (node >> 40) & 0x01:  # 多播位 -> 可能不是真实 MAC
            return ""
        return ":".join(f"{(node >> shift) & 0xFF:02x}" for shift in range(40, -8, -8))
    except Exception:  # noqa: BLE001
        return ""


def list_wifi_networks() -> list[str]:
    """列出附近扫描到的 + 系统已保存的 WiFi 名称，供账户选择。"""
    found: dict[str, str] = {}

    def add(value: str) -> None:
        value = value.strip()
        if value:
            found.setdefault(value.casefold(), value)

    try:
        raw = subprocess.check_output(
            ["netsh", "wlan", "show", "networks"],
            creationflags=CREATE_NO_WINDOW,
            timeout=8,
        )
        for line in _decode_netsh(raw).splitlines():
            match = re.match(r"^\s*SSID\s+\d+\s*:\s*(.+?)\s*$", line)
            if match:
                add(match.group(1))
    except (OSError, subprocess.SubprocessError):
        pass

    try:
        raw = subprocess.check_output(
            ["netsh", "wlan", "show", "profiles"],
            creationflags=CREATE_NO_WINDOW,
            timeout=6,
        )
        for line in _decode_netsh(raw).splitlines():
            match = re.match(
                r"^\s*(?:所有用户配置文件|All User Profile)\s*:\s*(.+?)\s*$", line
            )
            if match:
                add(match.group(1))
    except (OSError, subprocess.SubprocessError):
        pass

    add(get_wifi_info().ssid)
    return sorted(found.values(), key=str.casefold)


def get_wifi_profile_auto_connect(ssid: str) -> bool | None:
    """读取系统无线配置里的“自动连接”状态；配置不存在时返回 None。"""
    ssid = (ssid or "").strip()
    if not ssid:
        return None
    try:
        raw = subprocess.check_output(
            ["netsh", "wlan", "show", "profile", f"name={ssid}"],
            creationflags=CREATE_NO_WINDOW,
            timeout=6,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    text = _decode_netsh(raw)
    match = re.search(
        r"^\s*(?:连接模式|Connection mode)\s*[:：]\s*(.+?)\s*$",
        text,
        re.MULTILINE | re.IGNORECASE,
    )
    if not match:
        return None
    value = match.group(1).strip().casefold()
    if "auto" in value or "自动" in value:
        return True
    if "manual" in value or "手动" in value:
        return False
    return None


def set_wifi_profile_auto_connect(ssid: str, enabled: bool) -> tuple[bool, str]:
    """修改系统无线配置的自动连接开关。"""
    ssid = (ssid or "").strip()
    if not ssid:
        return False, "WiFi 名称为空"
    mode = "auto" if enabled else "manual"
    try:
        proc = subprocess.run(
            [
                "netsh",
                "wlan",
                "set",
                "profileparameter",
                f"name={ssid}",
                f"connectionmode={mode}",
            ],
            creationflags=CREATE_NO_WINDOW,
            capture_output=True,
            timeout=8,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"{type(exc).__name__}: {exc}"
    if proc.returncode == 0:
        return True, ""
    detail = _decode_netsh(proc.stdout + proc.stderr).strip()
    return False, detail or f"netsh 返回 {proc.returncode}"


def _is_private_host(host: str) -> bool:
    if not host:
        return False
    try:
        return ipaddress.ip_address(host).is_private or ipaddress.ip_address(host).is_link_local
    except ValueError:
        return host.endswith(".local") or host == "localhost"


def looks_like_portal_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    host = parsed.hostname or ""
    if _is_private_host(host):
        return True
    lowered = url.lower()
    return any(keyword in lowered for keyword in _PORTAL_KEYWORDS)


def _is_online_response(url: str, response: requests.Response) -> bool:
    lowered = url.lower()
    if "generate_204" in lowered:
        return response.status_code == 204
    if "connecttest.txt" in lowered:
        return response.status_code == 200 and "Microsoft Connect Test" in response.text[:4096]
    if "success.txt" in lowered:
        return (
            response.status_code == 200
            and response.text[:4096].strip().lower().startswith("success")
        )
    return False


_META_REFRESH_RE = re.compile(
    r'<meta[^>]+http-equiv=["\']?refresh["\']?[^>]*content=["\'][^"\']*?url=([^"\'>\s]+)',
    re.IGNORECASE,
)
_JS_REDIRECT_RE = re.compile(
    r'(?:window\.)?location(?:\.href)?\s*=\s*["\']([^"\']+)["\']', re.IGNORECASE
)
_HAS_FORM_RE = re.compile(r"<form\b", re.IGNORECASE)
_HAS_PASSWORD_RE = re.compile(r'<input[^>]+type=["\']?password', re.IGNORECASE)


def _portal_from_response(response: requests.Response) -> str | None:
    """从返回的 HTML 里找认证页地址或识别认证页本身。"""
    text = response.text[:30000]
    for pattern in (_META_REFRESH_RE, _JS_REDIRECT_RE):
        match = pattern.search(text)
        if match:
            candidate = urljoin(response.url, match.group(1).replace("&amp;", "&"))
            if looks_like_portal_url(candidate):
                return candidate
    host = urlparse(response.url).hostname or ""
    if _HAS_FORM_RE.search(text) and _HAS_PASSWORD_RE.search(text) and _is_private_host(host):
        # 探针请求被网关原地替换成了认证页
        return response.url
    return None


def check_connectivity(timeout: float = 3.5) -> ProbeResult:
    """检测互联网是否可用；需要认证时返回 portal 地址。"""
    session = requests.Session()
    session.trust_env = False  # 避免系统代理/VPN 干扰局域网的认证页
    notes: list[str] = []
    had_http_response = False
    had_connection_error = False

    for url in PROBE_URLS:
        try:
            response = session.get(
                url,
                headers=HEADERS,
                timeout=(min(2.5, timeout), timeout),
                allow_redirects=False,
            )
        except requests.RequestException as exc:
            had_connection_error = True
            notes.append(f"{urlparse(url).hostname}: {type(exc).__name__}")
            continue

        had_http_response = True

        if _is_online_response(url, response):
            return ProbeResult(True, "online", None, f"{urlparse(url).hostname} 正常")

        location = response.headers.get("Location", "")
        if location:
            target = urljoin(url, location)
            if looks_like_portal_url(target):
                return ProbeResult(False, "captive", target, "检测到认证页面")
            # 正常跳转（例如 http -> https / connecttest.txt），跟随一次
            if response.status_code in (301, 302, 303, 307, 308):
                try:
                    followed = session.get(
                        target,
                        headers=HEADERS,
                        timeout=(min(2.5, timeout), timeout),
                        allow_redirects=True,
                    )
                except requests.RequestException as exc:
                    had_connection_error = True
                    notes.append(f"{urlparse(target).hostname}: {type(exc).__name__}")
                    continue
                if _is_online_response(followed.url, followed):
                    return ProbeResult(
                        True, "online", None, f"{urlparse(followed.url).hostname} 正常"
                    )
                portal = _portal_from_response(followed)
                if portal:
                    return ProbeResult(False, "captive", portal, "检测到认证页面")
                notes.append(f"{urlparse(url).hostname}: 跳转后无法识别")
                continue

        portal = _portal_from_response(response)
        if portal:
            return ProbeResult(False, "captive", portal, "检测到认证页面")
        notes.append(f"{urlparse(url).hostname}: HTTP {response.status_code}")

    status = "unknown" if had_http_response else ("offline" if had_connection_error else "unknown")
    detail = "；".join(notes) or "所有检测请求均失败"
    return ProbeResult(False, status, None, detail)