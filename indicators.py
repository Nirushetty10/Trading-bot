"""
indicators.py — All technical indicator calculations.
PDH/PDL, CPR, EMA, VWAP, ATR, ORB.
"""

import pandas as pd
import numpy as np
from datetime import datetime, date
import pytz

IST = pytz.timezone("Asia/Kolkata")


# ── Basic indicators ──────────────────────────────────────────────────────────

def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()

def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    h, l, c = df["high"], df["low"], df["close"]
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(span=period, adjust=False).mean()

def vwap(df: pd.DataFrame) -> pd.Series:
    """Intraday VWAP — resets each day."""
    df = df.copy()
    df["date"]  = df.index.date
    df["tp"]    = (df["high"] + df["low"] + df["close"]) / 3
    df["tp_vol"] = df["tp"] * df["volume"]

    result = pd.Series(index=df.index, dtype=float)
    for day, grp in df.groupby("date"):
        cum_vol = grp["volume"].cumsum()
        cum_tp_vol = grp["tp_vol"].cumsum()
        result.loc[grp.index] = cum_tp_vol / cum_vol.replace(0, np.nan)
    return result

def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain  = delta.clip(lower=0).ewm(span=period, adjust=False).mean()
    loss  = (-delta.clip(upper=0)).ewm(span=period, adjust=False).mean()
    rs    = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


# ── PDH / PDL ─────────────────────────────────────────────────────────────────

def get_pdh_pdl(df: pd.DataFrame) -> dict:
    """
    Extract Previous Day High, Low, Close from a multi-day 5-min dataframe.
    Returns dict with pdh, pdl, pdc for today's trading session.
    """
    df = df.copy()
    df["date"] = df.index.date
    dates = sorted(df["date"].unique())

    if len(dates) < 2:
        return {"pdh": None, "pdl": None, "pdc": None}

    prev_day = dates[-2]
    prev_df  = df[df["date"] == prev_day]

    return {
        "pdh": float(prev_df["high"].max()),
        "pdl": float(prev_df["low"].min()),
        "pdc": float(prev_df["close"].iloc[-1]),
    }


# ── CPR (Central Pivot Range) ─────────────────────────────────────────────────

def calculate_cpr(pdh: float, pdl: float, pdc: float) -> dict:
    """
    CPR = Pivot, BC (Bottom Central), TC (Top Central)
    Narrow CPR (TC-BC < 0.15% of pivot) → trending day expected.
    """
    pivot = (pdh + pdl + pdc) / 3
    bc    = (pdh + pdl) / 2
    tc    = (pivot * 2) - bc

    # Ensure TC > BC
    if tc < bc:
        tc, bc = bc, tc

    width_pct = abs(tc - bc) / pivot * 100

    return {
        "pivot":     round(pivot, 2),
        "bc":        round(bc, 2),
        "tc":        round(tc, 2),
        "width_pct": round(width_pct, 4),
        "narrow":    width_pct < 0.15,
    }


# ── ORB (Opening Range Breakout) ──────────────────────────────────────────────

def calculate_orb(df: pd.DataFrame, today: date) -> dict:
    """
    Calculate 30-minute Opening Range (9:15–9:44 AM) for today.
    Returns high, low, range_pct, is_valid.
    """
    today_df = df[df.index.date == today]
    orb_df   = today_df.between_time("09:15", "09:44")

    if orb_df.empty:
        return {"high": None, "low": None, "range_pct": None, "valid": False}

    orb_high = float(orb_df["high"].max())
    orb_low  = float(orb_df["low"].min())
    mid      = (orb_high + orb_low) / 2
    rng_pct  = (orb_high - orb_low) / mid

    from backend.config import ORB_MIN_RANGE_PCT, ORB_MAX_RANGE_PCT
    valid = ORB_MIN_RANGE_PCT <= rng_pct <= ORB_MAX_RANGE_PCT

    return {
        "high":      round(orb_high, 2),
        "low":       round(orb_low, 2),
        "range_pct": round(rng_pct * 100, 3),
        "valid":     valid,
    }


# ── Setup scoring ─────────────────────────────────────────────────────────────

def score_pdh_cpr_setup(
    current_price: float,
    pdh: float, pdl: float,
    cpr: dict,
    ema20: float,
    vwap_val: float,
    volume: float,
    avg_volume: float,
    direction: str,  # "CE" or "PE"
) -> dict:
    """
    Score the PDH/CPR/EMA/VWAP setup quality 0–100.
    Returns score, reasons, and whether to trade.
    """
    score   = 0
    reasons = []

    if direction == "CE":
        # Bullish setup
        if cpr["narrow"]:
            score += 25
            reasons.append(f"Narrow CPR ({cpr['width_pct']:.3f}%) → trending day")

        if current_price > cpr["tc"]:
            score += 20
            reasons.append("Price above CPR top → bullish bias")

        if current_price > ema20:
            score += 20
            reasons.append(f"Above EMA20 ({ema20:.0f})")

        if current_price > vwap_val:
            score += 20
            reasons.append(f"Above VWAP ({vwap_val:.0f})")

        if volume > avg_volume * 1.5:
            score += 15
            reasons.append(f"Volume surge {volume/avg_volume:.1f}x avg")

    else:
        # Bearish setup (PE)
        if cpr["narrow"]:
            score += 25
            reasons.append(f"Narrow CPR ({cpr['width_pct']:.3f}%) → trending day")

        if current_price < cpr["bc"]:
            score += 20
            reasons.append("Price below CPR bottom → bearish bias")

        if current_price < ema20:
            score += 20
            reasons.append(f"Below EMA20 ({ema20:.0f})")

        if current_price < vwap_val:
            score += 20
            reasons.append(f"Below VWAP ({vwap_val:.0f})")

        if volume > avg_volume * 1.5:
            score += 15
            reasons.append(f"Volume surge {volume/avg_volume:.1f}x avg")

    from backend.config import MIN_SETUP_SCORE
    return {
        "score":    score,
        "reasons":  reasons,
        "tradable": score >= MIN_SETUP_SCORE,
    }


def detect_pdh_breakout(
    df_today: pd.DataFrame,
    pdh: float, pdl: float,
    cpr: dict,
    ema20_series: pd.Series,
    vwap_series: pd.Series,
) -> dict:
    """
    Detect PDH breakout or PDL breakdown on today's 5-min candles.
    Requires candle CLOSE outside level (not just wick).
    """
    from backend.config import PRIME_ENTRY_START, PRIME_ENTRY_END

    results = []
    avg_vol = df_today["volume"].rolling(20).mean()

    for ts, row in df_today.iterrows():
        bar_time = ts.time() if hasattr(ts, 'time') else ts

        if not (PRIME_ENTRY_START <= bar_time <= PRIME_ENTRY_END):
            continue

        ema_val  = ema20_series.get(ts, None)
        vwap_val = vwap_series.get(ts, None)
        avg_v    = avg_vol.get(ts, row["volume"])

        if ema_val is None or vwap_val is None:
            continue

        # PDH Breakout — CE
        if row["close"] > pdh:
            sc = score_pdh_cpr_setup(
                row["close"], pdh, pdl, cpr,
                ema_val, vwap_val, row["volume"], avg_v, "CE"
            )
            if sc["tradable"]:
                results.append({
                    "timestamp":  ts,
                    "setup":      "PDH_breakout",
                    "direction":  "CE",
                    "trigger_price": pdh,
                    "close_price":   row["close"],
                    **sc,
                })

        # PDL Breakdown — PE (only if score is higher — more selective)
        elif row["close"] < pdl:
            sc = score_pdh_cpr_setup(
                row["close"], pdh, pdl, cpr,
                ema_val, vwap_val, row["volume"], avg_v, "PE"
            )
            if sc["tradable"] and sc["score"] >= 70:  # More selective for PE
                results.append({
                    "timestamp":  ts,
                    "setup":      "PDL_breakdown",
                    "direction":  "PE",
                    "trigger_price": pdl,
                    "close_price":   row["close"],
                    **sc,
                })

    return results[0] if results else None


def detect_ema_pullback(
    df_today: pd.DataFrame,
    ema20_series: pd.Series,
    vwap_series: pd.Series,
    trend_direction: str,  # "CE" or "PE"
) -> dict:
    """
    Detect EMA20 pullback in an already trending market.
    Requires price touching EMA then bouncing with volume.
    """
    from backend.config import PRIME_ENTRY_END, SECOND_ENTRY_START

    for ts, row in df_today.iterrows():
        bar_time = ts.time() if hasattr(ts, 'time') else ts
        if not (PRIME_ENTRY_END <= bar_time <= SECOND_ENTRY_START):
            continue

        ema_val  = ema20_series.get(ts, None)
        vwap_val = vwap_series.get(ts, None)
        if ema_val is None or vwap_val is None:
            continue

        tol = ema_val * 0.001  # 0.1% tolerance

        if trend_direction == "CE":
            # Price touched EMA (low within tolerance) and closed above
            if row["low"] <= ema_val + tol and row["close"] > ema_val and row["close"] > vwap_val:
                return {
                    "timestamp":   ts,
                    "setup":       "EMA_pullback",
                    "direction":   "CE",
                    "score":       65,
                    "tradable":    True,
                    "reasons":     [f"EMA pullback bounce at {ema_val:.0f}", f"Above VWAP {vwap_val:.0f}"],
                    "close_price": row["close"],
                }
        else:
            if row["high"] >= ema_val - tol and row["close"] < ema_val and row["close"] < vwap_val:
                return {
                    "timestamp":   ts,
                    "setup":       "EMA_pullback",
                    "direction":   "PE",
                    "score":       65,
                    "tradable":    True,
                    "reasons":     [f"EMA pullback rejection at {ema_val:.0f}", f"Below VWAP {vwap_val:.0f}"],
                    "close_price": row["close"],
                }
    return None


def detect_orb_breakout(
    df_today: pd.DataFrame,
    orb: dict,
    vwap_series: pd.Series,
) -> dict:
    """
    Detect ORB breakout on 5-min candles after 9:45 AM.
    Requires full candle close outside ORB, confirmed by VWAP alignment.
    """
    from backend.config import ORB_END, PRIME_ENTRY_END, SECOND_ENTRY_START, SECOND_ENTRY_END

    if not orb.get("valid"):
        return None

    orb_high = orb["high"]
    orb_low  = orb["low"]
    results  = []

    trade_df = df_today.between_time("09:45", "13:55")
    for ts, row in trade_df.iterrows():
        bar_time = ts.time() if hasattr(ts, 'time') else ts

        in_prime  = bar_time <= PRIME_ENTRY_END
        in_second = SECOND_ENTRY_START <= bar_time <= SECOND_ENTRY_END
        if not (in_prime or in_second):
            continue

        vwap_val = vwap_series.get(ts, None)
        reasons  = []
        score    = 0

        # CE breakout
        if row["close"] > orb_high:
            score += 50
            reasons.append(f"ORB high broken: {orb_high:.0f}")
            if vwap_val and row["close"] > vwap_val:
                score += 30
                reasons.append(f"Above VWAP ({vwap_val:.0f})")
            if score >= 60:
                results.append({
                    "timestamp":   ts,
                    "setup":       "ORB_breakout",
                    "direction":   "CE",
                    "trigger_price": orb_high,
                    "close_price": row["close"],
                    "score":       score,
                    "tradable":    True,
                    "reasons":     reasons,
                })
                break

        # PE breakdown
        elif row["close"] < orb_low:
            score += 50
            reasons.append(f"ORB low broken: {orb_low:.0f}")
            if vwap_val and row["close"] < vwap_val:
                score += 30
                reasons.append(f"Below VWAP ({vwap_val:.0f})")
            if score >= 60:
                results.append({
                    "timestamp":   ts,
                    "setup":       "ORB_breakdown",
                    "direction":   "PE",
                    "trigger_price": orb_low,
                    "close_price": row["close"],
                    "score":       score,
                    "tradable":    True,
                    "reasons":     reasons,
                })
                break

    return results[0] if results else None
