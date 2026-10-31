"""Progress goes to stderr, so stdout stays free for results (a Board id, a document)."""

import sys


def logger(agent: str):
    def log(message: str) -> None:
        print(f"[{agent}] {message}", file=sys.stderr, flush=True)

    return log
