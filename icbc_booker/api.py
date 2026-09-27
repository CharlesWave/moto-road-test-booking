"""Thin client for ICBC's driver-exam booking API (the one behind onlinebusiness.icbc.com/webdeas-ui)."""
import datetime as dt
import os

import requests

from . import config


class IcbcError(RuntimeError):
    pass


def now_local():
    return dt.datetime.now(config.TZ)


def booked_ts(when=None):
    """Timestamp format the web UI sends with lock/OTP calls: local YYYY-MM-DDTHH:MM:SS."""
    return (when or now_local()).strftime("%Y-%m-%dT%H:%M:%S")


class IcbcClient:
    def __init__(self, last_name=None, licence=None, keyword=None, token=None, timeout=20):
        self.last_name = last_name or os.environ["ICBC_LAST_NAME"]
        self.licence = licence or os.environ["ICBC_LICENCE"]
        self.keyword = keyword or os.environ["ICBC_KEYWORD"]
        self.timeout = timeout
        self.user = None
        self.s = requests.Session()
        self.s.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/128.0 Safari/537.36",
            "Origin": "https://onlinebusiness.icbc.com",
            "Referer": "https://onlinebusiness.icbc.com/webdeas-ui/",
            "Content-Type": "application/json",
            "Cache-Control": "no-cache, no-store",
        })
        if token:
            self.s.headers["Authorization"] = token

    @property
    def token(self):
        return self.s.headers.get("Authorization")

    def login(self):
        r = self.s.put(config.API_BASE + "/webLogin/webLogin", timeout=self.timeout, json={
            "drvrLastName": self.last_name,
            "licenceNumber": self.licence,
            "keyword": self.keyword,
        })
        if not r.ok:
            raise IcbcError(f"login failed: HTTP {r.status_code} {r.text[:300]}")
        self.s.headers["Authorization"] = r.headers["authorization"]
        self.user = r.json()
        return self.user

    def _call(self, method, path, body, retry=True):
        r = self.s.request(method, config.API_BASE + path, json=body, timeout=self.timeout)
        if r.status_code in (401, 403) and retry:
            self.login()
            return self._call(method, path, body, retry=False)
        if not r.ok:
            raise IcbcError(f"{path} failed: HTTP {r.status_code} {r.text[:300]}")
        return r.json() if r.content else None

    def available(self, pos_id, from_date):
        return self._call("POST", "/web/getAvailableAppointments", {
            "aPosID": pos_id,
            "examType": config.EXAM,
            "examDate": from_date.isoformat(),
            "prfDaysOfWeek": "[0,1,2,3,4,5,6]",
            "prfPartsOfDay": "[0,1]",
            "lastName": self.user["lastName"],
            "licenseNumber": self.user["licenseNumber"],
        }) or []

    def lock(self, slot, ts):
        return self._call("PUT", "/web/lock", {
            "appointmentDt": slot["appointmentDt"],
            "dlExam": slot["dlExam"],
            "drvrDriver": {"drvrId": self.user["drvrId"]},
            "drscDrvSchl": {},
            "instructorDlNum": None,
            "bookedTs": ts,
            "startTm": slot["startTm"],
            "endTm": slot["endTm"],
            "posId": slot["posId"],
            "resourceId": slot["resourceId"],
            "signature": slot["signature"],
        })

    def send_otp(self, drvr_id, ts, method=config.OTP_METHOD):
        return self._call("POST", "/web/sendOTP", {"bookedTs": ts, "drvrID": drvr_id, "method": method})

    def verify_otp(self, drvr_id, ts, code):
        return self._call("PUT", "/web/verifyOTP", {"bookedTs": ts, "drvrID": drvr_id, "code": code})

    def book(self, drvr_id):
        return self._call("PUT", "/web/book", {
            "userId": f"WEBD:{drvr_id}",
            "appointment": {"drvrDriver": {"drvrId": drvr_id}},
        })
