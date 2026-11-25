"""校园网自动登录助手。"""

import os

__version__ = "0.1"

_BLANK_CHANNEL = (os.environ.get("CAMPUSNET_CHANNEL") or "").strip().lower() == "blank"
APP_TITLE = f"校园网自动登录 v{__version__}" if _BLANK_CHANNEL else "校园网自动登录"