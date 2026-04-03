"""
risk_manager.py — Position sizing, SL/target management, P&L tracking.
All exit logic (SL, T1, T2, trail, time exit) lives here.
"""

import math
from datetime import datetime
from dataclasses import dataclass, field
from typing import Optional, List, Dict
from loguru import logger


@dataclass
class Position:
    # Identity
    symbol:        str
    token:         str
    instrument:    str          # NIFTY or BANKNIFTY
    direction:     str          # CE or PE
    setup:         str          # PDH_breakout, ORB_breakout, etc.
    lot_size:      int

    # Prices
    entry_price:   float
    sl_price:      float
    target1_price: float
    target2_price: float
    quantity:      int

    # State
    status:        str   = "OPEN"   # OPEN, T1_BOOKED, CLOSED
    current_price: float = 0.0
    t1_qty_closed: int   = 0
    order_id:      str   = ""
    entry_time:    datetime = field(default_factory=datetime.now)
    exit_time:     Optional[datetime] = None
    exit_reason:   str   = ""
    exit_price:    float = 0.0
    paper_trade:   bool  = True
    notes:         str   = ""

    # Trail
    trail_sl:      float = 0.0

    @property
    def open_qty(self) -> int:
        return self.quantity - self.t1_qty_closed

    @property
    def pnl(self) -> float:
        """Unrealised P&L on open portion"""
        if self.status == "CLOSED":
            return self.realised_pnl
        return (self.current_price - self.entry_price) * self.open_qty

    @property
    def pnl_pct(self) -> float:
        if self.entry_price == 0:
            return 0.0
        return (self.current_price - self.entry_price) / self.entry_price * 100

    @property
    def realised_pnl(self) -> float:
        t1_pnl  = (self.target1_price - self.entry_price) * self.t1_qty_closed if self.t1_qty_closed else 0
        rem_pnl = (self.exit_price - self.entry_price) * (self.quantity - self.t1_qty_closed)
        return t1_pnl + rem_pnl

    def to_dict(self) -> dict:
        return {
            "symbol":        self.symbol,
            "token":         self.token,
            "instrument":    self.instrument,
            "direction":     self.direction,
            "setup":         self.setup,
            "quantity":      self.quantity,
            "lot_size":      self.lot_size,
            "entry_price":   round(self.entry_price, 2),
            "current_price": round(self.current_price, 2),
            "sl_price":      round(self.sl_price, 2),
            "target1_price": round(self.target1_price, 2),
            "target2_price": round(self.target2_price, 2),
            "trail_sl":      round(self.trail_sl, 2),
            "status":        self.status,
            "pnl":           round(self.pnl, 2),
            "pnl_pct":       round(self.pnl_pct, 2),
            "order_id":      self.order_id,
            "entry_time":    self.entry_time.isoformat(),
            "exit_time":     self.exit_time.isoformat() if self.exit_time else None,
            "exit_reason":   self.exit_reason,
            "paper_trade":   self.paper_trade,
            "notes":         self.notes,
        }


class RiskManager:
    def __init__(self):
        from backend.config import (
            CAPITAL_PER_TRADE, MAX_DAILY_LOSS, MAX_POSITIONS,
            MAX_TRADES_PER_DAY, SL_PCT_OPTION, TARGET1_PCT, TARGET2_PCT,
            TRAIL_FACTOR
        )
        self.capital_per_trade  = CAPITAL_PER_TRADE
        self.max_daily_loss     = MAX_DAILY_LOSS
        self.max_positions      = MAX_POSITIONS
        self.max_trades_per_day = MAX_TRADES_PER_DAY
        self.sl_pct             = SL_PCT_OPTION
        self.t1_pct             = TARGET1_PCT
        self.t2_pct             = TARGET2_PCT
        self.trail_factor       = TRAIL_FACTOR

        self.open_positions:   List[Position] = []
        self.closed_positions: List[Position] = []
        self.trades_today:     int  = 0
        self.daily_pnl:        float = 0.0
        self.daily_loss_hit:   bool = False

    # ── Gate checks ────────────────────────────────────────────────────────────
    @property
    def can_trade(self) -> tuple[bool, str]:
        if self.daily_loss_hit:
            return False, f"Daily loss limit hit (₹{self.max_daily_loss:,})"
        if self.daily_pnl <= -self.max_daily_loss:
            self.daily_loss_hit = True
            return False, f"Daily loss limit hit (₹{self.max_daily_loss:,})"
        if len(self.open_positions) >= self.max_positions:
            return False, f"Max positions ({self.max_positions}) reached"
        if self.trades_today >= self.max_trades_per_day:
            return False, f"Max trades/day ({self.max_trades_per_day}) reached"
        return True, "OK"

    def instrument_has_position(self, instrument: str) -> bool:
        return any(p.instrument == instrument for p in self.open_positions)

    # ── Position sizing ────────────────────────────────────────────────────────
    def calculate_size(self, entry_premium: float, lot_size: int) -> dict:
        """
        Risk ₹ / (SL_PCT * premium * lot_size) = number of lots.
        Always at least 1 lot.
        """
        risk_rs  = self.capital_per_trade * 0.02  # 2% risk per trade
        sl_pts   = entry_premium * self.sl_pct
        lots     = max(1, math.floor(risk_rs / (sl_pts * lot_size)))
        quantity = lots * lot_size
        return {"lots": lots, "quantity": quantity, "risk_rs": round(risk_rs, 0)}

    def build_position(self, symbol: str, token: str, instrument: str,
                       direction: str, setup: str, lot_size: int,
                       entry_premium: float, paper: bool = True,
                       order_id: str = "") -> Position:
        sl      = round(entry_premium * (1 - self.sl_pct), 2)
        t1      = round(entry_premium * (1 + self.t1_pct), 2)
        t2      = round(entry_premium * (1 + self.t2_pct), 2)
        sizing  = self.calculate_size(entry_premium, lot_size)

        pos = Position(
            symbol=symbol, token=token, instrument=instrument,
            direction=direction, setup=setup, lot_size=lot_size,
            entry_price=entry_premium, sl_price=sl,
            target1_price=t1, target2_price=t2,
            quantity=sizing["quantity"], trail_sl=sl,
            paper_trade=paper, order_id=order_id,
            current_price=entry_premium,
        )
        self.open_positions.append(pos)
        self.trades_today += 1
        logger.info(f"Position opened: {instrument} {direction} {setup} "
                    f"entry=₹{entry_premium} SL=₹{sl} T1=₹{t1} T2=₹{t2} "
                    f"qty={sizing['quantity']} {'[PAPER]' if paper else '[LIVE]'}")
        return pos

    # ── Price update and exit logic ────────────────────────────────────────────
    def update_position(self, pos: Position, current_premium: float) -> Optional[str]:
        """
        Update position price and check exit conditions.
        Returns exit_reason if position should be closed, else None.
        """
        pos.current_price = current_premium
        pnl_pct = (current_premium - pos.entry_price) / pos.entry_price

        # ── Stop loss ──────────────────────────────────────────────────────────
        if current_premium <= pos.sl_price:
            self._close_position(pos, current_premium, "SL")
            return "SL"

        # ── Trail SL hit (after T1) ────────────────────────────────────────────
        if pos.status == "T1_BOOKED" and current_premium < pos.trail_sl:
            self._close_position(pos, current_premium, "trail_SL")
            return "trail_SL"

        # ── Target 1 ──────────────────────────────────────────────────────────
        if pos.status == "OPEN" and current_premium >= pos.target1_price:
            half_qty = pos.quantity // 2
            pos.t1_qty_closed = half_qty
            pos.status = "T1_BOOKED"
            pos.sl_price  = pos.entry_price  # move SL to breakeven
            pos.trail_sl  = current_premium * self.trail_factor
            logger.info(f"T1 hit: {pos.symbol} @ ₹{current_premium} "
                        f"booked {half_qty} qty, SL moved to cost ₹{pos.entry_price}")
            return None

        # ── Target 2 ──────────────────────────────────────────────────────────
        if pos.status == "T1_BOOKED" and current_premium >= pos.target2_price:
            self._close_position(pos, current_premium, "T2")
            return "T2"

        # ── Update trail SL upward ────────────────────────────────────────────
        if pos.status == "T1_BOOKED":
            new_trail = current_premium * self.trail_factor
            if new_trail > pos.trail_sl:
                pos.trail_sl = new_trail

        return None

    def force_exit(self, pos: Position, current_premium: float, reason: str = "time_exit"):
        self._close_position(pos, current_premium, reason)

    def _close_position(self, pos: Position, exit_price: float, reason: str):
        pos.status      = "CLOSED"
        pos.exit_price  = exit_price
        pos.exit_time   = datetime.now()
        pos.exit_reason = reason

        pnl = pos.realised_pnl
        self.daily_pnl += pnl
        self.open_positions.remove(pos)
        self.closed_positions.append(pos)

        logger.info(f"Position closed: {pos.symbol} {reason} "
                    f"entry=₹{pos.entry_price} exit=₹{exit_price} "
                    f"P&L=₹{pnl:,.0f}")

    # ── Day reset ──────────────────────────────────────────────────────────────
    def reset_day(self):
        self.trades_today   = 0
        self.daily_pnl      = 0.0
        self.daily_loss_hit = False
        self.open_positions.clear()
        logger.info("Risk manager reset for new trading day")

    # ── Summary ────────────────────────────────────────────────────────────────
    def summary(self) -> dict:
        closed = self.closed_positions
        wins   = [p for p in closed if p.realised_pnl > 0]
        losses = [p for p in closed if p.realised_pnl <= 0]
        total  = len(closed)
        return {
            "daily_pnl":        round(self.daily_pnl, 2),
            "trades_today":     self.trades_today,
            "open_positions":   len(self.open_positions),
            "closed_today":     total,
            "wins":             len(wins),
            "losses":           len(losses),
            "win_rate":         round(len(wins)/total*100, 1) if total else 0,
            "can_trade":        self.can_trade[0],
            "block_reason":     self.can_trade[1],
            "daily_loss_pct":   round(abs(min(self.daily_pnl, 0))/self.max_daily_loss*100, 1),
            "daily_loss_limit": self.max_daily_loss,
        }


# Singleton
risk_manager = RiskManager()
