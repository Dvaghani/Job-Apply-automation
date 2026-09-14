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


class NotFound(SourceError):
    """The resource is gone. Distinct because it is often not an error.

    A job filled between a search and a follow-up detail request is the
    ordinary case, not a source outage — the caller usually wants to skip
    that one posting and carry on.
    """


def get(
    url: str,
    params: dict | None = None,
    headers: dict | None = None,
) -> requests.Response:
    """GET a URL, raising SourceError on anything but a 200."""
    try:
        resp = requests.get(
            url,
            params=params,
            timeout=TIMEOUT,
            headers={"User-Agent": USER_AGENT, **(headers or {})},
        )
    except requests.RequestException as exc:
        raise SourceError(f"request to {url} failed: {exc}") from exc

    if resp.status_code == 404:
        raise NotFound(f"{url} returned 404 — check the board token/slug")
    if resp.status_code != 200:
        raise SourceError(f"{url} returned HTTP {resp.status_code}")
    return resp


def fetch_json(
    url: str, params: dict | None = None, headers: dict | None = None
) -> Any:
    """GET a URL and parse JSON, raising SourceError on any failure."""
    resp = get(url, params=params, headers=headers)
    try:
        return resp.json()
    except ValueError as exc:
        raise SourceError(f"{url} returned non-JSON body") from exc


def fetch_text(
    url: str, params: dict | None = None, headers: dict | None = None
) -> str:
    """GET a URL and return its body as text — for XML and RSS feeds."""
    return get(url, params=params, headers=headers).text
