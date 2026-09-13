"""Shared HTTP helper for job sources."""

from __future__ import annotations

import logging
from typing import Any

import requests

log = logging.getLogger(__name__)

# Identify the client honestly. These are public endpoints meant to be read;
# there is no reason to pretend to be a browser.
USER_AGENT = "jobpipe/0.1 (personal job search; +https://github.com/Dvaghani/Job-Apply-automation)"
TIMEOUT = 20


class SourceError(RuntimeError):
    """A source failed in a way the caller should report but survive."""


def fetch_json(url: str, params: dict | None = None) -> Any:
    """GET a URL and parse JSON, raising SourceError on any failure."""
    try:
        resp = requests.get(
            url, params=params, timeout=TIMEOUT, headers={"User-Agent": USER_AGENT}
        )
    except requests.RequestException as exc:
        raise SourceError(f"request to {url} failed: {exc}") from exc

    if resp.status_code == 404:
        raise SourceError(f"{url} returned 404 — check the board token/slug")
    if resp.status_code != 200:
        raise SourceError(f"{url} returned HTTP {resp.status_code}")

    try:
        return resp.json()
    except ValueError as exc:
        raise SourceError(f"{url} returned non-JSON body") from exc
