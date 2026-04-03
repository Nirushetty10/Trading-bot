"""
config.py — All configuration constants for the trading bot.
Edit this file to tune strategy parameters.
"""

from datetime import time

# ── Angel One credentials (set via .env or UI) ────────────────────────────────
ANGEL_CLIENT_ID  = ""
ANGEL_API_KEY    = ""
ANGEL_PASSWORD   = ""
ANGEL_TOTP_SECRET = ""

# ── Instrument tokens (Angel One symbol tokens) ───────────────────────────────
INSTRUMENTS = {
    "NIFTY": {
        "index_token":  "99926000",
        "symbol":       "Nifty 50",
        "exchange":     "NSE",
        "lot_size":     75,
        "strike_step":  50,
        "strategy":     "PDH_CPR_EMA_VWAP",   # Nifty uses this strategy
        "atr_avg":      155,
    },
    "BANKNIFTY": {
        "index_token":  "99926009",
        "symbol":       "Nifty Bank",
        "exchange":     "NSE",
        "lot_size":     15,
        "strike_step":  100,
        "strategy":     "ORB",                 # BankNifty uses ORB
        "atr_avg":      430,
    },
}

# ── Trading windows (IST) ─────────────────────────────────────────────────────
MARKET_OPEN        = time(9, 15)
ORB_END            = time(9, 45)    # ORB formation window end
PRIME_ENTRY_START  = time(9, 30)    # PDH/CPR strategy entry start
PRIME_ENTRY_END    = time(11, 30)   # Prime entry cutoff
SECOND_ENTRY_START = time(13, 30)   # Second entry window
SECOND_ENTRY_END   = time(13, 55)
HARD_EXIT_TIME     = time(14, 0)    # All positions closed by 2 PM
MARKET_CLOSE       = time(15, 30)

# ── Strategy parameters ───────────────────────────────────────────────────────
# ORB (BankNifty)
ORB_MIN_RANGE_PCT  = 0.002   # Skip if ORB < 0.2% (too tight)
ORB_MAX_RANGE_PCT  = 0.008   # Skip if ORB > 0.8% (too wide)
ORB_CANDLES        = 6       # 6 × 5-min = 30-min ORB window
ORB_CONFIRM_CLOSE  = True    # Require candle close (not just wick)

# PDH/CPR/EMA/VWAP (Nifty)
EMA_PERIOD         = 20
CPR_NARROW_PCT     = 0.15    # CPR width < 0.15% = narrow = trending
MIN_SETUP_SCORE    = 60      # Minimum confluence score to trade
VWAP_BUFFER_PCT    = 0.001   # 0.1% buffer around VWAP

# ── Risk management ───────────────────────────────────────────────────────────
CAPITAL_PER_TRADE  = 50_000   # ₹ per trade
MAX_DAILY_LOSS     = 5_000    # Stop all trading if daily loss hits this
MAX_POSITIONS      = 3        # Max concurrent open positions
MAX_TRADES_PER_DAY = 4        # Max trades in a single day

SL_PCT_OPTION      = 0.35    # 35% stop loss on option premium
TARGET1_PCT        = 0.60    # +60% → book 50% qty
TARGET2_PCT        = 1.00    # +100% → trail rest
TRAIL_FACTOR       = 0.85    # Trail SL at 85% of current premium

# ── VIX thresholds ────────────────────────────────────────────────────────────
VIX_MIN_BUY        = 10.0    # Below this: options too cheap, skip buying
VIX_MAX_BUY        = 22.0    # Above this: options too expensive, skip buying
VIX_TOKEN          = "13626" # Angel One token for India VIX

# ── Scan interval ─────────────────────────────────────────────────────────────
SCAN_INTERVAL_SEC  = 30      # Scan every 30 seconds

# ── Logging ───────────────────────────────────────────────────────────────────
LOG_DIR            = "logs"
LOG_LEVEL          = "INFO"

# ── Paper trade default ───────────────────────────────────────────────────────
DEFAULT_PAPER_TRADE = True   # Always start in paper mode
