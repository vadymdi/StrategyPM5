""" CRYPTO ARBITRAGE TERMINAL v38.0 — Bulk Filter & Concurrent Orderbooks """
import os
import sys
import asyncio
import aiohttp
import re
import json
from dotenv import load_dotenv
from dataclasses import dataclass
from typing import Optional, List, Tuple
from telegram import Bot

# ── CONFIG ────────────────────────────────────────────────────────────────────
BET_AMOUNT              = 1000.0
MIN_PROFIT_USD          = 1.0
ROI_THRESHOLD_ALERT     = 5.0
SAFETY_FACTOR           = 1.15
MAX_CONCURRENT          = 20
BINANCE_FEE_RATE        = 0.0005
PREDICT_DEFAULT_FEE_BPS = 200
POLYMARKET_CRYPTO_FEE   = 0.07

load_dotenv()
PREDICT_API_KEY    = os.getenv("PREDICT_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")

ASSETS_MAP = {
    "BTC": "BTCUSDT", "Bitcoin": "BTCUSDT",
    "ETH": "ETHUSDT", "Ethereum": "ETHUSDT",
    "SOL": "SOLUSDT", "Solana": "SOLUSDT",
    "BNB": "BNBUSDT", "Binance Coin": "BNBUSDT",
}

POLY_SLUGS = [
    "will-solana-hit-60-or-140-first",
    "will-ethereum-hit-1k-or-3k-first",
    "will-bnb-hit-400-or-800-first"
]

# ── DATACLASSES ───────────────────────────────────────────────────────────────
@dataclass
class MarketParsed:
    ticker: str
    asset: str
    target_low: float
    target_high: float
    current: float

@dataclass
class Deal:
    bet_type: str
    bet_price: float
    hedge_dir: str
    pos_size_usd: float
    leverage: int
    profit_usd: float
    roi: float
    source: str
    question: str
    parsed: MarketParsed

# ── SCANNER ───────────────────────────────────────────────────────────────────
class ArbitrageScanner:
    def __init__(self):
        self.prices: dict = {}
        self.bot = Bot(token=TELEGRAM_BOT_TOKEN) if (TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID) else None
        self._pred_headers = {
            "x-api-key": PREDICT_API_KEY,
            "Content-Type": "application/json",
            "User-Agent": "ArbitrageBot/38.0",
        }
        print(f"\n{'='*65}")
        print(f"  ARBITRAGE TERMINAL v38.0 | Budget: ${BET_AMOUNT:.0f}")
        print(f"  Polymarket + Predict.fun | Bulk Metadata -> Parse -> Orderbook")
        print(f"{'='*65}\n")

    # ─── PRICES ───────────────────────────────────────────────────────────────
    async def fetch_prices(self, session: aiohttp.ClientSession):
        print("  [1/4] Fetching prices...")
        urls = [
            ("https://fapi.binance.com/fapi/v1/ticker/price", "Binance Futures"),
            ("https://api.binance.com/api/v3/ticker/price",   "Binance Spot"),
        ]
        for url, label in urls:
            try:
                async with session.get(url) as r:
                    if r.status == 200:
                        data = await r.json(content_type=None)
                        self.prices = {x['symbol']: float(x['price']) for x in data}
                        print(f"       ✅ {label}: {len(self.prices)} pairs")
                        return
            except Exception:
                continue

        try:
            ids = "bitcoin,ethereum,solana,binancecoin"
            url = f"https://api.coingecko.com/api/v3/simple/price?ids={ids}&vs_currencies=usd"
            async with session.get(url) as r:
                if r.status == 200:
                    data = await r.json(content_type=None)
                    MAP = {"bitcoin": "BTCUSDT", "ethereum": "ETHUSDT", "solana": "SOLUSDT", "binancecoin": "BNBUSDT"}
                    for gid, sym in MAP.items():
                        if gid in data:
                            self.prices[sym] = float(data[gid]['usd'])
                    print(f"       ✅ CoinGecko: {len(self.prices)} pairs")
                    return
        except Exception:
            pass
        print("       ❌ All price sources failed!")

    # ─── PARSING ──────────────────────────────────────────────────────────────
    def parse_market(self, question: str) -> Optional[MarketParsed]:
        if not question: return None
        q = question.lower()
        
        if not ((" or " in q or " before " in q) and any(x in q for x in ("hit", "reach", "touch", "first"))):
            return None
            
        ticker = asset = None
        for name, sym in ASSETS_MAP.items():
            if re.search(r'(?<![a-z])' + re.escape(name.lower()) + r'(?![a-z])', q):
                if sym in self.prices:
                    ticker, asset = sym, name
                    break
                    
        if not ticker: return None

        cur = self.prices[ticker]
        nums = []
        for raw in re.findall(r'[\d,]+\.?\d*[kK]?', q):
            s = raw.lower().replace(',', '')
            mult = 1000 if 'k' in s else 1
            try:
                v = float(s.replace('k', '')) * mult
                if v > 0 and 0.05 <= v / cur <= 20.0:
                    nums.append(v)
            except ValueError:
                pass

        if len(nums) < 2: return None
        return MarketParsed(ticker=ticker, asset=asset, target_low=min(nums), target_high=max(nums), current=cur)

    # ─── FETCH METADATA ───────────────────────────────────────────────────────
    async def _poly_slug(self, session, slug: str) -> Optional[dict]:
        try:
            async with session.get(f"https://gamma-api.polymarket.com/markets/slug/{slug}") as r:
                if r.status == 200:
                    data = await r.json(content_type=None)
                    m = data[0] if isinstance(data, list) and data else data
                    raw = m.get('clobTokenIds')
                    tokens = json.loads(raw) if isinstance(raw, str) else raw
                    if tokens and len(tokens) >= 2:
                        m['source'] = 'Polymarket'
                        m['clobTokenIds'] = tokens
                        return m
        except Exception:
            pass
        return None

    async def fetch_poly_metadata(self, session) -> list:
        res = await asyncio.gather(*[self._poly_slug(session, s) for s in POLY_SLUGS])
        return [r for r in res if r]

    async def fetch_pred_metadata(self, session) -> list:
        markets = []
        cursor = None
        for _ in range(10):
            params = {"limit": 200, "status": "ACTIVE"}
            if cursor: params["after"] = cursor
            try:
                async with session.get("https://api.predict.fun/v1/markets", headers=self._pred_headers, params=params) as r:
                    if r.status !=
