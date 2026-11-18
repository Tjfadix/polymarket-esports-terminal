"""Deterministic synthetic esports feed for PM_DEMO=1.

Provides the same call signatures as upstream.fetch_json for three URL
families: /events, /book, /price-history and /trades, seeded so results are
stable within a process run but drift slightly over time like a real market.
"""
import hashlib
import random
import time

_MATCHES = [
    ("Natus Vincere vs FaZe Clan", "Natus Vincere", "FaZe Clan", "Counter-Strike", 0.58),
    ("Team Vitality vs G2 Esports", "Team Vitality", "G2 Esports", "Counter-Strike", 0.62),
    ("T1 vs Gen.G", "T1", "Gen.G", "League of Legends", 0.47),
    ("G2 vs Fnatic", "G2", "Fnatic", "League of Legends", 0.55),
    ("Sentinels vs Fnatic", "Sentinels", "Fnatic", "Valorant", 0.51),
    ("Team Spirit vs Gaimin Gladiators", "Team Spirit", "Gaimin Gladiators", "Dota 2", 0.66),
    ("Karmine Corp vs Team BDS", "Karmine Corp", "Team BDS", "Rocket League", 0.54),
    ("Crazy Raccoon vs Fnatic", "Crazy Raccoon", "Fnatic", "Overwatch", 0.49),
]

_seed_state = {}


def _seeded(tag, salt=""):
    key = tag + salt
    if key not in _seed_state:
        _seed_state[key] = random.Random(int(hashlib.sha256(key.encode()).hexdigest()[:12], 16))
    return _seed_state[key]


def _fake_event(idx, title, favorite, underdog, game, base_p):
    rnd = _seeded(title, "evt")
    event_id = 99000 + idx
    markets = []
    for team, win_p in ((favorite, base_p), (underdog, round(1 - base_p, 4))):
        q = f"Will {team} win?"
        p = win_p
        bid = round(p - rnd.uniform(0.002, 0.006), 4)
        ask = round(p + rnd.uniform(0.002, 0.006), 4)
        yes_id = str(10**70 + idx * 3 + (0 if team == favorite else 1))
        no_id = str(10**70 + idx * 3 + 2 + (0 if team == favorite else 1))
        markets.append({
            "id": str(990000 + idx * 2),
            "question": q,
            "conditionId": "0xdemo{:06d}{}".format(idx, "1" if team == favorite else "2"),
            "outcomes": '["Yes", "No"]',
            "outcomePrices": f'["{p}", "{round(1 - p, 4)}"]',
            "clobTokenIds": f'["{yes_id}", "{no_id}"]',
            "bestBid": bid, "bestAsk": ask,
            "spread": round(ask - bid, 4),
            "orderPriceMinTickSize": 0.001,
            "volume24hr": round(rnd.uniform(30_000, 400_000) * (1.4 if team == favorite else 0.9), 2),
            "volume1wk": round(rnd.uniform(200_000, 2_000_000), 2),
            "liquidity": round(rnd.uniform(10_000, 120_000), 2),
            "oneHourPriceChange": round(rnd.uniform(-0.02, 0.02), 4),
            "oneDayPriceChange": round(rnd.uniform(-0.06, 0.06), 4),
            "lastTradePrice": p,
            "negRisk": False,
            "endDate": "2026-12-31T23:59:00Z",
            "feeSchedule": {"rate": 0.03},
            "acceptingOrders": True,
            "closed": False,
        })
    return {
        "id": str(event_id),
        "title": title,
        "slug": title.lower().replace(" ", "-").replace(".", ""),
        "endDate": "2026-12-31T23:59:00Z",
        "volume24hr": round(rnd.uniform(80_000, 700_000), 2),
        "liquidity": round(rnd.uniform(40_000, 250_000), 2),
        "tags": [{"label": "Esports", "slug": "esports"}, {"label": game, "slug": game.lower().replace(" ", "-")}],
        "markets": markets,
    }


def _build_events():
    return [_fake_event(i, t, fav, ud, g, p) for i, (t, fav, ud, g, p) in enumerate(_MATCHES)]


_TOKEN_BOOK = {}


def _fake_book(token_id, chg1h=0.0):
    rnd = _seeded(token_id, "book")
    drift = rnd.uniform(-0.01, 0.01) + chg1h
    mid = max(0.05, min(0.95, 0.5 + drift))
    step = 0.004
    bids, asks = [], []
    for i in range(12):
        bids.append({"price": round(mid - step * i - 0.002, 4),
                     "size": round(rnd.uniform(150, 2500) * (1.9 if i < 3 else 1.0), 1)})
        asks.append({"price": round(mid + step * i + 0.002, 4),
                     "size": round(rnd.uniform(150, 2500) * (0.8 if i < 3 else 1.0), 1)})
    return {"bids": bids, "asks": asks}


def _fake_history(token_id):
    rnd = _seeded(token_id, "hist")
    now = int(time.time())
    price, out = 0.5, []
    for i in range(360):
        price = max(0.05, min(0.95, price + rnd.gauss(0, 0.004)))
        out.append({"t": now - (360 - i) * 60, "p": round(price, 4)})
    return out


def _fake_trades(condition_id):
    rnd = _seeded(condition_id, "trades")
    now = int(time.time())
    rows = []
    for i in range(60):
        rows.append({
            "side": "BUY" if rnd.random() < 0.58 else "SELL",
            "size": round(rnd.uniform(20, 900) * (3 if rnd.random() < 0.1 else 1), 2),
            "price": round(rnd.uniform(0.35, 0.75), 4),
            "timestamp": now - int(i * rnd.uniform(20, 90)),
            "name": "paperflow",
        })
    return rows


def fetch(url, timeout=10.0):
    """Drop-in replacement for upstream.fetch_json serving demo data."""
    if "/events" in url:
        return _build_events()
    if "/book" in url:
        token = url.split("token_id=")[1].split("&")[0]
        return _fake_book(token)
    if "/price-history" in url:
        token = url.split("market=")[1].split("&")[0]
        return {"history": _fake_history(token)}
    if "/trades" in url:
        cid = url.split("market=")[1].split("&")[0]
        return _fake_trades(cid)
    raise ValueError(f"demo fetch cannot handle: {url}")
