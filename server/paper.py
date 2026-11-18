"""Paper trading engine: cash account, positions, marks, P&L, persistence.

Fills always walk the live book snapshot passed in by the caller, so slippage
on size is modelled realistically. Pure logic + JSON persistence; no deps.
"""
import json
import os
import threading
import time

from . import signals


class PaperEngine:
    def __init__(self, path, start_cash, clock=time.time):
        self.path = path
        self.start_cash = float(start_cash)
        self.clock = clock
        self._lock = threading.Lock()
        self.state = self._load()

    # ---------------- persistence ----------------

    def _load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, json.JSONDecodeError):
            pass
        return {
            "cash": self.start_cash,
            "opened": self.clock(),
            "filled": 0,
            "realized": 0.0,
            "positions": {},   # token_id -> {meta..., shares, avgCost}
            "history": [],
        }

    def save(self):
        directory = os.path.dirname(self.path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.state, fh, indent=1)
        os.replace(tmp, self.path)

    def reset(self):
        with self._lock:
            self.state = {
                "cash": self.start_cash,
                "opened": self.clock(),
                "filled": 0,
                "realized": 0.0,
                "positions": {},
                "history": [],
            }
            self.save()
        return self.state

    # ---------------- execution ----------------

    def execute(self, token_id, side, shares, book, meta):
        """Buy or sell `shares` into the live book snapshot.

        side BUY consumes asks, SELL consumes bids (requires an open position).
        Returns dict with fills, vwap, fee, cash delta.
        """
        shares = int(shares)
        if shares <= 0:
            raise ValueError("shares must be a positive integer")
        levels = (book or {}).get("asks" if side == "BUY" else "bids")
        vwap, leftover = signals.walk_book(levels, shares, is_bid=(side == "SELL"))
        filled = shares - leftover
        if filled <= 0 or vwap is None:
            raise ValueError("book has no liquidity at this level; order rest unfilled")

        meta = dict(meta or {})
        if not (0 < vwap < 1):
            raise ValueError("fill price out of range; book data invalid")

        with self._lock:
            st = self.state
            pos = st["positions"].get(token_id)
            if side == "SELL":
                if pos is None:
                    raise ValueError("no position to sell on this token")
                filled = min(filled, int(pos["shares"]))
                vwap, _ = signals.walk_book(levels, filled, is_bid=True)
                if filled <= 0 or vwap is None:
                    raise ValueError("no sellable size in book")

            fee_rate = float(meta.get("feeRate") or 0.0)
            fee = signals.fee_per_share(vwap, fee_rate) * filled
            cash_delta = (-(vwap * filled) - fee) if side == "BUY" else (vwap * filled - fee)

            if side == "BUY":
                if st["cash"] < vwap * filled + fee:
                    raise ValueError("insufficient paper cash")
                st["cash"] += cash_delta
                if pos is None:
                    st["positions"][token_id] = {
                        "meta": meta, "shares": filled, "avgCost": vwap,
                    }
                else:
                    total_cost = pos["avgCost"] * pos["shares"] + vwap * filled
                    pos["shares"] += filled
                    pos["avgCost"] = total_cost / pos["shares"]
                    pos["meta"] = meta or pos["meta"]
            else:
                proceeds = vwap * filled - fee
                st["cash"] += proceeds
                realized = (vwap - pos["avgCost"]) * filled - fee
                st["realized"] += realized
                pos["shares"] -= filled
                if pos["shares"] <= 1e-9:
                    del st["positions"][token_id]

            st["filled"] += 1
            st["history"].append({
                "ts": self.clock(),
                "token": token_id,
                "conditionId": meta.get("conditionId"),
                "label": meta.get("label"),
                "side": side,
                "shares": filled,
                "price": round(vwap, 6),
                "fee": round(fee, 4),
                "realized": round(realized, 4) if side == "SELL" else None,
            })
            st["history"] = st["history"][-500:]
            self.save()
            return {
                "side": side, "shares": filled, "price": round(vwap, 6),
                "fee": round(fee, 4), "cash": round(st["cash"], 2),
            }

    # ---------------- marking ----------------

    def account(self, mark_fn):
        """mark_fn(token_id) -> mid price or None. Returns full account snapshot."""
        with self._lock:
            st = self.state
            positions, unrealized = [], 0.0
            for token_id, pos in st["positions"].items():
                mark = mark_fn(token_id)
                mark = mark if mark is not None else pos["avgCost"]
                upnl = (mark - pos["avgCost"]) * pos["shares"]
                unrealized += upnl
                positions.append({
                    "token": token_id,
                    "label": pos["meta"].get("label"),
                    "question": pos["meta"].get("question"),
                    "side": pos["meta"].get("entrySide") or "YES",
                    "shares": pos["shares"],
                    "avgCost": round(pos["avgCost"], 4),
                    "mark": round(mark, 4),
                    "upnl": round(upnl, 2),
                    "endTime": pos["meta"].get("endDate"),
                })
            positions.sort(key=lambda p: (p["endTime"] or "", p["token"]))
            equity = st["cash"] + sum(p["mark"] * p["shares"] for p in positions)
            return {
                "cash": round(st["cash"], 2),
                "equity": round(equity, 2),
                "startCash": self.start_cash,
                "realized": round(st["realized"], 2),
                "unrealized": round(unrealized, 2),
                "returnPct": round(100 * (equity - self.start_cash) / self.start_cash, 2),
                "filled": st["filled"],
                "positions": positions,
                "history": list(reversed(st["history"][:50])),
                "opened": st["opened"],
            }
