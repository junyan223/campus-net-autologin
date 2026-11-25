"""界面配色与字体。"""

from __future__ import annotations

BG = "#131519"
CARD = "#1c1f25"
CARD_HOVER = "#232730"
INPUT = "#121418"
BORDER = "#2a2f38"
TEXT = "#e9ebf0"
MUTED = "#8d95a3"
HINT = "#646c7a"
ACCENT = "#3b82f6"
ACCENT_HOVER = "#5b9bff"
SUCCESS = "#34d399"
WARN = "#fbbf24"
DANGER = "#f87171"
INFO_BLUE = "#60a5fa"
GRAY = "#94a3b8"

FONT = "Microsoft YaHei UI"
MONO = "Consolas"

STATE_COLORS = {
    "init": GRAY,
    "checking": INFO_BLUE,
    "online": SUCCESS,
    "success": SUCCESS,
    "captive": WARN,
    "logging_in": INFO_BLUE,
    "failed": DANGER,
    "no_wifi": GRAY,
    "other_wifi": GRAY,
    "need_setup": WARN,
    "network_error": DANGER,
}