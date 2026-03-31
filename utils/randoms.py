from __future__ import annotations

import random
from typing import Sequence, TypeVar

T = TypeVar("T")

_rng = random.SystemRandom()


def generate_lobby_password(length: int = 4) -> str:
    return "".join(str(_rng.randrange(10)) for _ in range(length))


def shuffled(items: Sequence[T]) -> list[T]:
    copied = list(items)
    _rng.shuffle(copied)
    return copied
