"""Start the registered organizer GPU worker for an explicitly bounded duration."""
import argparse
import json
import os
import re
import sys
import base64
from pathlib import Path

from dotenv import dotenv_values

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--seconds", type=int, required=True, help="GPU uptime, 60 seconds through 7 days")
parser.add_argument("--env", type=Path, default=Path("/etc/uestc-ai/backend.env"))
args = parser.parse_args()
if not 60 <= args.seconds <= 7 * 86400:
    parser.error("--seconds must be between 60 and 604800")
root = Path(__file__).resolve().parents[2]
release = json.loads(Path(__file__).with_name("autodl-depth-images.json").read_text())
remote = release["production_runtime"]["instance_uuid"]
if not re.fullmatch(r"pro-[A-Za-z0-9_-]{1,150}", remote):
    raise SystemExit("Invalid registered organizer instance")
os.environ.update({key: value for key, value in dotenv_values(args.env).items() if value is not None})
sys.path.insert(0, str(root / "backend"))
from platform_api import create_app
from platform_api.autodl import AutoDL
from platform_api.compute_models import ComputeProvider

with create_app().app_context():
    provider = ComputeProvider.query.filter_by(enabled=True,
        image_uuid=release["training_policy"]["image"]).one()
    api = AutoDL(provider)
    status = api.status(remote)
    if status == "shutdown":
        overlay = {"classification-evidence-worker.py": Path(__file__).with_name("classification-evidence-worker.py").read_text(),
                   "evaluation_evidence.py": (root / "backend/evaluation_evidence.py").read_text(),
                   "evaluation_transport.py": (root / "backend/evaluation_transport.py").read_text()}
        encoded = base64.b64encode(json.dumps(overlay).encode()).decode()
        bootstrap = ("import base64,json;from pathlib import Path;"
                     "p=Path('/root/autodl-tmp/uestc-evaluation/evidence-overlay');p.mkdir(parents=True,exist_ok=True);"
                     f"d=json.loads(base64.b64decode('{encoded}'));"
                     "[(p.joinpath(k).write_text(v),p.joinpath(k).chmod(384)) for k,v in d.items()]")
        command = (f"(sleep {args.seconds}; /usr/bin/shutdown) >/tmp/contest-auto-stop.log 2>&1 & "
            f'/opt/uestc-classification/runtime/bin/python -c "{bootstrap}" && '
            "nohup bash -c 'set -a; . /etc/uestc-classification.env; set +a; exec /opt/uestc-classification/runtime/bin/python /root/autodl-tmp/uestc-evaluation/evidence-overlay/classification-evidence-worker.py' "
            ">>/root/autodl-tmp/uestc-evaluation/worker.log 2>&1 & sleep 1")
        api.request("power_on", {"instance_uuid": remote, "payload": "gpu", "start_command": command})
        status = api.status(remote)
    elif status != "running":
        raise SystemExit("Instance is transitioning; check status before retrying")
    print(json.dumps({"instance_uuid": remote, "status": status,
        "requested_uptime_seconds": args.seconds, "note": "An already running instance keeps its existing startup command."}))
