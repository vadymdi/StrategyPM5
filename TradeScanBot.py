""" CRYPTO ARBITRAGE TERMINAL v41.0 — Direct Target Mode """
import os
import sys
import asyncio
import aiohttp
import re
import json
from dotenv import load_dotenv
from dataclasses import dataclass
from typing import Optional
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
    "BTC": "BTCUSDT", "ETH": "ETHUSDT", 
    "SOL": "SOLUSDT", "BNB": "BNBUSDT"
}

# Жорстко задані ринки для прямого запиту
TARGET_SLUGS = [
    "will-solana-hit-60-or-140-first",
    "will-ethereum-hit-1k-or-3k-first",
    "will-bnb-hit-400-or-800-first"
]

# ── DATACLASSES ───────────────────────────────────────────────────────────────
@dataclass
class MarketParsed:
    ticker: str
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
            "User-Agent": "ArbitrageBot/41.0",
        }
        print(f"\n{'='*65}")
        print(f"  ARBITRAGE TERMINAL v41.0 | Budget: ${BET_AMOUNT:.0f}")
        print(f"  Direct Target Mode: {len(TARGET_SLUGS)} specific markets")
        print(f"{'='*65}\n")

    # ─── PRICES ───────────────────────────────────────────────────────────────
    async def fetch_prices(self, session: aiohttp.ClientSession):
        print("  [1/4] Fetching prices...")
        try:
            url = "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin,ethereum,solana,binancecoin&vs_currencies=usd"
            async with session.get(url) as r:
                if r.status == 200:
                    data = await r.json(content_type=None)
                    MAP = {"bitcoin": "BTCUSDT", "ethereum": "ETHUSDT", "solana": "SOLUSDT", "binancecoin": "BNBUSDT"}
                    for gid, sym in MAP.items():
                        if gid in data:
                            self.prices[sym] = float(data[gid]['usd'])
                    print(f"       ✅ Prices updated: {len(self.prices)} pairs")
                    return
        except Exception as e:
            print(f"       ❌ Prices failed: {e}")

    # ─── PARSING ──────────────────────────────────────────────────────────────
    def parse_market(self, question: str) -> Optional[MarketParsed]:
        if not question: return None
        q = question.lower()
        
        ticker = None
        for name, sym in ASSETS_MAP.items():
            if name.lower() in q:
                ticker = sym
                break
        if not ticker or ticker not in self.prices: 
            return None

        cur = self.prices[ticker]
        nums = []
        for raw in re.findall(r'[\d,]+\.?\d*[kK]?', q):
            s = raw.lower().replace(',', '')
            mult = 1000 if 'k' in s else 1
            try:
                nums.append(float(s.replace('k', '')) * mult)
            except ValueError:
                pass

        if len(nums) < 2: return None
        return MarketParsed(ticker=ticker, target_low=min(nums), target_high=max(nums), current=cur)

    # ─── METADATA (DIRECT FETCH) ──────────────────────────────────────────────
    async def _fetch_poly_direct(self, session, slug: str) -> Optional[dict]:
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

    async def _fetch_pred_direct(self, session, slug: str) -> Optional[dict]:
        # Використовуємо ендпоінт /v1/markets/{id} напряму згідно специфікації
        try:
            async with session.get(f"https://api.predict.fun/v1/markets/{slug}", headers=self._pred_headers) as r:
                if r.status == 200:
                    res = await r.json(content_type=None)
                    m = res.get('data')
                    if m:
                        m['source'] = 'Predict.fun'
                        return m
        except Exception:
            pass
        return None

    # ─── ORDERBOOKS ───────────────────────────────────────────────────────────
    @staticmethod
    def _yes_is_low(question: str) -> bool:
        nums = re.findall(r'[\d,]+\.?\d*', question)
        if len(nums) >= 2:
            return float(nums[0].replace(',', '')) <= float(nums[1].replace(',', ''))
        return True

    async def analyze_market(self, session, m: dict, p: MarketParsed) -> list:
        src, q = m['source'], m.get('title') or m.get('question') or ''
        p_low = p_high = None

        if src == 'Polymarket':
            tokens = m.get('clobTokenIds', [])
            if len(tokens) >= 2:
                try:
                    r_yes = await session.get(f"https://clob.polymarket.com/book?token_id={tokens[0]}")
                    r_no = await session.get(f"https://clob.polymarket.com/book?token_id={tokens[1]}")
                    d_yes, d_no = await r_yes.json(), await r_no.json()
                    
                    if d_yes.get('asks') and d_no.get('asks'):
                        p_yes = float(min(d_yes['asks'], key=lambda x: float(x['price']))['price'])
                        p_no = float(min(d_no['asks'], key=lambda x: float(x['price']))['price'])
                        p_low, p_high = (p_yes, p_no) if self._yes_is_low(q) else (p_no, p_yes)
                except Exception:
                    pass

        elif src == 'Predict.fun':
            try:
                # Отримуємо стакан використовуючи числовий ID який прийшов з метадати
                market_id = m.get('id')
                async with session.get(f"https://api.predict.fun/v1/markets/{market_id}/orderbook", headers=self._pred_headers) as r:
                    if r.status == 200:
                        data = await r.json(content_type=None)
                        ob = data.get('data', data)
                        asks, bids = ob.get('asks', []), ob.get('bids', [])
                        if asks and bids:
                            p_yes = float(min(asks, key=lambda x: float(x[0]))[0])
                            p_no  = 1.0 - float(max(bids, key=lambda x: float(x[0]))[0])
                            p_low, p_high = (p_yes, p_no) if self._yes_is_low(q) else (p_no, p_yes)
            except Exception:
                pass

        deals = []
        for price, btype in ((p_low, "LOW"), (p_high, "HIGH")):
            if price and (0.05 < price < 0.85):
                is_low = btype == "LOW"
                pct_up = ((p.target_high - p.current) / p.current) if is_low else ((p.current - p.target_low) / p.current)
                pct_down = ((p.current - p.target_low) / p.current) if is_low else ((p.target_high - p.current) / p.current)
                
                if pct_up > 0 and pct_down > 0:
                    shares = BET_AMOUNT / price
                    fee = (shares * min(price, 1 - price) * (PREDICT_DEFAULT_FEE_BPS / 10000)) if src == 'Predict.fun' else (shares * POLYMARKET_CRYPTO_FEE * price * (1 - price))
                    
                    pos_usd = (BET_AMOUNT / price) / (pct_up + pct_down)
                    profit = min(
                        pos_usd * pct_up - pos_usd * (2 * BINANCE_FEE_RATE) - BET_AMOUNT - fee,
                        (BET_AMOUNT / price) - BET_AMOUNT - fee - pos_usd * pct_down - pos_usd * (2 * BINANCE_FEE_RATE)
                    )
                    
                    if profit >= MIN_PROFIT_USD:
                        dist = abs(p.current - (p.target_low if is_low else p.target_high)) / p.current
                        lev = min(max(1, int(1 / (dist * SAFETY_FACTOR))), 20) if dist else 1
                        roi = (profit / (BET_AMOUNT + pos_usd / lev + fee)) * 100
                        
                        deals.append(Deal(btype, price, "Long" if is_low else "Short", pos_usd, lev, profit, roi, src, q, p))
        return deals

    # ─── MAIN EXECUTOR ────────────────────────────────────────────────────────
    async def run(self):
        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(limit=MAX_CONCURRENT)) as session:
            await self.fetch_prices(session)
            if not self.prices: return

            print("  [2/4] Fetching specific targets...")
            poly_tasks = [self._fetch_poly_direct(session, s) for s in TARGET_SLUGS]
            pred_tasks = [self._fetch_pred_direct(session, s) for s in TARGET_SLUGS]
            
            raw_results = await asyncio.gather(*(poly_tasks + pred_tasks))
            valid_markets = [m for m in raw_results if m]
            
            poly_count = sum(1 for m in valid_markets if m['source'] == 'Polymarket')
            pred_count = sum(1 for m in valid_markets if m['source'] == 'Predict.fun')
            
            print(f"       ✅ Polymarket: {poly_count} target(s) found")
            print(f"       ✅ Predict.fun: {pred_count} target(s) found")

            print("  [3/4] Parsing & Fetching Orderbooks...")
            tasks = []
            for m in valid_markets:
                p = self.parse_market(m.get('title') or m.get('question') or '')
                if p: tasks.append(self.analyze_market(session, m, p))

            deals = []
            for res in await asyncio.gather(*tasks):
                deals.extend(res)

            print(f"  [4/4] {len(deals)} deals found\n")
            
            if not deals:
                print("  No profitable opportunities found.\n")
                return

            deals.sort(key=lambda d: d.roi, reverse=True)
            print("=" * 65)
            print("  RESULTS — Strategy 5: Delta-Neutral Synthetic")
            print("=" * 65)
            
            for d in deals:
                margin = d.pos_size_usd / d.leverage
                print(f"\n▸ {d.question}\n  {d.source} | {d.parsed.ticker} ${d.parsed.current:,.2f} → [${d.parsed.target_low:,.0f} / ${d.parsed.target_high:,.0f}]")
                print(f"  Profit: ${d.profit_usd:.2f}  ROI: +{d.roi:.1f}%")
                print(f"  1. Buy {d.bet_type} @ {d.bet_price:.3f} → ${BET_AMOUNT:.0f} (MARKET)")
                print(f"  2. {d.hedge_dir} Futures @ ${d.parsed.current:,.2f} → ${margin:.2f} (x{d.leverage})")
                print("-" * 65)

if __name__ == "__main__":
    sys.stdout.reconfigure(line_buffering=True)
    asyncio.run(ArbitrageScanner().run())
