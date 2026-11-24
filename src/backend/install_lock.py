import os
from contextlib import ExitStack, contextmanager

if os.name == "nt":
    import msvcrt
else:
    import fcntl


class InstallationBusy(Exception):
    pass


@contextmanager
def _file_lock(path, *, wait=False):
    # Keep this path after unlocking so all processes lock the same file.
    with path.open("a+b") as lock:
        lock.seek(0)
        try:
            if os.name == "nt":
                mode = msvcrt.LK_LOCK if wait else msvcrt.LK_NBLCK
                msvcrt.locking(lock.fileno(), mode, 1)
            else:
                mode = fcntl.LOCK_EX if wait else fcntl.LOCK_EX | fcntl.LOCK_NB
                fcntl.flock(lock, mode)
        except (BlockingIOError, PermissionError) as error:
            raise InstallationBusy from error
        try:
            yield
        finally:
            if os.name == "nt":
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)


@contextmanager
def installation_lock(root):
    runtime = root / ".runtime"
    runtime.mkdir(exist_ok=True)
    with _file_lock(runtime / "operation.lock"):
        # No server can register while the updater holds the operation lock.
        for path in runtime.glob("server-*.lock"):
            with _file_lock(path):
                pass
        yield


def pending_update(root):
    return root / ".runtime" / "update-pending"


@contextmanager
def server_installation(root, port):
    runtime = root / ".runtime"
    runtime.mkdir(exist_ok=True)
    port_lock = runtime / f"server-{port}.lock"
    with ExitStack() as server:
        try:
            # Serialize admission so simultaneous starts on distinct ports can both register.
            with _file_lock(runtime / "start.lock", wait=True), _file_lock(runtime / "operation.lock"):
                if pending_update(root).exists():
                    raise SystemExit("An update was interrupted. Run the update script before starting Oshigoe.")
                try:
                    server.enter_context(_file_lock(port_lock))
                except InstallationBusy:
                    raise SystemExit(f"Oshigoe is already running on port {port}. Choose another port.") from None
        except InstallationBusy:
            raise SystemExit("Oshigoe cannot start while an update is in progress.") from None
        yield
