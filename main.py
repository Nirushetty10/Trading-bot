"""
main.py — FastAPI backend for AlgoOptions Trading Bot.
All REST endpoints + WebSocket for live dashboard updates.
"""

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional
import asyncio
import json
from datetime import datetime
from loguru import logger
import os

from backend.angel_api import angel
from backend.risk_manager import risk_manager
from backend.scanner import state, start_scanner, stop_scanner, set_paper_mode
import backend.config as cfg

# ── App setup ─────────────────────────────────────────────────────────────────
app = FastAPI(title="AlgoOptions India", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve frontend
FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "..", "frontend")
if os.path.exists(FRONTEND_DIR):
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")

# ── WebSocket manager ─────────────────────────────────────────────────────────
class ConnectionManager:
    def __init__(self):
        self.active: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)

    def disconnect(self, ws: WebSocket):
        self.active.remove(ws)

    async def broadcast(self, data: dict):
        dead = []
        for ws in self.active:
            try:
                await ws.send_text(json.dumps(data, default=str))
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.active.remove(ws)

ws_manager = ConnectionManager()

# ── Background broadcast task ─────────────────────────────────────────────────
async def broadcast_loop():
    while True:
        if ws_manager.active:
            payload = build_dashboard_payload()
            await ws_manager.broadcast(payload)
        await asyncio.sleep(5)

@app.on_event("startup")
async def startup():
    asyncio.create_task(broadcast_loop())
    logger.info("AlgoOptions India backend started")

# ── Helpers ───────────────────────────────────────────────────────────────────
def build_dashboard_payload() -> dict:
    summary = risk_manager.summary()
    return {
        "type":         "dashboard",
        "connected":    angel.connected,
        "paper_mode":   state.paper_trade,
        "scanning":     state.running,
        "vix":          state.vix,
        "last_scan":    state.last_scan.isoformat() if state.last_scan else None,
        "summary":      summary,
        "open_positions": [p.to_dict() for p in risk_manager.open_positions],
        "closed_today":   [p.to_dict() for p in risk_manager.closed_positions[-20:]],
        "signals":      state.signals_log[:20],
        "alerts":       state.alerts[:20],
        "pdh_pdl":      state.pdh_pdl,
        "cpr":          state.cpr,
        "orb":          state.orb,
        "market_data":  state.market_data,
    }

# ── Pydantic models ───────────────────────────────────────────────────────────
class Credentials(BaseModel):
    client_id:    str
    api_key:      str
    password:     str
    totp_secret:  str

class RiskSettings(BaseModel):
    capital_per_trade:  Optional[float] = None
    max_daily_loss:     Optional[float] = None
    max_positions:      Optional[int]   = None
    max_trades_per_day: Optional[int]   = None

class TradeMode(BaseModel):
    paper: bool

class ManualExit(BaseModel):
    symbol: str

# ── Endpoints ─────────────────────────────────────────────────────────────────
@app.get("/")
async def root():
    index = os.path.join(FRONTEND_DIR, "index.html")
    if os.path.exists(index):
        return FileResponse(index)
    return {"status": "AlgoOptions India API v2.0"}

@app.post("/api/connect")
async def connect(creds: Credentials):
    result = angel.connect(
        creds.client_id, creds.api_key,
        creds.password, creds.totp_secret
    )
    if result["status"]:
        # Update config
        cfg.ANGEL_CLIENT_ID   = creds.client_id
        cfg.ANGEL_API_KEY     = creds.api_key
        cfg.ANGEL_PASSWORD    = creds.password
        cfg.ANGEL_TOTP_SECRET = creds.totp_secret
    return result

@app.post("/api/disconnect")
async def disconnect():
    angel.disconnect()
    stop_scanner()
    return {"status": True, "message": "Disconnected"}

@app.post("/api/start")
async def start():
    if not angel.connected:
        return {"status": False, "message": "Not connected to Angel One"}
    await start_scanner()
    return {"status": True, "message": "Scanner started"}

@app.post("/api/stop")
async def stop():
    stop_scanner()
    return {"status": True, "message": "Scanner stopped"}

@app.post("/api/mode")
async def set_mode(mode: TradeMode):
    set_paper_mode(mode.paper)
    return {"status": True, "paper": mode.paper}

@app.post("/api/risk")
async def update_risk(s: RiskSettings):
    if s.capital_per_trade is not None:
        risk_manager.capital_per_trade  = s.capital_per_trade
    if s.max_daily_loss is not None:
        risk_manager.max_daily_loss     = s.max_daily_loss
    if s.max_positions is not None:
        risk_manager.max_positions      = s.max_positions
    if s.max_trades_per_day is not None:
        risk_manager.max_trades_per_day = s.max_trades_per_day
    return {"status": True, "message": "Risk settings updated"}

@app.post("/api/exit/{symbol}")
async def exit_position(symbol: str):
    for pos in risk_manager.open_positions:
        if pos.symbol == symbol:
            risk_manager.force_exit(pos, pos.current_price, "manual_exit")
            if not state.paper_trade:
                angel.place_order(
                    tradingsymbol=pos.symbol, token=pos.token,
                    transaction_type="SELL", quantity=pos.quantity,
                )
            return {"status": True, "pnl": round(pos.realised_pnl, 2)}
    return {"status": False, "message": "Position not found"}

@app.post("/api/exit_all")
async def exit_all():
    exited = []
    for pos in list(risk_manager.open_positions):
        risk_manager.force_exit(pos, pos.current_price, "manual_exit")
        exited.append(pos.symbol)
    return {"status": True, "exited": exited}

@app.get("/api/dashboard")
async def dashboard():
    return build_dashboard_payload()

@app.get("/api/positions")
async def positions():
    return {
        "open":   [p.to_dict() for p in risk_manager.open_positions],
        "closed": [p.to_dict() for p in risk_manager.closed_positions],
    }

@app.get("/api/signals")
async def signals():
    return {"signals": state.signals_log}

@app.get("/api/alerts")
async def alerts():
    return {"alerts": state.alerts}

@app.get("/api/summary")
async def summary():
    return risk_manager.summary()

@app.get("/api/levels")
async def levels():
    return {
        "pdh_pdl": state.pdh_pdl,
        "cpr":     state.cpr,
        "orb":     state.orb,
    }

# ── WebSocket ─────────────────────────────────────────────────────────────────
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await ws_manager.connect(websocket)
    try:
        # Send initial state immediately
        await websocket.send_text(json.dumps(build_dashboard_payload(), default=str))
        while True:
            # Keep alive — client can send pings
            data = await asyncio.wait_for(websocket.receive_text(), timeout=30)
    except (WebSocketDisconnect, asyncio.TimeoutError):
        ws_manager.disconnect(websocket)
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        ws_manager.disconnect(websocket)
