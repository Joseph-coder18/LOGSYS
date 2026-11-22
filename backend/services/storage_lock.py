"""Reentrant advisory locks shared by threads and server processes (cross-platform)."""
from functools import lru_cache
from pathlib import Path
from threading import RLock, local

try:
    import fcntl
except ImportError:
    fcntl = None


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
                self.state.stream = self.path.open("a")
                if fcntl:
                    fcntl.flock(self.state.stream, fcntl.LOCK_EX)
            self.state.depth = getattr(self.state, "depth", 0) + 1
        except BaseException:
            self.thread_lock.release()
            raise
        return self

    def __exit__(self, *args):
        try:
            self.state.depth -= 1
            if not self.state.depth:
                if fcntl:
                    fcntl.flock(self.state.stream, fcntl.LOCK_UN)
                self.state.stream.close()
        finally:
            self.thread_lock.release()


@lru_cache(maxsize=None)
def storage_lock(path: Path) -> StorageLock:
    return StorageLock(path.resolve())
