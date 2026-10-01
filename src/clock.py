"""Clock for feature TTLs.

The live service uses wall time. The seeded switchback eval freezes this
clock so driver and search windows match a 4-second tick instead of
however fast the loop runs.
"""

import time

_frozen: float | None = None


def now() -> float:
    if _frozen is not None:
        return _frozen
    return time.time()


def set_frozen(timestamp: float | None) -> None:
    global _frozen
    _frozen = timestamp


def advance(seconds: float) -> None:
    global _frozen
    if _frozen is None:
        _frozen = time.time()
    _frozen += seconds
