"""GitHub Release 更新检查、下载与自更新。

约束：
- 只检查 GitHub Release 的正式版本（latest 不包含 draft / prerelease）；
- 下载必须校验 SHA256，校验失败立即删除；
- 镜像只作为下载加速，哈希仍以 GitHub Release 为准；
- 自更新只在打包后的 exe 中启用，源码运行回退为手动下载。
"""

from __future__ import annotations

import ctypes
import hashlib
import hmac
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Callable
from urllib.parse import quote

import requests

from . import __version__

log = logging.getLogger("campus")

GITHUB_REPO = "junyan223/campus-net-autologin"
GITHUB_API = f"https://api.github.com/repos/{GITHUB_REPO}"
RELEASES_PAGE = f"https://github.com/{GITHUB_REPO}/releases"

USER_AGENT = f"CampusNetAutoLogin/{__version__}"
API_HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    "User-Agent": USER_AGENT,
}
ASSET_HEADERS = {
    "Accept": "application/octet-stream",
    "User-Agent": USER_AGENT,
}
ASSET_NAME_RE = re.compile(r"^campusnetautologin-?v?\d.*\.exe$", re.IGNORECASE)
SHA_RE = re.compile(r"(?i)sha-?256\s*[:：]?\s*`?([0-9a-f]{64})`?")

# 可插拔镜像：每一项是包含 {tag} / {name} 的 URL 模板。
# 镜像只用于下载加速，最终仍必须匹配 GitHub Release 提供的 SHA256。
MIRROR_URL_TEMPLATES: tuple[str, ...] = ()

UPDATE_DIR = Path(tempfile.gettempdir()) / "CampusNetAutoLogin-update"
DOWNLOAD_CHUNK = 64 * 1024
PROGRESS_INTERVAL = 0.15


class UpdateError(Exception):
    """更新流程中的可展示错误。"""


class UpdateCancelled(Exception):
    """用户取消了下载。"""


@dataclass(frozen=True)
class ReleaseAsset:
    name: str
    api_url: str
    browser_url: str
    size: int
    digest: str = ""


@dataclass(frozen=True)
class ReleaseInfo:
    tag: str
    version: tuple[int, ...]
    name: str
    body: str
    html_url: str
    asset: ReleaseAsset | None
    expected_sha256: str


@dataclass(frozen=True)
class CheckResult:
    current_version: str
    latest: ReleaseInfo | None = None
    error: str = ""

    @property
    def has_update(self) -> bool:
        if self.latest is None:
            return False
        return version_is_newer(self.latest.version, parse_version(self.current_version))


def parse_version(text: str) -> tuple[int, ...]:
    text = (text or "").strip()
    if text[:1].lower() == "v":
        text = text[1:]
    parts = re.findall(r"\d+", text)
    if not parts:
        return (0,)
    return tuple(int(part) for part in parts[:4])


def version_is_newer(latest: tuple[int, ...], current: tuple[int, ...]) -> bool:
    length = max(len(latest), len(current), 1)
    left = latest + (0,) * (length - len(latest))
    right = current + (0,) * (length - len(current))
    return left > right


def today_str() -> str:
    return date.today().isoformat()


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def release_page_url() -> str:
    return f"{RELEASES_PAGE}/latest"


def _pick_asset(data: dict, tag: str) -> ReleaseAsset | None:
    assets = data.get("assets")
    if not isinstance(assets, list):
        return None
    candidates: list[tuple[int, ReleaseAsset]] = []
    normalized_tag = tag.casefold().lstrip("v")
    for item in assets:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "")
        if not ASSET_NAME_RE.search(name):
            continue
        score = 1
        if normalized_tag and normalized_tag in name.casefold():
            score += 2
        digest = str(item.get("digest") or "")
        candidates.append(
            (
                score,
                ReleaseAsset(
                    name=name,
                    api_url=str(item.get("url") or ""),
                    browser_url=str(item.get("browser_download_url") or ""),
                    size=int(item.get("size") or 0),
                    digest=digest,
                ),
            )
        )
    if not candidates:
        return None
    candidates.sort(key=lambda pair: pair[0], reverse=True)
    return candidates[0][1]


def _expected_sha(asset: ReleaseAsset | None, body: str) -> str:
    if asset is not None and asset.digest.lower().startswith("sha256:"):
        value = asset.digest.split(":", 1)[1].strip().lower()
        if re.fullmatch(r"[0-9a-f]{64}", value):
            return value
    match = SHA_RE.search(body or "")
    return match.group(1).lower() if match else ""


def fetch_latest_release(timeout: float = 8.0) -> ReleaseInfo:
    url = f"{GITHUB_API}/releases/latest"
    try:
        response = requests.get(url, headers=API_HEADERS, timeout=(5.0, timeout))
    except requests.RequestException as exc:
        raise UpdateError(f"无法连接 GitHub：{exc}") from exc

    if response.status_code == 404:
        raise UpdateError("仓库还没有可用的正式版本")
    if response.status_code == 403:
        raise UpdateError("GitHub 接口访问受限，请稍后再试")
    if response.status_code >= 400:
        raise UpdateError(f"GitHub 返回 HTTP {response.status_code}")

    try:
        data = response.json()
    except ValueError as exc:
        raise UpdateError("GitHub 返回内容无法解析") from exc
    if not isinstance(data, dict):
        raise UpdateError("GitHub 返回内容格式异常")
    if data.get("draft") or data.get("prerelease"):
        raise UpdateError("最新版本暂不可用")

    tag = str(data.get("tag_name") or "")
    if not tag:
        raise UpdateError("最新版本缺少 tag")
    body = str(data.get("body") or "")
    asset = _pick_asset(data, tag)
    return ReleaseInfo(
        tag=tag,
        version=parse_version(tag),
        name=str(data.get("name") or tag),
        body=body,
        html_url=str(data.get("html_url") or RELEASES_PAGE),
        asset=asset,
        expected_sha256=_expected_sha(asset, body),
    )


def check_for_update(current_version: str) -> CheckResult:
    try:
        latest = fetch_latest_release()
    except UpdateError as exc:
        return CheckResult(current_version=current_version, error=str(exc))
    return CheckResult(current_version=current_version, latest=latest)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source_candidates(release: ReleaseInfo) -> list[tuple[str, str, dict]]:
    asset = release.asset
    if asset is None:
        return []
    candidates: list[tuple[str, str, dict]] = []
    if asset.api_url:
        candidates.append(("GitHub API", asset.api_url, dict(ASSET_HEADERS)))
    if asset.browser_url:
        candidates.append(("GitHub", asset.browser_url, {}))
    for template in MIRROR_URL_TEMPLATES:
        try:
            url = template.format(tag=quote(release.tag), name=quote(asset.name))
        except (KeyError, ValueError):
            continue
        if url:
            candidates.append(("镜像", url, {}))
    return candidates


def _probe_source(url: str, headers: dict, timeout: float = 3.0) -> float | None:
    started = time.monotonic()
    try:
        response = requests.head(
            url, headers=headers, allow_redirects=True, timeout=(2.0, timeout)
        )
        if response.status_code < 400:
            return time.monotonic() - started
    except requests.RequestException:
        return None
    return None


def _ordered_sources(release: ReleaseInfo) -> list[tuple[str, str, dict]]:
    candidates = [item for item in _source_candidates(release) if item[1]]
    if not candidates:
        return []
    scored: list[tuple[float, tuple[str, str, dict]]] = []
    for candidate in candidates:
        latency = _probe_source(candidate[1], candidate[2])
        if latency is not None:
            scored.append((latency, candidate))
    scored.sort(key=lambda item: item[0])
    ordered = [candidate for _, candidate in scored]
    for candidate in candidates:
        if candidate not in ordered:
            ordered.append(candidate)
    return ordered


def _download_one(
    label: str,
    url: str,
    headers: dict,
    release: ReleaseInfo,
    destination_dir: Path,
    progress: Callable[[int, int], None] | None,
    cancel: "threading.Event | None",
) -> Path:
    asset = release.asset
    if asset is None:
        raise UpdateError("该版本没有可下载的程序")

    part = destination_dir / f"{asset.name}.part"
    existing = part.stat().st_size if part.exists() else 0
    if asset.size and existing > asset.size:
        try:
            part.unlink()
        except OSError:
            pass
        existing = 0

    request_headers = dict(headers)
    if existing:
        request_headers["Range"] = f"bytes={existing}-"

    try:
        response = requests.get(
            url,
            headers=request_headers,
            stream=True,
            allow_redirects=True,
            timeout=(5.0, 30.0),
        )
    except requests.RequestException as exc:
        raise UpdateError(f"{label} 下载失败：{exc}") from exc

    with response:
        if response.status_code == 416:
            try:
                part.unlink()
            except OSError:
                pass
            raise UpdateError(f"{label} 的续传位置无效，已重新开始")
        if response.status_code not in (200, 206):
            raise UpdateError(f"{label} 返回 HTTP {response.status_code}")

        if response.status_code == 200 and existing:
            existing = 0
            try:
                part.unlink()
            except OSError:
                pass

        content_length = int(response.headers.get("Content-Length") or 0)
        total = (existing + content_length) if content_length else asset.size
        mode = "ab" if existing and response.status_code == 206 else "wb"
        downloaded = existing
        last_emit = 0.0
        try:
            with part.open(mode) as handle:
                for chunk in response.iter_content(DOWNLOAD_CHUNK):
                    if cancel is not None and cancel.is_set():
                        raise UpdateCancelled()
                    if not chunk:
                        continue
                    handle.write(chunk)
                    downloaded += len(chunk)
                    now = time.monotonic()
                    if progress is not None and now - last_emit >= PROGRESS_INTERVAL:
                        progress(downloaded, total)
                        last_emit = now
        except UpdateCancelled:
            try:
                part.unlink()
            except OSError:
                pass
            raise
        except OSError as exc:
            raise UpdateError(f"{label} 写入临时文件失败：{exc}") from exc
        except requests.RequestException as exc:
            raise UpdateError(f"{label} 下载中断：{exc}") from exc

    if progress is not None:
        progress(downloaded, total or downloaded)
    return part


def sha256_of(path: Path) -> str:
    return _sha256_file(path)


def download_update(
    release: ReleaseInfo,
    progress: Callable[[int, int], None] | None = None,
    cancel: "threading.Event | None" = None,
    destination_dir: Path | None = None,
) -> Path:
    if release.asset is None:
        raise UpdateError("该版本没有可下载的程序")
    if not release.expected_sha256:
        raise UpdateError("该版本没有提供 SHA256，无法校验，已停止自动更新")

    destination_dir = destination_dir or UPDATE_DIR
    destination_dir.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []
    part: Path | None = None

    for label, url, headers in _ordered_sources(release):
        if cancel is not None and cancel.is_set():
            raise UpdateCancelled()
        try:
            log.info("正在从 %s 下载更新：%s", label, release.asset.name)
            part = _download_one(
                label,
                url,
                headers,
                release,
                destination_dir,
                progress,
                cancel,
            )
            break
        except UpdateCancelled:
            raise
        except UpdateError as exc:
            errors.append(str(exc))
            log.warning("%s 下载失败：%s", label, exc)
            continue

    if part is None:
        detail = "；".join(errors[-2:]) if errors else "没有可用的下载源"
        raise UpdateError(f"下载失败：{detail}")

    actual = _sha256_file(part)
    if not hmac.compare_digest(actual, release.expected_sha256.lower()):
        try:
            part.unlink()
        except OSError:
            pass
        raise UpdateError("下载文件 SHA256 校验失败，已删除")

    final = destination_dir / release.asset.name
    try:
        final.unlink(missing_ok=True)
        os.replace(part, final)
    except OSError as exc:
        raise UpdateError(f"保存更新文件失败：{exc}") from exc
    log.info("更新下载完成并通过校验：%s", final)
    return final


def launch_apply_update(new_exe: Path) -> None:
    """启动新 exe 的 --apply-update 模式，然后由调用方退出旧进程。"""
    if not is_frozen():
        raise UpdateError("当前是源码运行模式，不支持自动替换")
    target = Path(sys.executable).resolve()
    new_exe = Path(new_exe).resolve()
    if not new_exe.exists():
        raise UpdateError("更新文件不存在")
    if new_exe == target:
        raise UpdateError("更新文件与当前程序相同")
    flags = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(
        [str(new_exe), "--apply-update", str(target), str(os.getpid())],
        cwd=str(new_exe.parent),
        close_fds=True,
        creationflags=flags,
    )


def _wait_for_pid(pid: int, timeout_ms: int = 30000) -> None:
    if pid <= 0:
        return
    kernel32 = ctypes.windll.kernel32
    kernel32.OpenProcess.restype = ctypes.c_void_p
    handle = kernel32.OpenProcess(0x00100000, False, int(pid))  # SYNCHRONIZE
    if not handle:
        time.sleep(1.0)
        return
    try:
        kernel32.WaitForSingleObject(ctypes.c_void_p(handle), timeout_ms)
    finally:
        kernel32.CloseHandle(ctypes.c_void_p(handle))


def _apply_error(message: str) -> None:
    try:
        import tkinter.messagebox as messagebox

        messagebox.showerror("校园网自动登录", message)
    except Exception:  # noqa: BLE001
        pass


def run_apply_update(target_path: str, old_pid: str) -> int:
    """新 exe 的辅助模式：等待旧进程退出，备份并替换，然后启动新程序。"""
    target = Path(target_path).resolve()
    source = Path(sys.executable).resolve()
    try:
        pid = int(old_pid)
    except (TypeError, ValueError):
        pid = 0

    if not target.exists():
        _apply_error(f"找不到要替换的程序：\n{target}")
        return 1
    if source == target:
        _apply_error("更新文件路径异常，已停止替换。")
        return 1

    _wait_for_pid(pid)
    backup = target.with_name(target.name + ".bak")
    replaced = False
    try:
        shutil.copy2(target, backup)
        last_error: Exception | None = None
        for _ in range(20):
            try:
                shutil.copy2(source, target)
                replaced = True
                break
            except OSError as exc:
                last_error = exc
                time.sleep(0.5)
        if not replaced:
            raise UpdateError(f"无法写入程序目录：{last_error}")

        flags = 0x00000008 | 0x00000200
        subprocess.Popen(
            [str(target)],
            cwd=str(target.parent),
            close_fds=True,
            creationflags=flags,
        )
        return 0
    except Exception as exc:  # noqa: BLE001
        if not replaced:
            try:
                backup.unlink(missing_ok=True)
            except OSError:
                pass
        else:
            try:
                shutil.copy2(backup, target)
            except OSError:
                pass
        _apply_error(
            "自动更新失败，已尽量恢复原程序。\n"
            f"原因：{exc}\n\n请手动从 Release 页面下载并替换。"
        )
        return 1


def cleanup_update_cache() -> None:
    """清理两天前的更新缓存，避免临时目录堆积。"""
    try:
        if not UPDATE_DIR.exists():
            return
        cutoff = time.time() - 2 * 24 * 3600
        current = Path(sys.executable).resolve() if is_frozen() else None
        for item in UPDATE_DIR.iterdir():
            try:
                if not item.is_file():
                    continue
                if current is not None and item.resolve() == current:
                    continue
                if item.stat().st_mtime >= cutoff:
                    continue
                item.unlink(missing_ok=True)
            except OSError:
                continue
    except OSError:
        pass