"""Shared HTTP session with retries and polite pacing."""
import os
import time

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

_session = None
_last_call = {}


def session():
    global _session
    if _session is None:
        s = requests.Session()
        retry = Retry(total=4, backoff_factor=1.5,
                      status_forcelist=(429, 500, 502, 503, 504),
                      allowed_methods=("GET", "POST"))
        s.mount("https://", HTTPAdapter(max_retries=retry))
        _session = s
    return _session


def pace(host, min_interval):
    """Sleep so calls to one host are at least min_interval seconds apart."""
    now = time.monotonic()
    wait = _last_call.get(host, 0) + min_interval - now
    if wait > 0:
        time.sleep(wait)
    _last_call[host] = time.monotonic()


def sec_headers():
    ua = os.environ.get("SEC_USER_AGENT", "").strip()
    if not ua:
        raise RuntimeError(
            "SEC_USER_AGENT is not set. Add a repo secret like "
            "'Razvan stock-screener you@example.com' (SEC requires a contact).")
    return {"User-Agent": ua, "Accept-Encoding": "gzip, deflate"}
