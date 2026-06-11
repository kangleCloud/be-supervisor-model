"""异步边界辅助函数。"""
from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from starlette.concurrency import run_in_threadpool


T = TypeVar("T")


async def run_blocking(func: Callable[..., T], /, *args, **kwargs) -> T:
    """把同步阻塞调用放到线程池，避免阻塞事件循环。"""
    return await run_in_threadpool(func, *args, **kwargs)
