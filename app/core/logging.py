"""日志初始化。"""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


LOG_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"
LOG_MAX_BYTES = 50 * 1024 * 1024
LOG_BACKUP_COUNT = 10
_HANDLER_MARK = "_be_supervisor_model_handler"


def _build_formatter() -> logging.Formatter:
    return logging.Formatter(LOG_FORMAT)


def _build_stream_handler() -> logging.Handler:
    handler = logging.StreamHandler()
    handler.setFormatter(_build_formatter())
    setattr(handler, _HANDLER_MARK, True)
    return handler


def _build_file_handler(log_path: Path) -> logging.Handler:
    """日志目录不存在时主动创建，避免容器首次启动直接因为路径缺失失败。"""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        log_path,
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    handler.setFormatter(_build_formatter())
    setattr(handler, _HANDLER_MARK, True)
    return handler


def configure_logging(level: str, *, log_path: Path | None = None) -> None:
    """初始化应用日志。

    规则固定为：
    1. 始终保留 stdout 输出，方便 `docker logs` 和本地前台排障。
    2. 仅当显式配置 log_path 时，再额外写入滚动文件。
    3. 重复初始化时只替换本项目自己加的 handler，避免日志重复打印。
    """
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    for handler in list(root_logger.handlers):
        if getattr(handler, _HANDLER_MARK, False):
            root_logger.removeHandler(handler)
            handler.close()

    root_logger.addHandler(_build_stream_handler())
    if log_path is not None:
        root_logger.addHandler(_build_file_handler(log_path))
