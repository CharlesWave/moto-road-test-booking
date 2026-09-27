"""Booking preferences. Edit here to change what the monitor will book."""
import datetime as dt
from zoneinfo import ZoneInfo

API_BASE = "https://onlinebusiness.icbc.com/deas-api/v1"
TZ = ZoneInfo("America/Vancouver")

EXAM = "6-R-1"  # Class 6 (full licence) motorcycle road test

# ICBC point-of-service IDs that offer Class 6 road tests.
OFFICES = {
    2: "Burnaby (3880 Lougheed Hwy)",
    283: "Coquitlam (1575 Hartley Ave)",
    93: "Richmond (Lansdowne Centre)",
    11: "Surrey (13426 78 Ave)",
    73: "Port Coquitlam (1930 Oxford Connector)",
    8: "North Vancouver (1331 Marine Dr)",
}

LAST_ACCEPTABLE_DATE = dt.date(2026, 10, 24)  # test must be before Oct 25
MIN_LEAD_DAYS = 1  # tomorrow is the earliest acceptable day
WEEKDAY_EARLIEST_START = dt.time(14, 0)  # Mon-Fri: start at or after 2pm; Sat/Sun: any time

OTP_METHOD = "E"  # email (browser.js sends "E")
MAX_LOCK_ATTEMPTS = 5
