from __future__ import annotations

from contextlib import contextmanager
import fcntl
from pathlib import Path
from threading import Lock
from typing import Iterator

_process_lock = Lock()


@contextmanager
def locked_state(state_path: Path) -> Iterator[None]:
    lock_path = state_path.with_suffix(state_path.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with _process_lock, lock_path.open("a+") as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("Another service process is using this state file") from exc
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

