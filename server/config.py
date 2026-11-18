"""Central configuration, tunable via environment variables."""
import os

# --- Server ---
HOST = os.environ.get("PM_HOST", "127.0.0.1")
PORT = int(os.environ.get("PM_PORT", "8080"))

# --- Data sources ---
GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"
DATA_API = "https://data-api.polymarket.com"

# Esports tag slugs scanned on Gamma. `esports` is the umbrella tag; the rest
# catch game-specific listings that are not always cross-tagged.
TAG_SLUGS = os.environ.get(
    "PM_TAGS",
    "esports,cs2,counter-strike-2,league-of-legends,valorant,dota-2,dota2,"
    "rocket-league,overwatch-2,rainbow-six,mlbb,call-of-duty,starcraft2",
).split(",")

# If upstream APIs are unreachable from your network you can still evaluate the
# terminal with a deterministic synthetic feed: PM_DEMO=1
DEMO = os.environ.get("PM_DEMO", "0") == "1"

# --- Refresh cadence (seconds) ---
REFRESH_FEED_SECONDS = int(os.environ.get("PM_FEED_SECS", "25"))
REFRESH_BOOK_SECONDS = float(os.environ.get("PM_BOOK_SECS", "4"))
REFRESH_TTL_SECONDS = int(os.environ.get("PM_TTL_SECS", "120"))

# Background worker enriches the N most-traded markets each cycle.
TOP_N_BOOKS = int(os.environ.get("PM_TOP_N", "30"))

# --- Signal engine weights (sum < 1, remainder is headroom) ---
W_MOMENTUM = 0.20
W_IMBALANCE = 0.25
W_FLOW = 0.25
W_EDGE = 0.30

# Fallback taker fee per share when a market omits feeSchedule (sports ≈ 3%).
DEFAULT_FEE_RATE = 0.03

# Momentum half-life / windows (seconds)
MOM_FAST_SPAN = 300.0
MOM_SLOW_SPAN = 2700.0

# Flow skew recency half-life (seconds)
FLOW_HALF_LIFE = 900.0
FAIR_HALF_LIFE = 1800.0

# Paper trading
PAPER_START_CASH = float(os.environ.get("PM_PAPER_CASH", "10000"))
PAPER_PATH = os.environ.get("PM_PAPER_PATH", "data/paper_account.json")
