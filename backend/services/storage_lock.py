"""Reentrant advisory locks shared by threads and server processes (cross-platform)."""
from functools import lru_cache
from pathlib import Path
from threading import RLock, local
import time

try:
    import fcntl
except ImportError:
    fcntl = None

try:
    import msvcrt
except ImportError:
    msvcrt = None


def _win_lock(stream) -> None:
    """Acquire an exclusive byte-range lock on Windows with retry."""
    fd = stream.fileno()
    deadline = time.monotonic() + 30.0
    while True:
        try:
            stream.seek(0)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            return
        except OSError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.01)


def _win_unlock(stream) -> None:
    """Release the exclusive byte-range lock on Windows."""
    stream.seek(0)
    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)


class StorageLock:
    def __init__(self, path: Path):
        self.path = path
        self.thread_lock = RLock()
        self.state = local()

    def __enter__(self):
        self.thread_lock.acquire()
        try:
            if not getattr(self.state, "depth", 0):
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.state.stream = self.path.open("a+b")
                if fcntl:
                    fcntl.flock(self.state.stream, fcntl.LOCK_EX)
                elif msvcrt:
                    _win_lock(self.state.stream)
            self.state.depth = getattr(self.state, "depth", 0) + 1
        except BaseException:
            stream = getattr(self.state, "stream", None)
            if stream is not None and not getattr(self.state, "depth", 0):
                stream.close()
                self.state.stream = None
            self.thread_lock.release()
            raise
        return self

    def __exit__(self, *args):
        try:
            self.state.depth -= 1
            if not self.state.depth:
                try:
                    if fcntl:
                        fcntl.flock(self.state.stream, fcntl.LOCK_UN)
                    elif msvcrt:
                        _win_unlock(self.state.stream)
                finally:
                    self.state.stream.close()
                    self.state.stream = None
        finally:
            self.thread_lock.release()


@lru_cache(maxsize=None)
def storage_lock(path: Path) -> StorageLock:
    return StorageLock(path.resolve())
