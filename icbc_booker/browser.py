"""Drive browser.js: it holds the ICBC session open from lock until the emailed code arrives."""
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

STATE_DIR = Path(__file__).resolve().parent.parent / ".state"
SCRIPT = Path(__file__).resolve().parent / "browser.js"
STATUS = STATE_DIR / "status.json"
CODE = STATE_DIR / "otp_code"


def _node_env():
    env = dict(os.environ)
    root = subprocess.run(["npm", "root", "-g"], capture_output=True, text=True).stdout.strip()
    env["NODE_PATH"] = os.pathsep.join(p for p in (env.get("NODE_PATH"), root) if p)
    return env


def read_status():
    try:
        return json.loads(STATUS.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def wait_for(statuses, timeout):
    """Wait until status.json reports one of `statuses` (or any final failure)."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        st = read_status()
        if st and st["status"] in statuses:
            return st
        time.sleep(1)
    return {"status": "ERROR", "error": f"browser did not report {sorted(statuses)} within {timeout}s",
            "last": read_status()}


def start(slot, drvr_id, lock_only=False):
    """Launch a detached browser session for `slot`; returns once it has locked and sent the code
    (or failed). The process keeps running afterwards, waiting for `submit_code`."""
    STATE_DIR.mkdir(exist_ok=True)
    for f in (STATUS, CODE):
        f.unlink(missing_ok=True)
    (STATE_DIR / "request.json").write_text(json.dumps({
        "posId": slot["posId"], "date": slot["appointmentDt"]["date"], "startTm": slot["startTm"],
        "drvrId": drvr_id, "lockOnly": lock_only,
    }))
    if not shutil.which("node"):
        return {"status": "ERROR", "error": "node is not installed"}
    with open(STATE_DIR / "browser.log", "a") as log:
        subprocess.Popen(["node", str(SCRIPT), str(STATE_DIR)], stdout=log, stderr=log,
                         env=_node_env(), start_new_session=True)
    done = {"LOCKED", "OTP_SENT", "LOCK_FAILED", "GONE", "ERROR"}
    return wait_for(done, timeout=150)


def submit_code(code):
    """Hand the emailed code to the waiting browser session and wait for the booking result."""
    st = read_status()
    if not st or st["status"] not in ("OTP_SENT", "OTP_REJECTED"):
        return {"status": "ERROR", "error": "no browser session is waiting for a code", "last": st}
    STATUS.write_text(json.dumps({**st, "status": "CODE_SUBMITTED"}))
    CODE.write_text(code.strip())
    return wait_for({"BOOKED", "OTP_REJECTED", "OTP_TIMEOUT", "ERROR"}, timeout=120)
