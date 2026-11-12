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
        time.sleep(0.03)  # pretend network work (the test waits 0.15 s: fine on most machines)
        self.done = True


def currency() -> str:
    """Reads config from the environment (trap: env var leaked by another test)."""
    import os

    return os.environ.get("SHOP_CURRENCY", "USD")


class Inventory:
    """Class-level state shared by every instance (trap: needs another test to run first)."""

    stock: dict[str, int] = {}

    @classmethod
    def restock(cls, item: str, qty: int) -> None:
        cls.stock[item] = cls.stock.get(item, 0) + qty


class Wallet:
    """Gift-card balance updated from several threads (trap: race condition)."""

    def __init__(self) -> None:
        self.balance = 0

    def top_up(self, amount: int) -> None:
        current = self.balance  # read...
        new = self._apply_fee(current, amount)  # ...a call: another thread can run here...
        self.balance = new  # ...write: may overwrite another thread's update

    @staticmethod
    def _apply_fee(balance: int, amount: int) -> int:
        return balance + amount
