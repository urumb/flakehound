"""Delta debugging (Zeller's ddmin), with each round's candidates tested in parallel.

Given a list of tests that reproduces a failure, find a small subset that still
reproduces it. The result is "1-minimal": removing any single test from it
makes the failure disappear.

Each round splits the list into ``n`` chunks and asks, all at once, "does any
chunk alone reproduce?" and "does the list without that chunk reproduce?".
Asking in parallel trades a few extra runs for much less waiting.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import TypeVar

T = TypeVar("T")

# Given candidate subsets, return for each one whether it still reproduces.
TestMany = Callable[[list[list[T]]], list[bool]]


def _split(items: Sequence[T], n: int) -> list[list[T]]:
    size, extra = divmod(len(items), n)
    chunks, start = [], 0
    for i in range(n):
        end = start + size + (1 if i < extra else 0)
        chunks.append(list(items[start:end]))
        start = end
    return [c for c in chunks if c]


def ddmin(items: Sequence[T], test_many: TestMany[T], *, max_rounds: int = 200) -> list[T]:
    current = list(items)
    n = 2
    for _ in range(max_rounds):
        if len(current) < 2:
            break
        chunks = _split(current, n)
        complements = (
            [
                [x for j, other in enumerate(chunks) if j != i for x in other]
                for i in range(len(chunks))
            ]
            if len(chunks) > 2
            else []  # with 2 chunks, each complement is just the other chunk
        )
        results = test_many(chunks + complements)
        hit_chunk = next((c for c, ok in zip(chunks, results, strict=False) if ok), None)
        if hit_chunk is not None:
            current, n = hit_chunk, 2
            continue
        hit_comp = next(
            (c for c, ok in zip(complements, results[len(chunks) :], strict=True) if ok), None
        )
        if hit_comp is not None:
            current, n = hit_comp, max(n - 1, 2)
            continue
        if n >= len(current):
            break
        n = min(len(current), n * 2)
    return current
