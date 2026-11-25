"""程序图标生成。

运行时不依赖外部图片资源：窗口图标 / 托盘图标都由 Pillow 现场绘制，
因此单文件 exe 里不需要打包图片（exe 自身的图标在构建时生成）。
"""

from __future__ import annotations

import math
import os
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

# 状态色
COLOR_ONLINE = "#34d399"     # 在线
COLOR_CAPTIVE = "#fbbf24"    # 需要登录
COLOR_WORKING = "#60a5fa"    # 登录中
COLOR_ERROR = "#f87171"      # 错误
COLOR_OFFLINE = "#94a3b8"    # 未连接 / 暂停

_BG_TOP = (37, 99, 235)      # #2563eb
_BG_BOTTOM = (14, 165, 233)  # #0ea5e9


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def _gradient(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size))
    draw = ImageDraw.Draw(img)
    for y in range(size):
        t = y / max(size - 1, 1)
        color = tuple(
            int(_BG_TOP[i] + (_BG_BOTTOM[i] - _BG_TOP[i]) * t) for i in range(3)
        )
        draw.line([(0, y), (size, y)], fill=color + (255,))
    return img


def draw_app_icon(size: int = 256, status: str | None = None) -> Image.Image:
    """绘制圆角方形 + WiFi 波纹图标。status 存在时在右下角画状态点。"""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    radius = int(size * 0.24)

    # 圆角遮罩
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, size - 1, size - 1], radius=radius, fill=255
    )
    img.paste(_gradient(size), (0, 0), mask)

    # 顶部柔和高光
    glow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(glow).rounded_rectangle(
        [int(size * 0.08), int(size * 0.06), int(size * 0.92), int(size * 0.52)],
        radius=int(size * 0.2),
        fill=(255, 255, 255, 26),
    )
    glow = glow.filter(ImageFilter.GaussianBlur(size * 0.03))
    img = Image.alpha_composite(img, Image.composite(glow, Image.new("RGBA", (size, size), (0, 0, 0, 0)), mask))

    draw = ImageDraw.Draw(img)
    cx = size / 2
    cy = size * 0.72
    stroke = max(2, int(size * 0.062))
    dot_r = size * 0.058

    # 三段圆弧（WiFi 波纹）
    for ratio in (0.16, 0.28, 0.40):
        r = size * ratio
        draw.arc(
            [cx - r, cy - r, cx + r, cy + r],
            start=225,
            end=315,
            fill=(255, 255, 255, 255),
            width=stroke,
        )
    # 波纹底部圆点
    draw.ellipse(
        [cx - dot_r, cy - dot_r, cx + dot_r, cy + dot_r],
        fill=(255, 255, 255, 255),
    )

    if status:
        badge_r = size * 0.155
        bx = size - badge_r - size * 0.045
        by = size - badge_r - size * 0.045
        ring = max(2, int(size * 0.025))
        # 先画一圈背景色描边，避免和图标糊在一起
        draw.ellipse(
            [bx - badge_r - ring, by - badge_r - ring, bx + badge_r + ring, by + badge_r + ring],
            fill=(15, 23, 42, 255),
        )
        draw.ellipse(
            [bx - badge_r, by - badge_r, bx + badge_r, by + badge_r],
            fill=_hex_to_rgb(status) + (255,),
        )

    # 裁掉遮罩外的抗锯齿边缘（保持透明）
    return img


def save_ico(path: Path, size: int = 256) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    img = draw_app_icon(size)
    img.save(
        path,
        format="ICO",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )


def ensure_window_icon(data_dir: Path) -> Path:
    """生成（或复用）窗口用 .ico，返回路径。"""
    ico = data_dir / "app.ico"
    try:
        if not ico.exists() or ico.stat().st_size < 1024:
            save_ico(ico)
    except OSError:
        pass
    return ico


def tray_icon_image(status: str | None = None) -> Image.Image:
    """托盘用小图，尺寸大一些由系统缩放。"""
    return draw_app_icon(64, status)


def spin_angle_for(state: str) -> int:
    """给界面上的圆形指示器用。"""
    return int(math.copysign(1, 1)) * 0