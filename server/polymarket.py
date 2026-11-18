"""Polymarket data layer: esports event scan, books, tapes, price history.

All upstream calls go through an injectable `fetch` callable (defaults to
upstream.fetch_json) so the whole layer can run against fixtures in tests.
"""
import threading
import time
from urllib.parse import quote

from . import config, signals
from .upstream import parse_list_field


class TTLCache:
    def __init__(self):
        self._lock = threading.Lock()
        self._store = {}

    def get(self, key, ttl):
        with self._lock:
            item = self._store.get(key)
            if item and time.time() - item[0] <= ttl:
                return item[1]
            return None

    def put(self, key, value):
        with self._lock:
            self._store[key] = (time.time(), value)

    def age(self, key):
        with self._lock:
            item = self._store.get(key)
            return None if item is None else time.time() - item[0]

    def mark_failed(self, key):
        self.put(key, {"__failed__": True})

    def failed(self, key, ttl):
        val = self.get(key, ttl)
        return bool(val and val.get("__failed__"))


class EsportsFeed:
    def __init__(self, fetch):
        self.fetch = fetch
        self.cache = TTLCache()
        self._board_stored = None
        self._board_ts = 0.0
        self._board_lock = threading.Lock()
        self.last_error = None

    # ---------------- Gamma ----------------

    def refresh_events(self):
        """Pull active events for every configured tag slug, dedupe, store."""
        seen = {}
        errors = []
        for slug in config.TAG_SLUGS:
            slug = slug.strip()
            if not slug:
                continue
            url = (f"{config.GAMMA_API}/events?closed=false&active=true&archived=false"
                   f"&limit=60&order=volume24hr&ascending=false&tag_slug={quote(slug)}")
            try:
                events = self.fetch(url)
            except Exception as exc:  # noqa: BLE001 - one bad tag must not kill the scan
                errors.append(f"{slug}: {exc}")
                continue
            for ev in events or []:
                ev_id = str(ev.get("id"))
                if ev_id not in seen:
                    seen[ev_id] = normalize_event(ev)
        if seen or not errors:
            self.last_error = "; ".join(errors) if errors and not seen else None
            with self._board_lock:
                self._board_stored = list(seen.values())
                self._board_ts = time.time()
        return len(seen)

    def board_snapshot(self):
        with self._board_lock:
            return self._board_stored or [], self._board_ts

    # ---------------- CLOB ----------------

    def book(self, token_id):
        key = f"book:{token_id}"
        cached = self.cache.get(key, config.REFRESH_BOOK_SECONDS)
        if cached is not None:
            return cached if not self.cache.failed(key, 5) else None
        try:
            data = self.fetch(f"{config.CLOB_API}/book?token_id={quote(token_id)}")
        except Exception:  # noqa: BLE001
            self.cache.mark_failed(key)
            return None
        self.cache.put(key, data)
        return data

    def history(self, token_id):
        key = f"hist:{token_id}"
        cached = self.cache.get(key, config.REFRESH_TTL_SECONDS)
        if cached is not None:
            return cached
        try:
            data = self.fetch(
                f"{config.CLOB_API}/price-history?market={quote(token_id)}"
                f"&interval=6h&fidelity=1"
            )
            hist = (data or {}).get("history") or []
        except Exception:  # noqa: BLE001
            hist = []
        self.cache.put(key, hist)
        return hist

    def trades(self, condition_id):
        key = f"trades:{condition_id}"
        cached = self.cache.get(key, max(5.0, config.REFRESH_BOOK_SECONDS))
        if cached is not None:
            return cached
        try:
            data = self.fetch(f"{config.DATA_API}/trades?market={quote(condition_id)}&limit=100")
        except Exception:  # noqa: BLE001
            data = []
        rows = data if isinstance(data, list) else []
        self.cache.put(key, rows)
        return rows


def normalize_event(event):
    """Flatten a Gamma event into the terminal's internal shape."""
    markets = []
    for m in event.get("markets") or []:
        try:
            markets.append(normalize_market(m))
        except Exception:  # noqa: BLE001 - skip malformed markets
            continue
    tags = [t.get("label") or t.get("slug") for t in event.get("tags") or [] if isinstance(t, dict)]
    game = _game_label(tags)
    return {
        "eventId": str(event.get("id") or ""),
        "title": event.get("title") or "",
        "slug": event.get("slug") or "",
        "endDate": event.get("endDate") or "",
        "volume24": float(event.get("volume24hr") or 0),
        "liquidity": float(event.get("liquidity") or 0),
        "tags": tags,
        "game": game,
        "markets": markets,
    }


def normalize_market(m):
    outcomes = [str(o) for o in parse_list_field(m, "outcomes")]
    prices = parse_list_field(m, "outcomePrices")
    tokens = [str(t) for t in parse_list_field(m, "clobTokenIds")]
    fees = (m.get("feeSchedule") or {}).get("rate")
    if fees is None:
        fees = config.DEFAULT_FEE_RATE
    return {
        "conditionId": m.get("conditionId") or "",
        "question": m.get("question") or "",
        "outcomes": outcomes or ["Yes", "No"],
        "tokens": tokens[:2],
        "prices": [float(p) for p in prices[:2] if _is_num(p)],
        "bestBid": _num(m.get("bestBid")),
        "bestAsk": _num(m.get("bestAsk")),
        "spread": _num(m.get("spread")),
        "tick": _num(m.get("orderPriceMinTickSize")) or 0.001,
        "volume24": float(m.get("volume24hr") or 0),
        "volume1wk": float(m.get("volume1wk") or 0),
        "liquidity": float(m.get("liquidity") or 0),
        "chg1h": _num(m.get("oneHourPriceChange")),
        "chg24h": _num(m.get("oneDayPriceChange")),
        "lastTrade": _num(m.get("lastTradePrice")),
        "negRisk": bool(m.get("negRisk")),
        "endDate": m.get("endDate") or "",
        "feeRate": float(fees),
        "accepting": bool(m.get("acceptingOrders")) and not bool(m.get("closed", False)),
    }


def _is_num(v):
    try:
        float(v)
        return True
    except (TypeError, ValueError):
        return False


def _num(v):
    return float(v) if _is_num(v) else None


def _game_label(tags):
    known = {"CS2": "Counter-Strike", "counter-strike-2": "Counter-Strike",
             "League of Legends": "League of Legends", "lol": "League of Legends",
             "Valorant": "Valorant", "Dota 2": "Dota 2", "dota-2": "Dota 2",
             "Rocket League": "Rocket League", "Overwatch": "Overwatch",
             "Overwatch 2": "Overwatch", "Rainbow Six": "Rainbow Six",
             "Mobile Legends": "Mobile Legends", "Call of Duty": "Call of Duty",
             "StarCraft": "StarCraft", "starcraft2": "StarCraft"}
    for t in tags:
        if t in known:
            return known[t]
    return "Esports"
