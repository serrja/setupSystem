"""Run blocking calls in the current runtime's bounded worker thread pool."""

from collections.abc import Callable
from functools import partial

import anyio


async def run_sync[**P, T](function: Callable[P, T], /, *args: P.args, **kwargs: P.kwargs) -> T:
    return await anyio.to_thread.run_sync(
        partial(function, *args, **kwargs), limiter=anyio.to_thread.current_default_thread_limiter()
    )
