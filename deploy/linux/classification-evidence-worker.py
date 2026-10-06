"""Transport-only overlay for sealed v4 images; preserve the saved judge runtime."""
import sys
import fcntl
from pathlib import Path

# Only one trusted process may claim jobs, including after manual restarts.
lock_path = Path('/root/autodl-tmp/uestc-evaluation/worker.lock')
lock_path.parent.mkdir(parents=True, exist_ok=True)
lock = lock_path.open('a')
try:
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    raise SystemExit('Trusted worker is already running')

sys.path.insert(0, "/opt/uestc-classification/backend")
import classification_worker as worker

# This helper is installed beside this wrapper, outside the image's sealed code.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from evaluation_evidence import publish_classification
from evaluation_transport import install_origin_route

install_origin_route()

download = worker.download
request_json = worker.request_json
infrastructure_before_execution = False


def trusted_download(*args, **kwargs):
    global infrastructure_before_execution
    try:
        return download(*args, **kwargs)
    except (OSError, RuntimeError):
        infrastructure_before_execution = True
        print('Infrastructure: package transfer failed before inference', flush=True)
        raise


def with_failure_kind(base, path, method='GET', body=None, headers=None):
    if path.endswith('/complete') and body and body.get('status') == 'failed' and infrastructure_before_execution:
        body = {**body, 'failure_kind': 'infrastructure_before_execution'}
    return request_json(base, path, method, body, headers)


worker.download = trusted_download
worker.request_json = with_failure_kind

execute = worker.execute


def with_evidence(base, job, **kwargs):
    global infrastructure_before_execution
    infrastructure_before_execution = False
    result = execute(base, job, **kwargs)
    if result["status"] == "completed":
        publish_classification(base, job, kwargs["log_root"])
    return result


worker.execute = with_evidence
worker.main()
