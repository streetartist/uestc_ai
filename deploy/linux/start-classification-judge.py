"""Start the registered organizer GPU worker for an explicitly bounded duration."""
import argparse
import json
import os
import re
import sys
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
from platform_api.judge import startup_command
from platform_api.judge_models import JudgePool

with create_app().app_context():
    pool = JudgePool.query.filter_by(remote_id=remote).one()
    api = AutoDL(pool.provider)
    status = api.status(remote)
    if status == "shutdown":
        command = startup_command(pool, uptime_seconds=args.seconds)
        api.request("power_on", {"instance_uuid": remote, "payload": "gpu", "start_command": command})
        status = api.status(remote)
    elif status != "running":
        raise SystemExit("Instance is transitioning; check status before retrying")
    print(json.dumps({"instance_uuid": remote, "status": status,
        "requested_uptime_seconds": args.seconds, "note": "An already running instance keeps its existing startup command."}))
