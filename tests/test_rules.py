import datetime as dt
import unittest
from zoneinfo import ZoneInfo

from icbc_booker.cli import existing_acceptable, watch_window
from icbc_booker.filters import is_acceptable, pick_order

TODAY = dt.date(2026, 9, 27)  # a Sunday
VAN = ZoneInfo("America/Vancouver")


def slot(date, start, pos=2):
    return {"appointmentDt": {"date": date}, "startTm": start, "endTm": "23:59", "posId": pos}


class FilterTests(unittest.TestCase):
    def test_weekday_needs_2pm_or_later(self):
        self.assertFalse(is_acceptable(slot("2026-10-06", "13:59"), TODAY))  # Tue
        self.assertTrue(is_acceptable(slot("2026-10-06", "14:00"), TODAY))
        self.assertTrue(is_acceptable(slot("2026-10-09", "15:20"), TODAY))  # Fri

    def test_weekend_any_time(self):
        self.assertTrue(is_acceptable(slot("2026-10-03", "08:30"), TODAY))  # Sat
        self.assertTrue(is_acceptable(slot("2026-10-04", "08:30"), TODAY))  # Sun

    def test_deadline_is_oct_24_inclusive(self):
        self.assertTrue(is_acceptable(slot("2026-10-24", "09:00"), TODAY))  # Sat
        self.assertFalse(is_acceptable(slot("2026-10-25", "09:00"), TODAY))  # Sun
        self.assertFalse(is_acceptable(slot("2026-12-09", "14:20"), TODAY))

    def test_tomorrow_ok_today_not(self):
        self.assertFalse(is_acceptable(slot("2026-09-27", "15:00"), TODAY))
        self.assertTrue(is_acceptable(slot("2026-09-28", "15:00"), TODAY))  # Mon

    def test_office_must_be_listed(self):
        self.assertFalse(is_acceptable(slot("2026-10-06", "15:00", pos=1), TODAY))  # Abbotsford
        for pos in (2, 283, 93, 11, 73, 8):
            self.assertTrue(is_acceptable(slot("2026-10-06", "15:00", pos=pos), TODAY))

    def test_pick_order_earliest_first(self):
        slots = [slot("2026-10-10", "09:00", 11), slot("2026-10-06", "15:00", 93), slot("2026-10-06", "14:10", 2)]
        self.assertEqual([s["posId"] for s in pick_order(slots)], [2, 93, 11])


class ExistingBookingTests(unittest.TestCase):
    def test_existing(self):
        appt = {"appointmentDt": {"date": "2026-10-20"}, "dlExam": {"code": "6-R-1"}}
        late = {"appointmentDt": {"date": "2026-12-01"}, "dlExam": {"code": "6-R-1"}}
        other = {"appointmentDt": {"date": "2026-10-20"}, "dlExam": {"code": "5-R-1"}}
        self.assertEqual(existing_acceptable({"webAappointments": [appt, late, other]}), [appt])
        self.assertEqual(existing_acceptable({"webAappointments": None}), [])


class WatchWindowTests(unittest.TestCase):
    T0, T1 = dt.time(0, 0), dt.time(0, 10)

    def test_fired_before_midnight(self):
        now = dt.datetime(2026, 9, 27, 23, 57, tzinfo=VAN)
        tick, end = watch_window(now, self.T0, self.T1)
        self.assertEqual(tick, dt.datetime(2026, 9, 28, 0, 0, tzinfo=VAN))
        self.assertEqual(end, dt.datetime(2026, 9, 28, 0, 10, tzinfo=VAN))

    def test_fired_inside_window(self):
        now = dt.datetime(2026, 9, 28, 0, 3, tzinfo=VAN)
        tick, end = watch_window(now, self.T0, self.T1)
        self.assertEqual(tick, now)
        self.assertEqual(end, dt.datetime(2026, 9, 28, 0, 10, tzinfo=VAN))

    def test_fired_late_runs_once(self):
        now = dt.datetime(2026, 9, 28, 0, 20, tzinfo=VAN)
        self.assertEqual(watch_window(now, self.T0, self.T1), (now, now))


if __name__ == "__main__":
    unittest.main()
