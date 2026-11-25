"""高优先级开机自启动：登录时计划任务（注册需要管理员授权）。"""

from __future__ import annotations

import ctypes
import logging
import os
import subprocess
import sys
import tempfile
from ctypes import wintypes
from pathlib import Path
from xml.sax.saxutils import escape

from . import APP_TITLE

log = logging.getLogger("campus")

TASK_NAME = "CampusNetAutoLogin_HighPriority"
CREATE_NO_WINDOW = 0x08000000
SEE_MASK_NOCLOSEPROCESS = 0x00000040
WAIT_TIMEOUT = 0x00000102
ERROR_CANCELLED = 1223


class _ShellExecuteInfo(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("fMask", wintypes.ULONG),
        ("hwnd", wintypes.HWND),
        ("lpVerb", wintypes.LPCWSTR),
        ("lpFile", wintypes.LPCWSTR),
        ("lpParameters", wintypes.LPCWSTR),
        ("lpDirectory", wintypes.LPCWSTR),
        ("nShow", ctypes.c_int),
        ("hInstApp", wintypes.HINSTANCE),
        ("lpIDList", wintypes.LPVOID),
        ("lpClass", wintypes.LPCWSTR),
        ("hkeyClass", wintypes.HKEY),
        ("dwHotKey", wintypes.DWORD),
        ("hIcon", wintypes.HANDLE),
        ("hProcess", wintypes.HANDLE),
    ]


_shell32 = ctypes.WinDLL("shell32", use_last_error=True)
_shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(_ShellExecuteInfo)]
_shell32.ShellExecuteExW.restype = wintypes.BOOL

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
_kernel32.WaitForSingleObject.restype = wintypes.DWORD
_kernel32.GetExitCodeProcess.argtypes = [
    wintypes.HANDLE,
    ctypes.POINTER(wintypes.DWORD),
]
_kernel32.GetExitCodeProcess.restype = wintypes.BOOL
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
_kernel32.CloseHandle.restype = wintypes.BOOL


def _decode_console(raw: bytes) -> str:
    for enc in ("gbk", "utf-8"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("gbk", errors="replace")


def _current_user() -> str:
    domain = os.environ.get("USERDOMAIN") or ""
    user = os.environ.get("USERNAME") or ""
    return f"{domain}\\{user}" if domain and user else user


def _source_entry() -> Path:
    candidate = Path(sys.argv[0])
    if candidate.suffix.lower() == ".py" and candidate.exists():
        return candidate.resolve()
    return Path(__file__).resolve().parents[1] / "main.py"


def _app_launch_spec() -> tuple[str, str]:
    if getattr(sys, "frozen", False):
        return sys.executable, "--hidden"
    script = _source_entry()
    return sys.executable, subprocess.list2cmdline([str(script), "--hidden"])


def _helper_spec(enable: bool) -> tuple[str, str]:
    flag = "--high-priority-task=enable" if enable else "--high-priority-task=disable"
    if getattr(sys, "frozen", False):
        return sys.executable, flag
    script = _source_entry()
    return sys.executable, subprocess.list2cmdline([str(script), flag])


def _task_xml() -> str:
    command, arguments = _app_launch_spec()
    user = escape(_current_user())
    command_xml = escape(command)
    arguments_xml = escape(arguments)
    description = escape(f"{APP_TITLE} 高优先级启动")
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>{description}</Description>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <UserId>{user}</UserId>
    </LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{user}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>4</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{command_xml}</Command>
      <Arguments>{arguments_xml}</Arguments>
    </Exec>
  </Actions>
</Task>
"""


def high_priority_task_exists() -> bool | None:
    try:
        proc = subprocess.run(
            ["schtasks", "/Query", "/TN", TASK_NAME],
            creationflags=CREATE_NO_WINDOW,
            capture_output=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode == 0:
        return True
    text = _decode_console(proc.stdout + proc.stderr).casefold()
    if "拒绝访问" in text or "access is denied" in text or "access denied" in text:
        return None
    return False


def _install_high_priority_task() -> tuple[bool, str]:
    xml = _task_xml()
    tmp_path = ""
    try:
        with tempfile.NamedTemporaryFile(
            "w", suffix=".xml", prefix="campus_task_", delete=False, encoding="utf-16"
        ) as handle:
            handle.write(xml)
            tmp_path = handle.name
        proc = subprocess.run(
            [
                "schtasks",
                "/Create",
                "/F",
                "/TN",
                TASK_NAME,
                "/XML",
                tmp_path,
            ],
            creationflags=CREATE_NO_WINDOW,
            capture_output=True,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"{type(exc).__name__}: {exc}"
    finally:
        if tmp_path:
            try:
                Path(tmp_path).unlink(missing_ok=True)
            except OSError:
                pass
    if proc.returncode == 0:
        return True, ""
    detail = _decode_console(proc.stdout + proc.stderr).strip()
    return False, detail or f"schtasks 返回 {proc.returncode}"


def _remove_high_priority_task() -> tuple[bool, str]:
    if high_priority_task_exists() is False:
        return True, ""
    try:
        proc = subprocess.run(
            ["schtasks", "/Delete", "/F", "/TN", TASK_NAME],
            creationflags=CREATE_NO_WINDOW,
            capture_output=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"{type(exc).__name__}: {exc}"
    if proc.returncode == 0:
        return True, ""
    detail = _decode_console(proc.stdout + proc.stderr).strip()
    return False, detail or f"schtasks 返回 {proc.returncode}"


def _is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:  # noqa: BLE001
        return False


def _run_elevated(exe: str, params: str) -> tuple[int | None, str]:
    info = _ShellExecuteInfo()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = SEE_MASK_NOCLOSEPROCESS
    info.lpVerb = "runas"
    info.lpFile = exe
    info.lpParameters = params
    info.nShow = 0
    if not _shell32.ShellExecuteExW(ctypes.byref(info)):
        error = ctypes.get_last_error()
        if error == ERROR_CANCELLED:
            return None, "已取消管理员授权"
        return None, f"无法请求管理员权限（错误 {error}）"
    try:
        if _kernel32.WaitForSingleObject(info.hProcess, 120000) == WAIT_TIMEOUT:
            return None, "管理员操作等待超时"
        code = wintypes.DWORD()
        if not _kernel32.GetExitCodeProcess(info.hProcess, ctypes.byref(code)):
            return None, "无法读取管理员操作结果"
        return int(code.value), ""
    finally:
        if info.hProcess:
            _kernel32.CloseHandle(info.hProcess)


def apply_high_priority_change(enable: bool) -> tuple[bool, str]:
    """注册/移除高优先级计划任务；非管理员会触发一次 UAC。"""
    if _is_admin():
        ok, detail = (
            _install_high_priority_task() if enable else _remove_high_priority_task()
        )
    else:
        exe, params = _helper_spec(enable)
        code, error = _run_elevated(exe, params)
        if code is None:
            return False, error
        ok = code == 0
        detail = "" if ok else "管理员操作未完成，请查看日志"
    if not ok:
        return False, detail or "计划任务操作失败"
    exists = high_priority_task_exists()
    if exists is not None and exists != enable:
        return False, "计划任务状态没有生效"
    return True, ""


def run_high_priority_action(enable: bool) -> int:
    """在提权后的辅助进程里执行计划任务注册/删除。"""
    if not _is_admin():
        log.error("高优先级启动设置需要管理员权限")
        return 2
    ok, detail = (
        _install_high_priority_task() if enable else _remove_high_priority_task()
    )
    if ok:
        log.info("高优先级启动计划任务已%s", "创建" if enable else "删除")
        return 0
    log.error("高优先级启动计划任务操作失败：%s", detail)
    return 1