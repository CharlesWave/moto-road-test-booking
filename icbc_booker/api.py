"""Thin client for ICBC's driver-exam booking API (the one behind onlinebusiness.icbc.com/webdeas-ui).

Only login and search live here: ICBC rejects lock/OTP/book from plain HTTP clients, so those run
in a real browser session (browser.js)."""
import datetime as dt
import os

import requests

from . import config


class IcbcError(RuntimeError):
    pass


def now_local():
    return dt.datetime.now(config.TZ)


class IcbcClient:
    def __init__(self, last_name=None, licence=None, keyword=None, timeout=20):
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
