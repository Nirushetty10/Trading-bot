"""
scanner.py — Main trading engine.
Orchestrates: data fetch → indicator calc → signal detect → order → manage exits.

Strategy routing:
  NIFTY     → PDH / CPR / EMA20 / VWAP
  BANKNIFTY → ORB (30-min Opening Range Breakout)
"""

import asyncio
import asyncio
from datetime import datetime, date, time
from typing import List, Dict, Optional
import pandas as pd
import numpy as np
import pytz
from loguru import logger

from config import (
    INSTRUMENTS, SCAN_INTERVAL_SEC, HARD_EXIT_TIME,
    MARKET_OPEN, MARKET_CLOSE, EMA_PERIOD, VIX_MIN_BUY, VIX_MAX_BUY,
    DEFAULT_PAPER_TRADE,
)
from angel_api import angel
from indicators import (
    ema, atr, vwap, get_pdh_pdl, calculate_cpr, calculate_orb,
    detect_pdh_breakout, detect_ema_pullback, detect_orb_breakout,
)
from risk_manager import risk_manager, Position

IST = pytz.timezone("Asia/Kolkata")


# ── Global state ──────────────────────────────────────────────────────────────
class BotState:
    def __init__(self):
        self.running:         bool  = False
        self.paper_trade:     bool  = DEFAULT_PAPER_TRADE
        self.vix:             float = 14.5
        self.signals_log:     List[dict] = []
        self.alerts:          List[dict] = []
        self.market_data:     Dict[str, dict] = {}
        self.pdh_pdl:         Dict[str, dict] = {}
        self.cpr:             Dict[str, dict] = {}
        self.orb:             Dict[str, dict] = {}
        self.candles:         Dict[str, pd.DataFrame] = {}
        self.last_scan:       Optional[datetime] = None
        self.trading_day:     Optional[date] = None
        self.day_initialized: bool = False

    def add_signal(self, signal: dict):
        self.signals_log.insert(0, signal)
        if len(self.signals_log) > 100:
            self.signals_log = self.signals_log[:100]

    def add_alert(self, msg: str, level: str = "info"):
        self.alerts.insert(0, {
            "time":    datetime.now(IST).strftime("%H:%M:%S"),
            "message": msg,
            "level":   level,
        })
        if len(self.alerts) > 50:
            self.alerts = self.alerts[:50]


state = BotState()


# ── Market hours check ────────────────────────────────────────────────────────
def ist_now() -> datetime:
    return datetime.now(IST)

def is_market_open() -> bool:
    now = ist_now().time()
    today = ist_now().weekday()
    if today >= 5:  # Weekend
        return False
    return MARKET_OPEN <= now <= MARKET_CLOSE

def is_trading_window() -> bool:
    now = ist_now().time()
    from config import PRIME_ENTRY_START, SECOND_ENTRY_END
    return PRIME_ENTRY_START <= now <= SECOND_ENTRY_END

def should_exit_all() -> bool:
    return ist_now().time() >= HARD_EXIT_TIME


# ── Data pipeline ─────────────────────────────────────────────────────────────
async def refresh_candles(instrument: str) -> pd.DataFrame:
    """Fetch latest 5-min candles (last 5 days for PDH/PDL)."""
    cfg = INSTRUMENTS[instrument]
    df  = angel.get_candles(cfg["index_token"], interval="FIVE_MINUTE", days=5)

    if df.empty:
        logger.warning(f"No candle data for {instrument}")
        return pd.DataFrame()

    state.candles[instrument] = df
    return df


async def initialize_day(instrument: str, df: pd.DataFrame):
    """
    Run once per trading day per instrument.
    Calculates PDH/PDL, CPR, ORB window.
    """
    today = ist_now().date()
    cfg   = INSTRUMENTS[instrument]

    # PDH / PDL
    levels = get_pdh_pdl(df)
    state.pdh_pdl[instrument] = levels
    logger.info(f"{instrument} PDH={levels['pdh']} PDL={levels['pdl']} PDC={levels['pdc']}")

    # CPR (for Nifty)
    if levels["pdh"] and levels["pdl"] and levels["pdc"]:
        cpr = calculate_cpr(levels["pdh"], levels["pdl"], levels["pdc"])
        state.cpr[instrument] = cpr
        logger.info(f"{instrument} CPR pivot={cpr['pivot']} TC={cpr['tc']} BC={cpr['bc']} "
                    f"width={cpr['width_pct']:.3f}% {'NARROW' if cpr['narrow'] else 'WIDE'}")

    # ORB (for BankNifty — filled during 9:15–9:45)
    state.orb[instrument] = {"valid": False}

    state.add_alert(f"{instrument} levels loaded: PDH={levels['pdh']:.0f} PDL={levels['pdl']:.0f}", "info")


# ── VIX filter ────────────────────────────────────────────────────────────────
async def check_vix() -> bool:
    vix = angel.get_vix()
    state.vix = vix
    ok = VIX_MIN_BUY <= vix <= VIX_MAX_BUY
    if not ok:
        state.add_alert(f"VIX {vix:.1f} outside range [{VIX_MIN_BUY}–{VIX_MAX_BUY}] — no trades", "warning")
    return ok


# ── Option premium estimator ──────────────────────────────────────────────────
def estimate_option_premium(index_price: float, strike: int,
                             opt_type: str, atr_val: float,
                             mins_to_expiry: int = 270) -> float:
    """
    ATR-based option premium proxy.
    Used for paper trades and position sizing when real option LTP unavailable.
    """
    intrinsic = max(0, index_price - strike) if opt_type == "CE" else max(0, strike - index_price)
    time_val  = atr_val * np.sqrt(max(mins_to_expiry, 1) / 375) * 0.45
    return max(intrinsic + time_val, atr_val * 0.05)


def get_atm_strike(price: float, step: int) -> int:
    return round(price / step) * step


def minutes_to_hard_exit() -> int:
    now = ist_now()
    exit_dt = now.replace(hour=HARD_EXIT_TIME.hour, minute=HARD_EXIT_TIME.minute,
                           second=0, microsecond=0)
    return max(0, int((exit_dt - now).total_seconds() / 60))


# ── Strategy: PDH + CPR + EMA + VWAP (Nifty) ─────────────────────────────────
async def run_nifty_strategy(df: pd.DataFrame) -> Optional[dict]:
    instrument = "NIFTY"
    cfg        = INSTRUMENTS[instrument]
    today      = ist_now().date()

    levels = state.pdh_pdl.get(instrument, {})
    cpr    = state.cpr.get(instrument, {})

    if not levels.get("pdh") or not cpr:
        return None

    # Build today's candles
    today_df = df[df.index.date == today].copy()
    if today_df.empty:
        return None

    # Indicators on full df
    df_full          = df.copy()
    df_full["ema20"] = ema(df_full["close"], EMA_PERIOD)
    df_full["vwap"]  = vwap(df_full)

    ema20_series = df_full["ema20"]
    vwap_series  = df_full["vwap"]

    # ── Detect setups ──────────────────────────────────────────────────────────
    # 1. PDH Breakout / PDL Breakdown
    setup = detect_pdh_breakout(
        today_df, levels["pdh"], levels["pdl"], cpr,
        ema20_series, vwap_series
    )

    # 2. EMA pullback (secondary, only if trend already established)
    if setup is None and len(risk_manager.closed_positions) > 0:
        last_closed = risk_manager.closed_positions[-1]
        if last_closed.instrument == instrument and last_closed.realised_pnl > 0:
            setup = detect_ema_pullback(
                today_df, ema20_series, vwap_series, last_closed.direction
            )

    if setup is None:
        return None

    setup["instrument"] = instrument
    return setup


# ── Strategy: ORB (BankNifty) ─────────────────────────────────────────────────
async def run_banknifty_strategy(df: pd.DataFrame) -> Optional[dict]:
    instrument = "BANKNIFTY"
    cfg        = INSTRUMENTS[instrument]
    today      = ist_now().date()

    today_df = df[df.index.date == today].copy()
    if today_df.empty:
        return None

    # Update ORB window (9:15–9:45) — only calculate once after 9:45
    now_time = ist_now().time()
    from config import ORB_END
    if now_time >= ORB_END and not state.orb.get(instrument, {}).get("valid"):
        orb = calculate_orb(df, today)
        state.orb[instrument] = orb
        if orb["valid"]:
            logger.info(f"BANKNIFTY ORB: high={orb['high']} low={orb['low']} "
                        f"range={orb['range_pct']:.3f}%")
            state.add_alert(
                f"BankNifty ORB set: {orb['low']:.0f}–{orb['high']:.0f} "
                f"({orb['range_pct']:.2f}%)", "info"
            )
        else:
            state.add_alert(f"BankNifty ORB invalid (range {orb.get('range_pct','?')}%)", "warning")

    orb = state.orb.get(instrument, {})
    if not orb.get("valid"):
        return None

    # VWAP
    df_full         = df.copy()
    df_full["vwap"] = vwap(df_full)
    vwap_series     = df_full["vwap"]

    setup = detect_orb_breakout(today_df, orb, vwap_series)
    if setup:
        setup["instrument"] = instrument
    return setup


# ── Execute trade ─────────────────────────────────────────────────────────────
async def execute_trade(setup: dict):
    instrument = setup["instrument"]
    cfg        = INSTRUMENTS[instrument]
    direction  = setup["direction"]

    # Gate checks
    ok, reason = risk_manager.can_trade
    if not ok:
        logger.info(f"Trade blocked: {reason}")
        return

    if risk_manager.instrument_has_position(instrument):
        logger.info(f"Already have {instrument} position, skipping")
        return

    mins_left = minutes_to_hard_exit()
    if mins_left < 30:
        logger.info("Less than 30 min to hard exit, skipping new trade")
        return

    # Get current index price
    ltp = angel.get_ltp(cfg["exchange"], cfg["symbol"], cfg["index_token"])
    if ltp <= 0:
        ltp = setup.get("close_price", 0)
    if ltp <= 0:
        logger.error(f"Cannot get LTP for {instrument}")
        return

    # Strike and symbol
    strike      = get_atm_strike(ltp, cfg["strike_step"])
    expiry      = angel.nearest_expiry(3 if instrument == "NIFTY" else 2)
    opt_symbol  = angel.build_option_symbol(instrument, expiry, strike, direction)

    # Premium estimate
    atr_val      = cfg["atr_avg"]
    entry_prem   = estimate_option_premium(ltp, strike, direction, atr_val, mins_left)

    # Place order
    if state.paper_trade:
        order_resp = {"status": True, "order_id": f"PAPER_{int(datetime.now().timestamp())}",
                      "simulated": True}
    else:
        order_resp = angel.place_order(
            tradingsymbol=opt_symbol, token="",
            transaction_type="BUY", quantity=cfg["lot_size"],
        )

    if not order_resp.get("status"):
        state.add_alert(f"Order failed: {order_resp.get('message')}", "error")
        return

    # Build position
    pos = risk_manager.build_position(
        symbol=opt_symbol, token="",
        instrument=instrument, direction=direction,
        setup=setup["setup"], lot_size=cfg["lot_size"],
        entry_premium=entry_prem,
        paper=state.paper_trade,
        order_id=order_resp["order_id"],
    )

    signal_log = {
        "time":       datetime.now(IST).strftime("%H:%M:%S"),
        "instrument": instrument,
        "setup":      setup["setup"],
        "direction":  direction,
        "strike":     strike,
        "symbol":     opt_symbol,
        "score":      setup.get("score", 0),
        "reasons":    setup.get("reasons", []),
        "entry_prem": round(entry_prem, 2),
        "sl":         round(pos.sl_price, 2),
        "t1":         round(pos.target1_price, 2),
        "t2":         round(pos.target2_price, 2),
        "mode":       "PAPER" if state.paper_trade else "LIVE",
    }
    state.add_signal(signal_log)
    state.add_alert(
        f"{'[PAPER]' if state.paper_trade else '[LIVE]'} {instrument} "
        f"{direction} {setup['setup']} entry=₹{entry_prem:.0f} "
        f"SL=₹{pos.sl_price:.0f} T1=₹{pos.target1_price:.0f}",
        "success"
    )
    logger.success(f"Trade executed: {signal_log}")


# ── Manage open positions ──────────────────────────────────────────────────────
async def manage_positions(df_map: Dict[str, pd.DataFrame]):
    """Update all open position prices and check exits."""
    positions_to_exit = []

    for pos in list(risk_manager.open_positions):
        instrument = pos.instrument
        cfg        = INSTRUMENTS[instrument]

        # Get current LTP
        ltp = angel.get_ltp(cfg["exchange"], cfg["symbol"], cfg["index_token"])
        if ltp <= 0 and instrument in df_map:
            df = df_map[instrument]
            if not df.empty:
                ltp = float(df["close"].iloc[-1])

        mins_left    = minutes_to_hard_exit()
        current_prem = estimate_option_premium(ltp, 0, pos.direction, cfg["atr_avg"], mins_left)

        # Check exits
        exit_reason = risk_manager.update_position(pos, current_prem)

        if exit_reason:
            if not state.paper_trade:
                angel.place_order(
                    tradingsymbol=pos.symbol, token=pos.token,
                    transaction_type="SELL", quantity=pos.open_qty,
                )
            state.add_alert(
                f"{pos.instrument} {pos.direction} {exit_reason} "
                f"P&L: ₹{pos.realised_pnl:,.0f}",
                "success" if pos.realised_pnl > 0 else "error"
            )

    # Hard exit at 2 PM
    if should_exit_all():
        for pos in list(risk_manager.open_positions):
            cfg  = INSTRUMENTS[pos.instrument]
            ltp  = angel.get_ltp(cfg["exchange"], cfg["symbol"], cfg["index_token"])
            prem = estimate_option_premium(ltp, 0, pos.direction, cfg["atr_avg"], 0)
            risk_manager.force_exit(pos, prem, "time_exit")
            if not state.paper_trade:
                angel.place_order(
                    tradingsymbol=pos.symbol, token=pos.token,
                    transaction_type="SELL", quantity=pos.quantity,
                )
            state.add_alert(f"2 PM hard exit: {pos.instrument} P&L=₹{pos.realised_pnl:,.0f}", "info")


# ── Main scan loop ────────────────────────────────────────────────────────────
async def scan_loop():
    logger.info("Scanner started")
    state.add_alert("Bot scanner started", "info")

    while state.running:
        try:
            now  = ist_now()
            today = now.date()

            if not is_market_open():
                await asyncio.sleep(60)
                continue

            # Day initialization (once per day)
            if state.trading_day != today or not state.day_initialized:
                logger.info(f"Initializing new trading day: {today}")
                risk_manager.reset_day()
                state.trading_day = today
                state.day_initialized = False

                for instrument in INSTRUMENTS:
                    df = await refresh_candles(instrument)
                    if not df.empty:
                        await initialize_day(instrument, df)
                state.day_initialized = True

            # Fetch fresh candles
            df_map = {}
            for instrument in INSTRUMENTS:
                df = await refresh_candles(instrument)
                if not df.empty:
                    df_map[instrument] = df

            # VIX check
            vix_ok = await check_vix()

            # Manage existing positions
            await manage_positions(df_map)

            # Look for new signals only in trading window
            if is_trading_window() and vix_ok and not should_exit_all():
                ok, _ = risk_manager.can_trade

                if ok:
                    # Nifty: PDH + CPR + EMA + VWAP
                    if "NIFTY" in df_map:
                        nifty_setup = await run_nifty_strategy(df_map["NIFTY"])
                        if nifty_setup:
                            await execute_trade(nifty_setup)

                    # BankNifty: ORB
                    if "BANKNIFTY" in df_map:
                        bn_setup = await run_banknifty_strategy(df_map["BANKNIFTY"])
                        if bn_setup:
                            await execute_trade(bn_setup)

            state.last_scan = now
            state.market_data["last_scan"] = now.strftime("%H:%M:%S")
            state.market_data["vix"]       = state.vix

        except Exception as e:
            logger.error(f"Scanner error: {e}")
            state.add_alert(f"Scanner error: {e}", "error")

        await asyncio.sleep(SCAN_INTERVAL_SEC)

    logger.info("Scanner stopped")
    state.add_alert("Bot scanner stopped", "warning")


# ── Public controls ────────────────────────────────────────────────────────────
async def start_scanner():
    if state.running:
        return
    state.running = True
    asyncio.create_task(scan_loop())

def stop_scanner():
    state.running = False

def set_paper_mode(paper: bool):
    state.paper_trade = paper
    mode = "PAPER" if paper else "LIVE"
    state.add_alert(f"Switched to {mode} trade mode", "warning" if not paper else "info")
    logger.info(f"Trade mode: {mode}")
