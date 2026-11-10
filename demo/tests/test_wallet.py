import threading

import shop


def test_concurrent_top_ups():
    wallet = shop.Wallet()

    def worker():
        for _ in range(2000):
            wallet.top_up(1)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    # Bug: top_up is not thread-safe. Passes almost always, until threads interleave.
    assert wallet.balance == 8000
