"""Standalone worker loop: `python -m webhooks.worker` dispatches due deliveries forever."""
import os
import time

from .dispatcher import Dispatcher, HttpxTransport
from .store import Store


def main() -> None:
    store = Store(os.environ.get("WEBHOOKS_DB", "webhooks.db"))
    dispatcher = Dispatcher(store, HttpxTransport())
    interval = float(os.environ.get("WEBHOOKS_POLL_SECONDS", "2"))
    while True:
        stats = dispatcher.run_once()
        if stats.attempted:
            print(stats, flush=True)
        time.sleep(interval)


if __name__ == "__main__":
    main()
