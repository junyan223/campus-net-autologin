"""单实例控制：通过命名事件唤起已运行的窗口。"""

from __future__ import annotations

import ctypes
import logging
import queue
import threading
from ctypes import wintypes

from .config import APP_NAME

log = logging.getLogger("campus")

EVENT_NAME = f"Local\\{APP_NAME}_Show"
EVENT_MODIFY_STATE = 0x0002
WAIT_OBJECT_0 = 0x00000000
WAIT_TIMEOUT = 0x00000102
ERROR_ALREADY_EXISTS = 183


class SingleInstanceGuard:
    """第一个实例创建事件并等待；后续实例 SetEvent 让它弹回主窗口。"""

    def __init__(self, events: "queue.Queue[dict]") -> None:
        self._events = events
        self._stop = threading.Event()
        self._handle: int | None = None
        self._thread: threading.Thread | None = None

        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._kernel32.CreateEventW.argtypes = [
            ctypes.c_void_p,
            wintypes.BOOL,
            wintypes.BOOL,
            wintypes.LPCWSTR,
        ]
        self._kernel32.CreateEventW.restype = wintypes.HANDLE
        self._kernel32.OpenEventW.argtypes = [
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.LPCWSTR,
        ]
        self._kernel32.OpenEventW.restype = wintypes.HANDLE
        self._kernel32.SetEvent.argtypes = [wintypes.HANDLE]
        self._kernel32.SetEvent.restype = wintypes.BOOL
        self._kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        self._kernel32.WaitForSingleObject.restype = wintypes.DWORD
        self._kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        self._kernel32.CloseHandle.restype = wintypes.BOOL

    def acquire(self) -> bool:
        handle = self._kernel32.CreateEventW(None, False, False, EVENT_NAME)
        if not handle:
            log.warning("创建单实例事件失败，按允许运行处理")
            return True
        if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
            self._wake_existing()
            self._kernel32.CloseHandle(handle)
            return False
        self._handle = handle
        self._thread = threading.Thread(
            target=self._wait_loop, name="campus-instance-guard", daemon=True
        )
        self._thread.start()
        return True

    def _wake_existing(self) -> None:
        handle = self._kernel32.OpenEventW(EVENT_MODIFY_STATE, False, EVENT_NAME)
        if handle:
            self._kernel32.SetEvent(handle)
            self._kernel32.CloseHandle(handle)
        else:
            log.warning("无法通知已运行的实例（事件打开失败）")

    def _wait_loop(self) -> None:
        while not self._stop.is_set() and self._handle:
            result = self._kernel32.WaitForSingleObject(self._handle, 500)
            if result == WAIT_OBJECT_0 and not self._stop.is_set():
                log.info("收到外部启动请求，正在显示主窗口")
                self._events.put({"type": "action", "action": "show"})
            elif result not in (WAIT_OBJECT_0, WAIT_TIMEOUT):
                log.warning("单实例事件等待异常：%s", result)
                return

    def release(self) -> None:
        self._stop.set()
        if self._handle:
            self._kernel32.SetEvent(self._handle)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        if self._handle:
            self._kernel32.CloseHandle(self._handle)
            self._handle = None