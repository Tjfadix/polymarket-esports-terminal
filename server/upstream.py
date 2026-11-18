"""Minimal JSON-over-HTTP client used for all upstream Polymarket calls."""
import json
import time
import urllib.error
import urllib.request

USER_AGENT = "esports-terminal/1.0 (research)"


class UpstreamError(Exception):
    pass


def fetch_json(url, timeout=10.0, retries=2):
    """GET a URL and parse JSON. Raises UpstreamError on any failure."""
    last_err = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
            return json.loads(raw)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError, OSError) as exc:
            last_err = exc
            if attempt < retries:
                time.sleep(0.4 * (attempt + 1))
    raise UpstreamError(f"{url} -> {last_err!r}")


def parse_list_field(market, key):
    """Gamma packs outcomes/outcomePrices/clobTokenIds as JSON inside strings."""
    raw = market.get(key)
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str):
        try:
            val = json.loads(raw)
            return val if isinstance(val, list) else []
        except json.JSONDecodeError:
            return []
    return []
