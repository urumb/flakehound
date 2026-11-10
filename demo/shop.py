"""A tiny shop with the kind of shared state that causes flaky tests."""

import random
import threading
import time

cart: list[str] = []  # module-level state shared by every test (trap: order dependency)


def add_item(item: str) -> None:
    cart.append(item)


def total(prices: dict[str, float]) -> float:
    return sum(prices[item] for item in cart)


def lucky_discount() -> float:
    """Unseeded randomness (trap: randomness)."""
    return random.random()


def render_tags(tags: set[str]) -> str:
    """Iterates a set: order depends on PYTHONHASHSEED (trap: hash seed)."""
    return ",".join(tags)


class Uploader:
    """Uploads in a background thread (trap: timing)."""

    def __init__(self) -> None:
        self.done = False

    def start(self) -> None:
        threading.Thread(target=self._work, daemon=True).start()

    def _work(self) -> None:
        time.sleep(0.05)  # pretend network work
        self.done = True
