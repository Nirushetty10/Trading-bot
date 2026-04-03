"""
angel_api.py — Angel One SmartAPI wrapper.
Handles auth, market data, order placement, option chain.
"""

import pyotp
import time as time_mod
import pandas as pd
from datetime import datetime, timedelta
from loguru import logger

try:
    from SmartApi import SmartConnect
    SMARTAPI_AVAILABLE = True
except ImportError:
    SMARTAPI_AVAILABLE = False
    logger.warning("smartapi-python not installed. Running in simulation mode.")


class AngelOneAPI:
    def __init__(self):
        self.obj          = None
        self.connected    = False
        self.feed_token   = None
        self.auth_token   = None
        self.client_id    = None
        self._sim_mode    = not SMARTAPI_AVAILABLE

    # ── Connection ─────────────────────────────────────────────────────────────
    def connect(self, client_id: str, api_key: str, password: str, totp_secret: str) -> dict:
        if self._sim_mode:
            self.connected = True
            self.client_id = client_id
            logger.info("Running in SIMULATION mode (smartapi not installed)")
            return {"status": True, "message": "Simulation mode — connected"}

        try:
            self.obj    = SmartConnect(api_key=api_key)
            totp        = pyotp.TOTP(totp_secret).now()
            data        = self.obj.generateSession(client_id, password, totp)

            if not data.get("status"):
                return {"status": False, "message": data.get("message", "Login failed")}

            self.feed_token  = self.obj.getfeedToken()
            self.auth_token  = data["data"]["jwtToken"]
            self.connected   = True
            self.client_id   = client_id
            logger.info(f"Connected to Angel One as {client_id}")
            return {"status": True, "message": f"Connected as {client_id}"}

        except Exception as e:
            logger.error(f"Angel One connect error: {e}")
            return {"status": False, "message": str(e)}

    def disconnect(self):
        if self.obj:
            try:
                self.obj.terminateSession(self.client_id)
            except Exception:
                pass
        self.connected = False
        logger.info("Disconnected from Angel One")

    # ── Market data ────────────────────────────────────────────────────────────
    def get_ltp(self, exchange: str, symbol: str, token: str) -> float:
        if self._sim_mode or not self.connected:
            return 0.0
        try:
            data = self.obj.ltpData(exchange, symbol, token)
            return float(data["data"]["ltp"])
        except Exception as e:
            logger.error(f"LTP fetch error {symbol}: {e}")
            return 0.0

    def get_candles(self, token: str, interval: str = "FIVE_MINUTE",
                    days: int = 5, exchange: str = "NSE") -> pd.DataFrame:
        if self._sim_mode or not self.connected:
            return pd.DataFrame()
        try:
            to_dt   = datetime.now()
            from_dt = to_dt - timedelta(days=days)
            params  = {
                "exchange":    exchange,
                "symboltoken": token,
                "interval":    interval,
                "fromdate":    from_dt.strftime("%Y-%m-%d %H:%M"),
                "todate":      to_dt.strftime("%Y-%m-%d %H:%M"),
            }
            data = self.obj.getCandleData(params)
            if not data.get("data"):
                return pd.DataFrame()

            df = pd.DataFrame(data["data"],
                              columns=["timestamp","open","high","low","close","volume"])
            df["timestamp"] = pd.to_datetime(df["timestamp"])
            for c in ["open","high","low","close","volume"]:
                df[c] = pd.to_numeric(df[c])
            df = df.set_index("timestamp").sort_index()
            return df
        except Exception as e:
            logger.error(f"Candle fetch error token={token}: {e}")
            return pd.DataFrame()

    def get_vix(self) -> float:
        """Fetch India VIX"""
        if self._sim_mode or not self.connected:
            return 14.5  # default neutral value
        try:
            from backend.config import VIX_TOKEN
            data = self.obj.ltpData("NSE", "India VIX", VIX_TOKEN)
            return float(data["data"]["ltp"])
        except Exception:
            return 14.5

    def get_option_chain(self, symbol: str, expiry: str) -> dict:
        if self._sim_mode or not self.connected:
            return {}
        try:
            data = self.obj.getOptionGreeks({"name": symbol, "expirydate": expiry})
            return data.get("data", {})
        except Exception as e:
            logger.error(f"Option chain error {symbol}: {e}")
            return {}

    # ── Order management ────────────────────────────────────────────────────────
    def place_order(self, tradingsymbol: str, token: str, transaction_type: str,
                    quantity: int, price: float = 0,
                    order_type: str = "MARKET", product: str = "INTRADAY") -> dict:
        if self._sim_mode or not self.connected:
            fake_id = f"SIM{int(time_mod.time()*1000)}"
            logger.info(f"[SIM] {transaction_type} {quantity} {tradingsymbol} @ market → {fake_id}")
            return {"status": True, "order_id": fake_id, "simulated": True}

        try:
            params = {
                "variety":         "NORMAL",
                "tradingsymbol":   tradingsymbol,
                "symboltoken":     token,
                "transactiontype": transaction_type,
                "exchange":        "NFO",
                "ordertype":       order_type,
                "producttype":     product,
                "duration":        "DAY",
                "price":           str(round(price, 1)) if order_type != "MARKET" else "0",
                "squareoff":       "0",
                "stoploss":        "0",
                "quantity":        str(quantity),
            }
            resp = self.obj.placeOrder(params)
            logger.info(f"Order placed: {transaction_type} {quantity} {tradingsymbol} → {resp}")
            return {"status": True, "order_id": resp}
        except Exception as e:
            logger.error(f"Order placement error: {e}")
            return {"status": False, "message": str(e)}

    def get_positions(self) -> list:
        if self._sim_mode or not self.connected:
            return []
        try:
            data = self.obj.position()
            return data.get("data", []) or []
        except Exception:
            return []

    def get_order_book(self) -> list:
        if self._sim_mode or not self.connected:
            return []
        try:
            data = self.obj.orderBook()
            return data.get("data", []) or []
        except Exception:
            return []

    # ── Utility ─────────────────────────────────────────────────────────────────
    def nearest_expiry(self, weekday: int = 3) -> str:
        """Return nearest Thursday (Nifty) or Wednesday (BankNifty) expiry"""
        today = datetime.now()
        days  = (weekday - today.weekday()) % 7
        if days == 0 and today.hour >= 15:
            days = 7
        expiry = today + timedelta(days=days)
        return expiry.strftime("%d%b%Y").upper()

    def build_option_symbol(self, underlying: str, expiry_str: str,
                             strike: int, opt_type: str) -> str:
        """Build NFO option trading symbol e.g. NIFTY24APR2425000CE"""
        exp = datetime.strptime(expiry_str, "%d%b%Y")
        return f"{underlying}{exp.strftime('%y%b').upper()}{strike}{opt_type}"


# Singleton
angel = AngelOneAPI()
