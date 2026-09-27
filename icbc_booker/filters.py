"""Rules for which appointment slots are acceptable."""
import datetime as dt

from . import config


def slot_date(slot):
    return dt.date.fromisoformat(slot["appointmentDt"]["date"])


def slot_time(slot):
    return dt.time.fromisoformat(slot["startTm"])


def is_acceptable(slot, today):
    if slot.get("posId") not in config.OFFICES:
        return False
    day = slot_date(slot)
    if day < today + dt.timedelta(days=config.MIN_LEAD_DAYS):
        return False
    if day > config.LAST_ACCEPTABLE_DATE:
        return False
    if day.weekday() < 5 and slot_time(slot) < config.WEEKDAY_EARLIEST_START:
        return False
    return True


def pick_order(slots):
    """Earliest date first, then earliest start, then office."""
    return sorted(slots, key=lambda s: (slot_date(s), slot_time(s), s["posId"]))


def describe(slot):
    return {
        "date": slot["appointmentDt"]["date"],
        "day": slot["appointmentDt"].get("dayOfWeek"),
        "start": slot["startTm"],
        "end": slot.get("endTm"),
        "posId": slot["posId"],
        "office": config.OFFICES.get(slot["posId"], str(slot["posId"])),
    }
