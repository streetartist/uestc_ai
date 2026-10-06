"""Host-wide process lock: acquire before removing recovered containers."""
import os
import tempfile
import sys
from pathlib import Path


def acquire_worker_lock(path=None):
    default = (Path("/run/uestc-ai-evaluation/docker-worker.lock") if sys.platform == "linux" else
               Path(tempfile.gettempdir()) / "uestc-docker-evaluation.lock")
    path = Path(path or os.environ.get("EVALUATION_WORKER_LOCK_FILE") or default)
    path.parent.mkdir(parents=True, exist_ok=True)
    stream = path.open("a+b")
    if path.stat().st_size == 0:
        stream.write(b"0"); stream.flush()
    stream.seek(0)
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        stream.close()
        raise SystemExit("Another Docker evaluation worker is already running on this host") from None
    return stream
