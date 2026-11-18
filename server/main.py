"""Esports Terminal HTTP server (stdlib only: no pip install required).

Run:  python -m server.main          (or: python server/main.py)

Endpoints
---------
GET  /                          terminal UI
GET  /api/board                 enriched live board (all esports markets)
GET  /api/market/<conditionId>  full book, tape, history, signal breakdown
GET  /api/paper/account         paper account: cash, equity, positions, fills
POST /api/paper/order           {conditionId, outcomeIdx, side, shares}
POST /api/paper/reset
GET  /api/health                upstream status
"""
import json
import mimetypes
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server import config, demo_data, paper, signals  # noqa: E402
from server import polymarket as pm  # noqa: E402
from server.upstream import fetch_json  # noqa: E402

STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")


class Terminal:
    """Glue: feed + refresh loop + enrichment + paper engine."""

    def __init__(self):
        fetcher = demo_data.fetch if config.DEMO else fetch_json
        self.feed = pm.EsportsFeed(fetcher)
        self.paper = paper.PaperEngine(config.PAPER_PATH, config.PAPER_START_CASH)
        self._lock = threading.Lock()

    def start(self):
        t = threading.Thread(target=self._loop, name="refresher", daemon=True)
        t.start()

    def _loop(self):
        while True:
            try:
                self.feed.refresh_events()
                events, _ = self.feed.board_snapshot()
                top = []
                for ev in events:
                    for m in ev["markets"]:
                        if m["accepting"] and m["tokens"]:
                            top.append((m["volume24"], ev, m))
                top.sort(key=lambda x: -x[0])
                now = time.time()
                for _, ev, m in top[: config.TOP_N_BOOKS]:
                    self._enrich(ev, m, now)
            except Exception as exc:  # noqa: BLE001 - refresher must survive anything
                self.feed.last_error = str(exc)
            time.sleep(config.REFRESH_FEED_SECONDS)

    # ---------------- enrichment ----------------

    def _enrich(self, event, market, now):
        if not market["tokens"]:
            return None
        yes_id, no_id = market["tokens"][0], (market["tokens"][1] if len(market["tokens"]) > 1 else None)
        yes_book = self.feed.book(yes_id) or {"bids": [], "asks": []}
        no_book = self.feed.book(no_id) or {"bids": [], "asks": []} if no_id else {"bids": [], "asks": []}
        trades = self.feed.trades(market["conditionId"])
        history = self.feed.history(yes_id)

        bid = signals.best_bid_price(yes_book.get("bids"))
        ask = signals.best_ask_price(yes_book.get("asks"))
        if bid is None and market["bestBid"] is not None:
            bid = market["bestBid"]
        if ask is None and market["bestAsk"] is not None:
            ask = market["bestAsk"]
        mid = (bid + ask) / 2 if (bid is not None and ask is not None) else (
            market["prices"][0] if market["prices"] else None)
        if mid is None:
            return None

        sig = signals.build_signal(
            mid, bid, ask, market["tick"], yes_book, no_book, trades,
            history, market["volume24"], market["volume1wk"], market["feeRate"], now)

        market["live"] = {
            "bid": bid, "ask": ask, "mid": round(mid, 4),
            "noBid": signals.best_bid_price(no_book.get("bids")),
            "noAsk": signals.best_ask_price(no_book.get("asks")),
            "yesBook": yes_book, "noBook": no_book,
            "tradesCount": len(trades),
            "sig": sig,
        }
        market["_yesToken"] = yes_id
        market["_noToken"] = no_id
        market["_steam"], market["_spark"] = self._spark(history)
        return market

    @staticmethod
    def _spark(history, points=40):
        rows = [(h.get("t"), h.get("p")) for h in history or []
                if isinstance(h, dict) and "t" in h and "p" in h]
        if len(rows) < 3:
            return 0.0, []
        rows.sort()
        first, last = rows[0][1], rows[-1][1]
        steam = last - first
        out = rows[-points:]
        return round(steam, 4), out

    # ---------------- API payloads ----------------

    def board(self, params):
        events, ts = self.feed.board_snapshot()
        rows = []
        game_filter = (params.get("game") or ["all"])[0]
        min_vol = float((params.get("min_vol") or ["0"])[0])
        q_filter = (params.get("q") or [""])[0].lower()
        for ev in events:
            if game_filter != "all" and ev["game"].lower() != game_filter.lower():
                continue
            for m in ev["markets"]:
                if not m["accepting"]:
                    continue
                if m["volume24"] < min_vol:
                    continue
                if q_filter and q_filter not in m["question"].lower() and q_filter not in ev["title"].lower():
                    continue
                live = m.get("live") or {}
                sig = live.get("sig") or {}
                rows.append({
                    "conditionId": m["conditionId"],
                    "tokens": m["tokens"],
                    "eventTitle": ev["title"],
                    "question": m["question"],
                    "game": ev["game"],
                    "endDate": m["endDate"] or ev["endDate"],
                    "slug": ev["slug"],
                    "bid": live.get("bid"),
                    "ask": live.get("ask"),
                    "mid": live.get("mid"),
                    "noBid": live.get("noBid"),
                    "noAsk": live.get("noAsk"),
                    "chg1h": m["chg1h"],
                    "chg24h": m["chg24h"],
                    "volume24": m["volume24"],
                    "volume1wk": m["volume1wk"],
                    "liquidity": m["liquidity"],
                    "spread": m["spread"],
                    "tick": m["tick"],
                    "feeRate": m["feeRate"],
                    "steam": m.get("_steam", 0.0),
                    "spark": m.get("_spark", []),
                    "sig": {k: sig.get(k) for k in (
                        "raw", "conviction", "action", "side", "entry", "edgePts",
                        "kelly", "vmult", "liq", "fair", "insight", "factors")},
                    "stale": time.time() - ts > 90,
                })
        rows.sort(key=lambda r: -r["sig"].get("conviction", 0))
        return {
            "mode": "demo" if config.DEMO else "live",
            "ts": int(ts),
            "error": self.feed.last_error,
            "rows": rows,
        }

    def market_detail(self, condition_id):
        events, _ = self.feed.board_snapshot()
        for ev in events:
            for m in ev["markets"]:
                if m["conditionId"] == condition_id:
                    self._enrich(ev, m, time.time())
                    tapes = self.feed.trades(condition_id)[-60:]
                    return {
                        "mode": "demo" if config.DEMO else "live",
                        "event": {"title": ev["title"], "slug": ev["slug"], "game": ev["game"],
                                  "endDate": ev["endDate"], "volume24": ev["volume24"]},
                        "market": {k: v for k, v in m.items() if not k.startswith("_") and k != "live"},
                        "live": {k: v for k, v in (m.get("live") or {}).items()},
                        "tape": [
                            {"side": t.get("side"), "price": t.get("price"),
                             "size": t.get("size"), "ts": t.get("timestamp"),
                             "name": t.get("name")}
                            for t in tapes
                        ],
                    }
        return None

    def paper_order(self, body):
        side = str(body.get("side", "BUY")).upper()
        shares = int(body.get("shares", 0))
        events, _ = self.feed.board_snapshot()
        wanted_token = body.get("token")
        wanted_cid = body.get("conditionId")
        outcome_idx = body.get("outcomeIdx")

        def matches(m):
            if wanted_token:
                return wanted_token in m["tokens"]
            if wanted_cid and m["conditionId"] == wanted_cid:
                if outcome_idx is not None and m["tokens"]:
                    return m["tokens"][int(outcome_idx)] is not None and int(outcome_idx) < len(m["tokens"])
                return bool(m["tokens"])
            return False

        for ev in events:
            for m in ev["markets"]:
                if not matches(m):
                    continue
                token = wanted_token if wanted_token else m["tokens"][int(outcome_idx or 0)]
                idx = m["tokens"].index(token)
                book = self.feed.book(token) or {"bids": [], "asks": []}
                label = ev["title"] + " · " + (m["outcomes"][idx] if idx < len(m["outcomes"]) else "?")
                return self.paper.execute(token, side, shares, book, {
                    "conditionId": m["conditionId"],
                    "label": label,
                    "question": m["question"],
                    "entrySide": m["outcomes"][idx] if idx < len(m["outcomes"]) else "?",
                    "endDate": m["endDate"] or ev["endDate"],
                    "feeRate": m["feeRate"],
                })
        raise LookupError("unknown market")


TERMINAL = Terminal()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # quieter logs
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    # ---------------- plumbing ----------------

    def _json(self, obj, status=200):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _static(self, rel_path):
        full = os.path.normpath(os.path.join(STATIC_DIR, rel_path))
        if not full.startswith(STATIC_DIR) or not os.path.isfile(full):
            self._json({"error": "not found"}, 404)
            return
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        with open(full, "rb") as fh:
            body = fh.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # ---------------- routing ----------------

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)
        try:
            if path == "/" or path == "/index.html":
                self._static("index.html")
            elif path.startswith("/static/"):
                self._static(path[len("/static/"):])
            elif path == "/api/board":
                self._json(TERMINAL.board(params))
            elif path.startswith("/api/market/"):
                detail = TERMINAL.market_detail(path.rsplit("/", 1)[1])
                self._json(detail if detail else {"error": "unknown market"},
                           200 if detail else 404)
            elif path == "/api/paper/account":
                self._json(TERMINAL.paper.account(_mark_fn))
            elif path == "/api/health":
                events, ts = TERMINAL.feed.board_snapshot()
                self._json({
                    "mode": "demo" if config.DEMO else "live",
                    "events": len(events),
                    "boardFetchedSecsAgo": round(time.time() - ts, 1) if ts else None,
                    "upstreamError": TERMINAL.feed.last_error,
                })
            else:
                self._json({"error": "not found"}, 404)
        except BrokenPipeError:
            pass
        except Exception as exc:  # noqa: BLE001
            self._json({"error": str(exc)}, 500)

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
            if self.path == "/api/paper/order":
                self._json(TERMINAL.paper_order(body))
            elif self.path == "/api/paper/reset":
                self._json(TERMINAL.paper.reset())
            else:
                self._json({"error": "not found"}, 404)
        except (ValueError, LookupError) as exc:
            self._json({"error": str(exc)}, 400)
        except Exception as exc:  # noqa: BLE001
            self._json({"error": str(exc)}, 500)


def _mark_fn(token_id):
    book = TERMINAL.feed.book(token_id)
    if not book:
        return None
    bid = signals.best_bid_price(book.get("bids"))
    ask = signals.best_ask_price(book.get("asks"))
    if bid is not None and ask is not None:
        return (bid + ask) / 2
    return bid if bid is not None else ask


def main():
    server = ThreadingHTTPServer((config.HOST, config.PORT), Handler)
    TERMINAL.start()
    print(f"Esports Terminal on http://{config.HOST}:{config.PORT} "
          f"({'DEMO' if config.DEMO else 'LIVE'} mode)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
