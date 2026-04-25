"""
Attente asynchrone d'une condition.

Pas de retry aveugle — chaque tour évalue le prédicat. Renvoie la valeur
non-nulle du prédicat ou lève TimeoutError.
"""
from __future__ import annotations

import asyncio
from typing import Awaitable, Callable, TypeVar

T = TypeVar("T")


async def wait_for(
    predicate: Callable[[], Awaitable[T | None]],
    timeout: float = 10.0,
    poll: float = 0.25,
    message: str = "condition non satisfaite",
) -> T:
    """Attend que predicate() retourne une valeur truthy.

    Exemple :
        session = await wait_for(
            lambda: db.get_latest_session("VIRTUAL-001"),
            timeout=15,
            message="StartTransaction pas persisté",
        )
    """
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    last_value = None
    while loop.time() < deadline:
        last_value = await predicate()
        if last_value:
            return last_value
        await asyncio.sleep(poll)
    raise TimeoutError(f"{message} (timeout={timeout}s, dernier={last_value!r})")


async def wait_until(
    predicate: Callable[[], Awaitable[bool]],
    timeout: float = 10.0,
    poll: float = 0.25,
    message: str = "condition non satisfaite",
) -> None:
    """Variante boolean : attend que predicate() retourne True."""
    await wait_for(
        lambda: _coerce_bool(predicate()),
        timeout=timeout, poll=poll, message=message,
    )


async def _coerce_bool(aw: Awaitable[bool]):
    v = await aw
    return True if v else None
