"""Run alongside the web API to monitor and stop budgeted AutoDL instances."""
import logging
import signal
import time
from uuid import uuid4

from platform_api import create_app
from platform_api.compute import claim_session, heartbeat, process_session
from platform_api.extensions import db


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    app = create_app()
    worker_id = "compute-" + uuid4().hex
    stopping = False

    def stop(_signal, _frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    logging.info("Compute worker started; no instances are created without an authorized queued request.")
    while not stopping:
        session = None
        with app.app_context():
            try:
                heartbeat(worker_id)
                session = claim_session(worker_id)
                if session:
                    process_session(session, worker_id)
                    heartbeat(worker_id)
            except Exception:
                # Do not print credentials, upstream bodies, or DB parameters.
                db.session.rollback()
                logging.error("Compute cycle failed; durable requests will be recovered after the lease expires.")
        time.sleep(1 if session else 5)
    with app.app_context():
        from platform_api.compute_models import ComputeWorkerHeartbeat
        entry = db.session.get(ComputeWorkerHeartbeat, worker_id)
        if entry:
            db.session.delete(entry)
            db.session.commit()


if __name__ == "__main__":
    main()
