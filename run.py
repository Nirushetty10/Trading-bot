"""
run.py — Start the AlgoOptions India trading bot.
Run from the tradingbot/ directory:
    python run.py
"""

import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(__file__))

import uvicorn
from loguru import logger
import os

# Configure logging
os.makedirs("logs", exist_ok=True)
logger.add("logs/bot_{time:YYYY-MM-DD}.log",
           rotation="1 day", retention="30 days",
           level="INFO", format="{time:HH:mm:ss} | {level} | {message}")

if __name__ == "__main__":
    logger.info("=" * 60)
    logger.info("  AlgoOptions India — Production Trading Bot v2.0")
    logger.info("  Strategy: PDH+CPR+EMA+VWAP (Nifty) | ORB (BankNifty)")
    logger.info("=" * 60)
    logger.info("Open browser: http://localhost:8000")
    logger.info("API docs:     http://localhost:8000/docs")

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        log_level="warning",
    )
