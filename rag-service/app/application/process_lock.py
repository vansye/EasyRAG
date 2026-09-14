"""OS-owned process exclusion shared by the service and maintenance CLI."""

import os
import sys
from pathlib import Path


class RuntimeLockUnavailable(RuntimeError):
    def __init__(self):
        super().__init__('无法独占后端运行锁，请检查是否已有后端或维护进程运行')


class ProcessLock:
    def __init__(self, path: str | Path):
        self._path = Path(path)
        self._file = None

    def __enter__(self):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        file = os.fdopen(os.open(self._path, os.O_CREAT | os.O_RDWR, 0o600), 'r+b')
        try:
            if os.fstat(file.fileno()).st_size == 0:
                file.write(b'0')
                file.flush()
            file.seek(0)
            if sys.platform == 'win32':
                import msvcrt
                msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            file.close()
            raise RuntimeLockUnavailable() from None
        self._file = file
        return self

    def __exit__(self, *_):
        file, self._file = self._file, None
        if file is not None:
            try:
                file.seek(0)
                if sys.platform == 'win32':
                    import msvcrt
                    msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(file.fileno(), fcntl.LOCK_UN)
            finally:
                file.close()
