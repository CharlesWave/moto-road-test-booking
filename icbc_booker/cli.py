"""Command line entry point. Every command prints JSON lines; the last line always has a "status" key.

Statuses:
  NO_SLOTS        nothing acceptable right now
  FOUND           acceptable slots exist (check without --book)
  FOUND_NOT_LOCKED acceptable slots exist but locking failed -> alert the user to book by hand
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

from . import browser, config
from .api import IcbcClient, IcbcError, now_local
from .filters import describe, is_acceptable, pick_order, slot_date

TERMINAL = {"OTP_SENT", "FOUND_NOT_LOCKED", "ALREADY_BOOKED", "WINDOW_CLOSED", "BOOKED"}


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
    lock_errors = []
    for slot in candidates:
        st = browser.start(slot, client.user["drvrId"])
        if st["status"] == "OTP_SENT":
            result.update(status="OTP_SENT", locked=describe(slot), otp_method=config.OTP_METHOD)
            break
        lock_errors.append({"slot": describe(slot), "browser": st})
    else:
        result.update(status="FOUND_NOT_LOCKED", note="acceptable slots found but none could be locked")
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
    st = browser.submit_code(args.code)
    if st["status"] != "BOOKED":
        return emit(st)
    try:
        user = IcbcClient().login()
    except Exception as e:  # noqa: BLE001 - booking already succeeded; just report
        return emit({**st, "verify_error": f"{type(e).__name__}: {e}"})
    return emit({**st, "appointments": summarize_appts(user)})


def cmd_test_lock(args):
    """Lock (never book) a slot that does NOT meet the rules, to prove the browser path works."""
    client = IcbcClient()
    user = client.login()
    today = now_local().date()
    slots = client.available(args.pos, today + dt.timedelta(days=1))
    bad = [s for s in slots if not is_acceptable(s, today)]
    if not bad:
        return emit({"status": "ERROR", "error": "no non-qualifying slot to test with"})
    st = browser.start(bad[-1], user["drvrId"], lock_only=True)
    return emit({**st, "slot": describe(bad[-1])})


def cmd_book_slot(args):
    """Book one specific slot, ignoring the date/time rules (manual use)."""
    try:
        client = IcbcClient()
        user = client.login()
        day = dt.date.fromisoformat(args.date)
        slot = next((s for s in client.available(args.pos, day)
                     if s["appointmentDt"]["date"] == args.date and s["startTm"] == args.start), None)
    except Exception as e:  # noqa: BLE001
        return emit({"status": "ERROR", "error": f"{type(e).__name__}: {e}"})
    if slot is None:
        return emit({"status": "GONE", "wanted": vars(args) | {"fn": None}})
    st = browser.start(slot, user["drvrId"])
    return emit({**st, "slot": describe(slot)})


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
    f.set_defaults(fn=cmd_confirm)

    t = sub.add_parser("test-lock", help="lock (never book) a non-qualifying slot via the browser")
    t.add_argument("--pos", type=int, default=11)
    t.set_defaults(fn=cmd_test_lock)

    b = sub.add_parser("book-slot", help="lock one specific slot and email a code (then run confirm)")
    b.add_argument("--pos", type=int, required=True)
    b.add_argument("--date", required=True, help="YYYY-MM-DD")
    b.add_argument("--start", required=True, help="HH:MM, 24h")
    b.set_defaults(fn=cmd_book_slot)

    s = sub.add_parser("status", help="show current ICBC appointments")
    s.set_defaults(fn=cmd_status)

    args = p.parse_args(argv)
    result = args.fn(args)
    return 0 if result["status"] not in ("ERROR",) else 2


if __name__ == "__main__":
    sys.exit(main())
