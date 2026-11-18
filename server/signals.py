"""Signal engine. Pure, dependency-free functions, fully unit-testable.

Every market's factors are computed on the *YES* outcome; `build_signal` maps
the combined view onto an actionable side (BUY YES vs BUY NO).
"""
import math


ACTION_STRONG = "STRONG BUY"
ACTION_BUY = "BUY"
ACTION_WATCH = "WATCH"
ACTION_NONE = "NO TRADE"


# --------------------------------------------------------------------------
# Book helpers
# --------------------------------------------------------------------------

def clean_levels(levels):
    """Normalize raw CLOB levels to (price, size) tuples.

    Accepts CLOB dicts ({"price", "size"}) as well as pre-cleaned tuples.
    """
    out = []
    for lvl in levels or []:
        if isinstance(lvl, dict):
            pair = (lvl.get("price"), lvl.get("size"))
        elif isinstance(lvl, (tuple, list)) and len(lvl) == 2:
            pair = (lvl[0], lvl[1])
        else:
            continue
        try:
            p, s = float(pair[0]), float(pair[1])
        except (TypeError, ValueError):
            continue
        if s > 0 and 0.0 <= p <= 1.0:
            out.append((round(p, 6), s))
    return out


def best_bid_price(levels):
    return max((p for p, _ in clean_levels(levels)), default=None)


def best_ask_price(levels):
    return min((p for p, _ in clean_levels(levels)), default=None)


def depth_within(levels, band, is_bid):
    """Total size resting within `band` price points of the best level."""
    lv = clean_levels(levels)
    if not lv:
        return 0.0
    best = max(p for p, _ in lv) if is_bid else min(p for p, _ in lv)
    return sum(s for p, s in lv if abs(best - p) <= band + 1e-9)


def notional_depth(levels, count=8):
    """USD notional of the top `count` levels: sum(price * size)."""
    lv = clean_levels(levels)[:count]
    return sum(p * s for p, s in lv)


def merge_yes_book(yes_book, no_book):
    """Synthetic full YES book via minting parity: 1.00 = YES + NO.

    A NO bid at p pays off in a way equivalent to supplying YES at 1-p, and a
    NO ask at p is equivalent to a bid for YES at 1-p. Merging tightens the
    effective spread for binary markets and feeds the imbalance signal.
    """
    yes_bids = clean_levels((yes_book or {}).get("bids"))
    yes_asks = clean_levels((yes_book or {}).get("asks"))
    syn_bids = [(round(1 - p, 6), s) for p, s in clean_levels((no_book or {}).get("asks"))]
    syn_asks = [(round(1 - p, 6), s) for p, s in clean_levels((no_book or {}).get("bids"))]
    return {
        "bids": sorted(yes_bids + syn_bids, key=lambda x: x[0], reverse=True),
        "asks": sorted(yes_asks + syn_asks, key=lambda x: x[0]),
    }


def imbalance(yes_book, no_book, band=0.03):
    """Skew of near-touch merged-book depth: (bid - ask) / total, in [-1, 1].

    Returns (skew, bid_size, ask_size, merged_book).
    """
    merged = merge_yes_book(yes_book, no_book)
    bid = depth_within(merged["bids"], band, is_bid=True)
    ask = depth_within(merged["asks"], band, is_bid=True)
    total = bid + ask
    if total <= 0:
        return 0.0, 0.0, 0.0, merged
    return (bid - ask) / total, bid, ask, merged


def walk_book(levels, shares, is_bid):
    """VWAP price for taking `shares` from the book. Returns (fills, leftover)."""
    lv = clean_levels(levels)
    lv = sorted(lv, key=lambda x: (-x[0] if is_bid else x[0]))
    remaining, cost = shares, 0.0
    for p, s in lv:
        take = min(remaining, s)
        cost += take * p
        remaining -= take
        if remaining <= 1e-9:
            break
    filled = shares - max(remaining, 0.0)
    return (cost / filled if filled > 0 else None, max(remaining, 0.0))


# --------------------------------------------------------------------------
# Tape (taker flow + fair value)
# --------------------------------------------------------------------------

def flow_skew(trades, now_ts, half_life=900.0):
    """Recency-weighted notional skew of taker buys vs sells, in [-1, 1]."""
    buys = sells = 0.0
    for t in trades or []:
        try:
            ts = float(t.get("timestamp") or 0)
            size = float(t.get("size") or 0)
            price = float(t.get("price") or 0)
        except (TypeError, ValueError):
            continue
        if not (0 < price < 1) or size <= 0:
            continue
        weight = math.exp(-max(0.0, now_ts - ts) / half_life)
        notional = size * price * weight
        side = str(t.get("side") or "").upper()
        if side == "BUY":
            buys += notional
        elif side == "SELL":
            sells += notional
    total = buys + sells
    return (buys - sells) / total if total > 0 else 0.0


def fair_value(trades, now_ts, half_life=1800.0):
    """Recency-weighted median of trade prices: robust microstructure fair value."""
    pts = []
    for t in trades or []:
        try:
            ts = float(t.get("timestamp") or 0)
            price = float(t.get("price") or 0)
        except (TypeError, ValueError):
            continue
        if 0 < price < 1:
            pts.append((price, math.exp(-max(0.0, now_ts - ts) / half_life)))
    if not pts:
        return None
    pts.sort()
    total_w = sum(w for _, w in pts)
    if total_w <= 0:
        return None
    acc = 0.0
    for price, w in pts:
        acc += w
        if acc >= total_w / 2:
            return price
    return pts[-1][0]


# --------------------------------------------------------------------------
# Momentum
# --------------------------------------------------------------------------

def _ewa(rows, span, now_ts):
    """Exponentially weighted average of price rows [(unix_ts, price), ...]."""
    if not rows:
        return None
    alpha = 1.0 - math.exp(-1.0 / span)
    num = den = 0.0
    for ts, price in rows:
        age = max(0.0, now_ts - ts)
        w = (1.0 - alpha) ** (age / 60.0)
        num += price * w
        den += w
    return num / den if den > 0 else rows[-1][1]


def momentum_score(history, now_ts, fast_span=300.0, slow_span=2700.0):
    """Fast-vs-slow EWA divergence of the 1-min price series, tanh-normalized."""
    rows = []
    for h in history or []:
        try:
            rows.append((float(h["t"]), float(h["p"])))
        except (KeyError, TypeError, ValueError):
            continue
    rows = [(t, p) for t, p in rows if 0 < p < 1]
    if len(rows) < 4:
        return 0.0
    rows.sort()
    prices = [p for _, p in rows]
    mean = sum(prices) / len(prices)
    var = sum((p - mean) ** 2 for p in prices) / len(prices)
    scale = max(math.sqrt(var), 0.004)
    fast = _ewa(rows[-60:], fast_span, now_ts)
    slow = _ewa(rows[-400:], slow_span, now_ts)
    if fast is None or slow is None:
        return 0.0
    return max(-1.0, min(1.0, math.tanh((fast - slow) / (2.0 * scale))))


# --------------------------------------------------------------------------
# Volume / liquidity / edge / sizing
# --------------------------------------------------------------------------

def volume_shock(volume24, volume1wk):
    """24h trading rate vs trailing 1-week daily average; clipped to [0, 10]."""
    wk_avg = (volume1wk or 0) / 7.0
    if wk_avg <= 0:
        return 3.0 if (volume24 or 0) > 1000 else 1.0
    return max(0.0, min(10.0, (volume24 or 0) / wk_avg))


def liquidity_score(spread, tick, bid_usd, ask_usd, volume24):
    """0-100 tradability score: tightness (40%) + depth (35%) + activity (25%)."""
    if spread is None or not tick or tick <= 0:
        return 0.0
    ticks = spread / tick
    if ticks <= 1.001:
        tight = 1.0
    elif ticks <= 3:
        tight = 0.7
    elif ticks <= 8:
        tight = 0.4
    else:
        tight = 0.15
    depth = min(1.0, (bid_usd + ask_usd) / 2000.0)
    activity = min(1.0, (volume24 or 0) / 20000.0)
    return round(100 * (0.40 * tight + 0.35 * depth + 0.25 * activity))


def fee_per_share(price, rate):
    """Polymarket taker fee model: rate * min(price, 1 - price) per share."""
    return max(0.0, rate) * min(price, 1.0 - price)


def edge(fair, cost, rate=0.03):
    """Net expected edge (probability points) for buying at `cost`.

    Positive = modelled fair value exceeds the after-fee entry price.
    """
    if fair is None or cost is None or not (0 < cost < 1):
        return None
    return fair - cost - fee_per_share(cost, rate)


def kelly_fraction(fair, price, cap=0.25):
    """Half-Kelly stake fraction for a binary contract paying 1.00 at expiry."""
    if fair is None or price is None or not (0 < price < 1) or not (0 < fair < 1):
        return 0.0
    return max(0.0, min(cap, (fair - price) / (1.0 - price) / 2.0))


# --------------------------------------------------------------------------
# Composite
# --------------------------------------------------------------------------

def build_signal(mid, best_bid, best_ask, tick, yes_book, no_book, trades,
                 history, volume24, volume1wk, fee_rate, now_ts):
    """Assemble weighted factors into one actionable view for a market.

    Sculpted for binary esports matchups: `no_book` participates through
    minting parity, the tape provides fair value and flow, history momentum.
    """
    imb, bid_size, ask_size, merged = imbalance(yes_book, no_book)
    mom = momentum_score(history, now_ts)
    flow = flow_skew(trades, now_ts)
    fair = fair_value(trades, now_ts)
    vmult = volume_shock(volume24, volume1wk)

    m_bid = merged["bids"][0][0] if merged["bids"] else None
    m_ask = merged["asks"][0][0] if merged["asks"] else None
    liq_bid = notional_depth(merged["bids"])
    liq_ask = notional_depth(merged["asks"])
    if m_bid is not None and m_ask is not None:
        spread = m_ask - m_bid
    elif best_bid is not None and best_ask is not None:
        spread = best_ask - best_bid
    else:
        spread = None
    liq = liquidity_score(spread, tick or 0.001, liq_bid, liq_ask, volume24)

    factors = {"momentum": mom, "imbalance": imb, "flow": flow, "edge": 0.0}
    side, entry_price, edge_pts = None, None, None

    if fair is not None and m_ask is not None:
        edge_y = edge(fair, m_ask, fee_rate)
        n_entry = round(1.0 - m_bid, 6) if m_bid is not None and 0 < m_bid < 1 else None
        edge_n = edge(1.0 - fair, n_entry, fee_rate) if n_entry is not None else None

        e_y = edge_y if edge_y is not None else -1.0
        e_n = edge_n if edge_n is not None else -1.0
        if e_y >= e_n:
            side, edge_pts = "YES", edge_y
            entry_price = m_ask
        else:
            side, edge_pts = "NO", edge_n
            entry_price = n_entry
        factors["edge"] = max(-1.0, min(1.0, (edge_pts if edge_pts is not None else 0.0) / 0.10))
        raw = 0.20 * mom + 0.25 * imb + 0.25 * flow + 0.30 * factors["edge"]
    else:
        raw = 0.20 * mom + 0.25 * imb + 0.25 * flow
        side = "YES" if raw >= 0 else "NO"
        if side == "NO" and m_bid is not None:
            entry_price = round(1.0 - m_bid, 6)

    raw = max(-1.5, min(1.5, raw * (1.0 + 0.25 * min(vmult, 4.0))))
    conviction = int(round(min(100.0, abs(raw) / 1.2 * 100.0)))

    if liq < 40 or abs(raw) < 0.15:
        action = ACTION_NONE
        side = None
    elif abs(raw) >= 0.75:
        action = ACTION_STRONG
    elif abs(raw) >= 0.45:
        action = ACTION_BUY
    else:
        action = ACTION_WATCH

    fair_side = None
    if fair is not None and side:
        fair_side = fair if side == "YES" else 1.0 - fair
    kelly = kelly_fraction(fair_side, entry_price)

    return {
        "raw": round(raw, 4),
        "conviction": conviction,
        "action": action if side else (ACTION_WATCH if conviction >= 30 else ACTION_NONE),
        "side": side if action != ACTION_NONE else None,
        "entry": entry_price,
        "factors": {k: round(v, 3) for k, v in factors.items()},
        "fair": None if fair is None else round(fair, 4),
        "edgePts": None if edge_pts is None else round(edge_pts, 4),
        "kelly": round(kelly, 4),
        "vmult": round(vmult, 2),
        "liq": liq,
        "insight": _describe(mom, imb, flow, fair, vmult, edge_pts, liq),
    }


def _describe(mom, imb, flow, fair, vmult, edge_pts, liq):
    bits = []
    if abs(mom) > 0.15:
        bits.append("1h momentum " + ("up" if mom > 0 else "down"))
    if abs(imb) > 0.2:
        bits.append("book {:d}% {}".format(int(abs(imb) * 100), "bid" if imb > 0 else "ask"))
    if abs(flow) > 0.2:
        bits.append("tape {:d}% {}".format(int(abs(flow) * 100), "buying" if flow > 0 else "selling"))
    if vmult >= 2:
        bits.append("volume x{:.1f}".format(vmult))
    if edge_pts is not None:
        bits.append("edge {:+.1f}pts".format(edge_pts * 100))
    if liq < 40:
        bits.append("thin book")
    return ", ".join(bits) if bits else "quiet tape"
