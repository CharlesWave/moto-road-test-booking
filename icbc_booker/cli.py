"""Command line entry point. Every command prints JSON lines; the last line always has a "status" key.

Statuses:
  NO_SLOTS        nothing acceptable right now
  FOUND           acceptable slots exist (check without --book)
  OTP_SENT        best slot locked and a verification code emailed -> run `confirm --code`
  BOOKED          booking confirmed
  ALREADY_BOOKED  an acceptable Class 6 booking already exists -> stop monitoring
  WINDOW_CLOSED   no acceptable date can exist any more -> stop monitoring
  ERROR           something failed; see "error"
"""
import argparse
import datetime as dt
import json
import sys
import time
from pathlib import Path

from . import config
from .api import IcbcClient, IcbcError, booked_ts, now_local
from .filters import describe, is_acceptable, pick_order, slot_date

STATE = Path(__file__).resolve().parent.parent / ".state" / "pending.json"
TERMINAL = {"OTP_SENT", "ALREADY_BOOKED", "WINDOW_CLOSED", "BOOKED"}


def emit(obj):
    obj.setdefault("at", now_local().isoformat(timespec="seconds"))
    print(json.dumps(obj), flush=True)
    return obj


def existing_acceptable(user):
    """Class 6 appointments already on file that satisfy the deadline."""
    found = []
    for a in user.get("webAappointments") or []:
        try:
            code = (a.get("dlExam") or {}).get("code")
            day = dt.date.fromisoformat(a["appointmentDt"]["date"])
        except (KeyError, TypeError, ValueError):
            continue
        if code == config.EXAM and day <= config.LAST_ACCEPTABLE_DATE:
            found.append(a)
    return found


def summarize_appts(user):
    out = []
    for a in user.get("webAappointments") or []:
        try:
            out.append({
                "date": a["appointmentDt"]["date"],
                "start": a.get("startTm"),
                "exam": (a.get("dlExam") or {}).get("code"),
                "posId": a.get("posId"),
                "office": config.OFFICES.get(a.get("posId"), a.get("posId")),
            })
        except (KeyError, TypeError):
            out.append(a)
    return out


def run_check(book=False, verbose=False):
    today = now_local().date()
    if today + dt.timedelta(days=config.MIN_LEAD_DAYS) > config.LAST_ACCEPTABLE_DATE:
        return {"status": "WINDOW_CLOSED"}

    client = IcbcClient()
    user = client.login()
    if existing_acceptable(user):
        return {"status": "ALREADY_BOOKED", "appointments": summarize_appts(user)}

    start = today + dt.timedelta(days=config.MIN_LEAD_DAYS)
    good, earliest, errors = [], {}, {}
    for pos_id in config.OFFICES:
        try:
            slots = client.available(pos_id, start)
        except IcbcError as e:
            errors[pos_id] = str(e)
            continue
        if slots:
            first = min(slots, key=slot_date)
            earliest[config.OFFICES[pos_id]] = f'{first["appointmentDt"]["date"]} {first["startTm"]}'
        good.extend(s for s in slots if is_acceptable(s, today))

    good = pick_order(good)
    result = {"status": "NO_SLOTS", "acceptable": [describe(s) for s in good]}
    if verbose:
        result["earliest_per_office"] = earliest
    if errors:
        result["office_errors"] = errors
    if len(errors) == len(config.OFFICES):
        result["status"] = "ERROR"
        result["error"] = "every office query failed"
        return result
    if not good:
        return result
    if not book:
        result["status"] = "FOUND"
        return result
    return lock_and_send_otp(client, good[: config.MAX_LOCK_ATTEMPTS], result)


def lock_and_send_otp(client, candidates, result):
    drvr_id = client.user["drvrId"]
    lock_errors = []
    for slot in candidates:
        ts = booked_ts()
        try:
            client.lock(slot, ts)
        except IcbcError as e:
            lock_errors.append({"slot": describe(slot), "error": str(e)})
            continue
        client.send_otp(drvr_id, ts)
        STATE.parent.mkdir(exist_ok=True)
        STATE.write_text(json.dumps({
            "token": client.token, "drvrId": drvr_id, "bookedTs": ts, "slot": slot,
        }))
        result.update(status="OTP_SENT", locked=describe(slot), otp_method=config.OTP_METHOD)
        break
    else:
        result.update(status="NO_SLOTS", note="acceptable slots found but none could be locked")
    if lock_errors:
        result["lock_errors"] = lock_errors
    return result


def cmd_check(args):
    try:
        return emit(run_check(book=args.book, verbose=args.verbose))
    except Exception as e:  # noqa: BLE001 - report every failure to the routine
        return emit({"status": "ERROR", "error": f"{type(e).__name__}: {e}"})


def parse_hhmm(s):
    return dt.time.fromisoformat(s)


def watch_window(now, start, until):
    """Return (first_tick, end) datetimes for the next start..until window.

    Fired shortly before midnight -> window is tomorrow. Fired inside the window -> start now.
    Fired after the window (routine delayed) -> a single immediate check."""
    start_dt = now.replace(hour=start.hour, minute=start.minute, second=0, microsecond=0)
    end_dt = now.replace(hour=until.hour, minute=until.minute, second=0, microsecond=0)
    if end_dt < start_dt:
        end_dt += dt.timedelta(days=1)
    if now > end_dt:
        if (now - end_dt) > dt.timedelta(hours=12):
            start_dt += dt.timedelta(days=1)
            end_dt += dt.timedelta(days=1)
        else:
            return now, now
    return max(start_dt, now), end_dt


def cmd_watch(args):
    now = now_local()
    tick, end = watch_window(now, parse_hhmm(args.start), parse_hhmm(args.until))
    emit({"status": "WATCHING", "first_check": tick.isoformat(timespec="seconds"),
          "until": end.isoformat(timespec="seconds"), "every_s": args.every})
    last = None
    while tick <= end:
        wait = (tick - now_local()).total_seconds()
        if wait > 0:
            time.sleep(wait)
        last = cmd_check(args)
        if last["status"] in TERMINAL:
            return last
        tick += dt.timedelta(seconds=args.every)
    return last or emit({"status": "NO_SLOTS"})


def cmd_confirm(args):
    try:
        pending = json.loads(STATE.read_text())
    except FileNotFoundError:
        return emit({"status": "ERROR", "error": "no pending lock; run `check --book` first"})
    client = IcbcClient(token=pending["token"])
    drvr_id, ts = pending["drvrId"], pending["bookedTs"]
    try:
        if args.relogin:
            client.login()
        v =client.verify_otp(drvr_id, ts, args.code.strip())
        if (v or {}).get("status") != "VERIFIED":
            return emit({"status": "ERROR", "error": f"code not verified: {v}"})
        booking = client.book(drvr_id)
        user = client.login()
    except Exception as e:  # noqa: BLE001
        return emit({"status": "ERROR", "error": f"{type(e).__name__}: {e}"})
    STATE.unlink(missing_ok=True)
    return emit({"status": "BOOKED", "slot": describe(pending["slot"]), "icbc_response": booking,
                 "appointments": summarize_appts(user)})


def cmd_status(args):
    try:
        user = IcbcClient().login()
    except Exception as e:  # noqa: BLE001
        return emit({"status": "ERROR", "error": f"{type(e).__name__}: {e}"})
    status = "ALREADY_BOOKED" if existing_acceptable(user) else "NOT_BOOKED"
    return emit({"status": status, "appointments": summarize_appts(user),
                 "eligibleExams": [e.get("code") for e in user.get("eligibleExams") or []]})


def main(argv=None):
    p = argparse.ArgumentParser(prog="icbc_booker")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("check", help="look for acceptable slots once")
    c.add_argument("--book", action="store_true", help="lock the best slot and email a verification code")
    c.add_argument("--verbose", action="store_true", help="also show earliest slot per office")
    c.set_defaults(fn=cmd_check)

    w = sub.add_parser("watch", help="check repeatedly inside a time window (Vancouver time)")
    w.add_argument("--start", default="00:00")
    w.add_argument("--until", default="00:10")
    w.add_argument("--every", type=int, default=120, help="seconds between checks")
    w.add_argument("--book", action="store_true")
    w.add_argument("--verbose", action="store_true")
    w.set_defaults(fn=cmd_watch)

    f = sub.add_parser("confirm", help="finish a locked booking with the emailed code")
    f.add_argument("--code", required=True)
    f.add_argument("--relogin", action="store_true", help="log in fresh instead of reusing the saved token")
    f.set_defaults(fn=cmd_confirm)

    s = sub.add_parser("status", help="show current ICBC appointments")
    s.set_defaults(fn=cmd_status)

    args = p.parse_args(argv)
    result = args.fn(args)
    return 0 if result["status"] not in ("ERROR",) else 2


if __name__ == "__main__":
    sys.exit(main())
