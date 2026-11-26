"""DPI / 分辨率检测与界面缩放。

自动比例按物理工作区相对 1920×1080 等比缩放，使窗口保持约 32% 的屏幕宽度：
- 1920×1080 → 100%，与旧版大小完全一致；
- 2560×1600 → 约 133%；
- 3840×2160 → 约 193%；
- DPI 只影响 Tk 的基础点缩放，不再重复乘进窗口比例；
- 手动比例仍可覆盖自动值，重启后生效。
"""

from __future__ import annotations

import ctypes
import tkinter as tk
from dataclasses import dataclass
from tkinter import ttk

MIN_SCALE = 1.0
MAX_SCALE = 2.5
MAX_AUTO_SCALE = 2.5

BASE_WIDTH = 620
BASE_MAX_HEIGHT = 900
MIN_WINDOW_WIDTH = 560
MIN_VIEWPORT_HEIGHT = 520

_AUTO_WIDTH_REFERENCE = 1920.0
_AUTO_HEIGHT_REFERENCE = 1080.0

_current_scale = 1.0


class _Rect(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


@dataclass(frozen=True)
class ScreenMetrics:
    screen_w: int
    screen_h: int
    work_left: int
    work_top: int
    work_w: int
    work_h: int
    dpi: int


def get_screen_metrics() -> ScreenMetrics:
    """读取主屏分辨率、工作区和系统 DPI（调用方需先设置 DPI awareness）。"""
    user32 = ctypes.windll.user32
    rect = _Rect()
    ok = False
    try:
        ok = bool(
            user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0)
        )
    except Exception:  # noqa: BLE001
        ok = False

    if ok:
        work_left, work_top = rect.left, rect.top
        work_w = max(1, rect.right - rect.left)
        work_h = max(1, rect.bottom - rect.top)
    else:
        work_left = work_top = 0
        work_w = max(1, int(user32.GetSystemMetrics(0)))
        work_h = max(1, int(user32.GetSystemMetrics(1)) - 48)

    dpi = 96
    try:
        dpi = int(user32.GetDpiForSystem())
    except Exception:  # noqa: BLE001
        try:
            hdc = user32.GetDC(0)
            try:
                dpi = int(ctypes.windll.gdi32.GetDeviceCaps(hdc, 88))
            finally:
                user32.ReleaseDC(0, hdc)
        except Exception:  # noqa: BLE001
            dpi = 96
    if not 72 <= dpi <= 480:
        dpi = 96

    screen_w = max(1, int(user32.GetSystemMetrics(0)))
    screen_h = max(1, int(user32.GetSystemMetrics(1)))
    return ScreenMetrics(
        screen_w=screen_w,
        screen_h=screen_h,
        work_left=work_left,
        work_top=work_top,
        work_w=work_w,
        work_h=work_h,
        dpi=dpi,
    )


def compute_auto_scale(metrics: ScreenMetrics) -> float:
    """自动比例：以 1920×1080 为旧版基准，按物理分辨率等比放大。

    1920×1080 返回 1.0，2560×1600 约 1.33，3840×2160 约 1.93。
    DPI 只影响 Tk 的基础点缩放，不在这里重复计算。
    """
    resolution_scale = min(
        metrics.work_w / _AUTO_WIDTH_REFERENCE,
        metrics.work_h / _AUTO_HEIGHT_REFERENCE,
    )
    scale = max(MIN_SCALE, min(MAX_AUTO_SCALE, resolution_scale))
    return round(scale, 2)


def resolve_scale(mode: str, manual: float, metrics: ScreenMetrics) -> float:
    """根据配置决定实际比例：手动值有效则使用，否则回退自动。"""
    if mode == "manual":
        try:
            value = float(manual)
        except (TypeError, ValueError):
            value = 0.0
        if MIN_SCALE <= value <= MAX_SCALE:
            return round(value, 2)
    return compute_auto_scale(metrics)


def set_current_scale(scale: float) -> None:
    global _current_scale
    _current_scale = max(MIN_SCALE, min(MAX_SCALE, float(scale)))


def current_scale() -> float:
    return _current_scale


def px(value: float) -> int:
    """把一个按 100% 设计的像素值换算为当前缩放下的大小。"""
    return int(round(float(value) * _current_scale))


def apply_tk_scaling(root: tk.Misc, metrics: ScreenMetrics, scale: float) -> None:
    """设置 Tk 的点缩放，让所有以 pt 定义的字号一起放大。"""
    set_current_scale(scale)
    try:
        root.tk.call("tk", "scaling", (metrics.dpi / 72.0) * scale)
    except tk.TclError:
        pass


def place_window(root: tk.Misc, metrics: ScreenMetrics, scale: float) -> tuple[int, int]:
    """按缩放比例设置窗口尺寸、最小尺寸并居中，返回 (宽, 高)。"""
    frame_w = px(18)
    frame_h = px(39)
    available_w = max(320, metrics.work_w - frame_w - px(16))
    available_h = max(420, metrics.work_h - frame_h - px(8))

    width = min(int(round(BASE_WIDTH * scale)), available_w)
    height = min(int(round(BASE_MAX_HEIGHT * scale)), available_h)

    x = metrics.work_left + max(0, (metrics.work_w - (width + frame_w)) // 2)
    y = metrics.work_top + max(0, (metrics.work_h - (height + frame_h)) // 2)
    try:
        root.geometry(f"{width}x{height}+{x}+{y}")
        min_w = min(int(round(MIN_WINDOW_WIDTH * scale)), width)
        min_h = min(int(round(MIN_VIEWPORT_HEIGHT * scale)), height)
        root.minsize(min_w, min_h)
    except tk.TclError:
        pass
    return width, height


def _scale_number(value: object) -> object:
    try:
        return int(round(float(value) * _current_scale))
    except (TypeError, ValueError):
        return value


def _scale_padding(value: object) -> object:
    if isinstance(value, (tuple, list)):
        return tuple(_scale_number(item) for item in value)
    return _scale_number(value)


def _scale_placement(widget: tk.Misc) -> None:
    manager = widget.winfo_manager()
    try:
        if manager == "pack":
            widget.pack_configure(
                padx=_scale_number(widget.pack_info().get("padx", 0)),
                pady=_scale_number(widget.pack_info().get("pady", 0)),
                ipadx=_scale_number(widget.pack_info().get("ipadx", 0)),
                ipady=_scale_number(widget.pack_info().get("ipady", 0)),
            )
        elif manager == "grid":
            info = widget.grid_info()
            widget.grid_configure(
                padx=_scale_padding(info.get("padx", 0)),
                pady=_scale_padding(info.get("pady", 0)),
                ipadx=_scale_number(info.get("ipadx", 0)),
                ipady=_scale_number(info.get("ipady", 0)),
            )
    except (tk.TclError, ValueError):
        pass


def _scale_widget_options(widget: tk.Misc) -> None:
    try:
        keys = set(widget.keys())
    except tk.TclError:
        return

    # 标签换行宽度
    if "wraplength" in keys:
        try:
            wrap = int(widget.cget("wraplength"))
            if wrap > 0:
                widget.configure(wraplength=px(wrap))
        except (tk.TclError, ValueError):
            pass

    # tk 控件内部的左右/上下留白（按钮、标签、文本等）
    if not isinstance(widget, (ttk.Widget, tk.Toplevel, tk.Tk)):
        for option in ("padx", "pady"):
            if option not in keys:
                continue
            try:
                value = int(widget.cget(option))
            except (tk.TclError, ValueError):
                continue
            if value > 0:
                try:
                    widget.configure(**{option: px(value)})
                except tk.TclError:
                    pass

    # 文本区内部的额外留白
    if isinstance(widget, tk.Text):
        for option in ("spacing1", "spacing2", "spacing3"):
            if option not in keys:
                continue
            try:
                value = int(widget.cget(option))
            except (tk.TclError, ValueError):
                continue
            if value > 0:
                try:
                    widget.configure(**{option: px(value)})
                except tk.TclError:
                    pass


def scale_widget_tree(root: tk.Misc) -> None:
    """把已经建好的控件树按当前比例缩放一次（只在启动时调用）。"""

    def walk(widget: tk.Misc) -> None:
        for child in widget.winfo_children():
            _scale_placement(child)
            _scale_widget_options(child)
            walk(child)

    walk(root)
    try:
        root.update_idletasks()
    except tk.TclError:
        pass


def manual_options() -> list[tuple[str, str, float]]:
    """设置下拉框的 (显示文本, 配置值, 比例) 列表。"""
    return [
        ("自动", "auto", 0.0),
        ("100%", "manual", 1.0),
        ("125%", "manual", 1.25),
        ("150%", "manual", 1.5),
        ("175%", "manual", 1.75),
        ("200%", "manual", 2.0),
        ("250%", "manual", 2.5),
    ]