"""日志：文件滚动日志 + 转发到界面。"""

from __future__ import annotations

import logging
import queue
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

SUCCESS_LEVEL = 25
logging.addLevelName(SUCCESS_LEVEL, "SUCCESS")

LOGGER_NAME = "campus"


def get_logger() -> logging.Logger:
    return logging.getLogger(LOGGER_NAME)


def log_success(message: str, *args: object) -> None:
    get_logger().log(SUCCESS_LEVEL, message, *args)


class UiLogHandler(logging.Handler):
    """把日志投递到 Tkinter 主线程消费的队列。"""

    def __init__(self, target: "queue.Queue[dict]") -> None:
        super().__init__()
        self._target = target

    def emit(self, record: logging.LogRecord) -> None:
        try:
            if record.levelno >= logging.ERROR:
                level = "ERROR"
            elif record.levelno >= logging.WARNING:
                level = "WARNING"
            elif record.levelno >= SUCCESS_LEVEL:
                level = "SUCCESS"
            else:
                level = "INFO"
            self._target.put(
                {
                    "type": "log",
                    "level": level,
                    "message": record.getMessage(),
                    "time": self.formatter.formatTime(record, "%H:%M:%S") if self.formatter else "",
                }
            )
        except Exception:  # noqa: BLE001
            pass


def setup_logging(data_dir: Path, ui_queue: "queue.Queue[dict] | None" = None) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    for handler in list(logger.handlers):
        logger.removeHandler(handler)

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S")

    file_handler = RotatingFileHandler(
        data_dir / "app.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    if ui_queue is not None:
        ui_handler = UiLogHandler(ui_queue)
        ui_handler.setFormatter(fmt)
        logger.addHandler(ui_handler)

    # 源码运行时方便在终端查看
    if sys.stderr is not None and getattr(sys.stderr, "isatty", lambda: False)():
        console = logging.StreamHandler()
        console.setFormatter(fmt)
        logger.addHandler(console)

    logging.getLogger("urllib3").setLevel(logging.WARNING)