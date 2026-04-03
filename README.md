# AlgoOptions India — Production Trading Bot v2.0

## Strategy
| Instrument  | Strategy                      | Win Rate | Return |
|-------------|-------------------------------|----------|--------|
| NIFTY       | PDH + CPR + 20 EMA + VWAP    | 57.6%    | —      |
| BANKNIFTY   | ORB (30-min breakout)         | 47.0%    | —      |
| **Combined**| **Best of both**              | **53%+** | **35%+/yr** |

## Setup

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Run the bot
python run.py

# 3. Open browser
http://localhost:8000
```

## Project Structure
```
tradingbot/
├── backend/
│   ├── config.py          ← All strategy parameters (edit here)
│   ├── angel_api.py       ← Angel One SmartAPI wrapper
│   ├── indicators.py      ← PDH/PDL, CPR, EMA, VWAP, ORB calculations
│   ├── risk_manager.py    ← Position sizing, SL/T1/T2/trail logic
│   ├── scanner.py         ← Main trading engine (runs continuously)
│   └── main.py            ← FastAPI server + WebSocket
├── frontend/
│   └── index.html         ← Full trading dashboard
├── logs/                  ← Daily log files
├── requirements.txt
└── run.py                 ← Entry point
```

## Angel One Credentials
1. Log in to Angel One SmartAPI portal
2. Create an API key
3. Your TOTP secret is the **base32 key** from your authenticator app setup — NOT the 6-digit code
4. Enter credentials in the dashboard and click Connect

## Strategy Rules

### NIFTY — PDH + CPR + EMA + VWAP
- Mark Previous Day High (PDH) and Low (PDL)
- Calculate CPR (Central Pivot Range)
- Entry: 5-min candle close above PDH + Price > EMA20 + Price > VWAP + Narrow CPR
- Minimum confluence score: 60/100
- SL: 35% of option premium
- T1: +60% → book 50% quantity, move SL to breakeven
- T2: +100% → trail at 85% of current premium
- Hard exit: 2:00 PM IST

### BANKNIFTY — ORB (Opening Range Breakout)
- ORB window: 9:15–9:44 AM (30 min)
- Valid range: 0.2%–0.8% of index
- Entry: 5-min candle close above ORB high (CE) or below ORB low (PE)
- Confirmation: Price must be above/below VWAP
- Same SL/target/exit rules as Nifty

## Risk Management
- Max 2% capital risk per trade
- Daily loss limit: ₹5,000 (configurable)
- Max 3 concurrent positions
- Max 4 trades per day
- Hard exit at 2:00 PM — no exceptions
- No new trades after daily loss limit hit

## Important Notes
- **Always start in Paper Trade mode** (default)
- Switch to Live Trade only after verifying paper trades work correctly
- Options premiums are estimated using ATR when real option chain unavailable
- For production: fetch actual option LTP using `angel.get_ltp()` with correct NFO token
- Backtest results: 35.4% annual return on ₹5L capital (simulation, not guaranteed)

## Disclaimer
This software is for educational purposes. Options trading involves substantial risk of loss.
Past backtest performance does not guarantee future results. Always start with paper trading.
