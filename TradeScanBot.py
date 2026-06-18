""" CRYPTO ARBITRAGE TERMINAL v43.1 — True Fee Integration + Telegram Fixed """
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
ROI_THRESHOLD_ALERT     = 10.0
SAFETY_FACTOR           = 1.15
MAX_CONCURRENT          = 20
BINANCE_FEE_RATE        = 0.0005
PREDICT_FEE_RATE        = 0.02
POLYMARKET_CRYPTO_FEE   = 0.07

load_dotenv()
PREDICT_API_KEY    = os.getenv("PREDICT_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")

ASSETS_MAP = {
    "BTC": "BTCUSDT", "ETH": "ETHUSDT",
    "SOL": "SOLUSDT", "BNB": "BNBUSDT"
}

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
    fee_usd: float
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
            "User-Agent": "ArbitrageBot/43.1",
        }
        print(f"\n{'='*65}")
        print(f"  ARBITRAGE TERMINAL v43.1 | Budget: ${BET_AMOUNT:.0f}")
        print(f"  Sniper Mode + True Protocol Fees + Telegram Fixed")
        print(f"{'='*65}\n")

    # ─── PRICES ───────────────────────────────────────────────────────────────
    async def fetch_prices(self, session: aiohttp.ClientSession):
        print("  [1/4] Fetching prices...")
        try:
            url = ("https://api.coingecko.com/api/v3/simple/price"
                   "?ids=bitcoin,ethereum,solana,binancecoin&vs_currencies=usd")
            async with session.get(url) as r:
                if r.status == 200:
                    data = await r.json(content_type=None)
                    MAP = {"bitcoin": "BTCUSDT", "ethereum": "ETHUSDT",
                           "solana": "SOLUSDT", "binancecoin": "BNBUSDT"}
                    for gid, sym in MAP.items():
                        if gid in data:
                            self.prices[sym] = float(data[gid]['usd'])
                    print(f"       ✅ Prices: {len(self.prices)} pairs")
                    return
        except Exception as e:
            print(f"       ❌ Prices failed: {e}")

    # ─── PARSING ──────────────────────────────────────────────────────────────
    def parse_market(self, question: str) -> Optional[MarketParsed]:
        if not question:
            return None
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

        if len(nums) < 2:
            return None
        return MarketParsed(ticker=ticker, target_low=min(nums),
                            target_high=max(nums), current=cur)

    # ─── FETCH MARKETS ────────────────────────────────────────────────────────
    async def fetch_poly_targets(self, session) -> list:
        slugs = [
            "will-solana-hit-60-or-140-first",
            "will-ethereum-hit-1k-or-3k-first",
            "will-bnb-hit-400-or-800-first",
        ]
        results = []
        for slug in slugs:
            try:
                async with session.get(
                    f"https://gamma-api.polymarket.com/markets/slug/{slug}"
                ) as r:
                    if r.status == 200:
                        data = await r.json(content_type=None)
                        m = data[0] if isinstance(data, list) and data else data
                        raw = m.get('clobTokenIds')
                        tokens = json.loads(raw) if isinstance(raw, str) else raw
                        if tokens and len(tokens) >= 2:
                            m['source'] = 'Polymarket'
                            m['clobTokenIds'] = tokens
                            results.append(m)
            except Exception:
                pass
        return results

    async def fetch_pred_targets(self, session) -> list:
        found_markets = []
        cursor = None

        for _ in range(10):
            params = {"first": 100, "status": "OPEN"}
            if cursor:
                params["after"] = cursor
            try:
                async with session.get(
                    "https://api.predict.fun/v1/markets",
                    headers=self._pred_headers,
                    params=params,
                ) as r:
                    if r.status != 200:
                        break
                    data = await r.json(content_type=None)
                    items = data.get('data', [])
                    if not items:
                        break
                    for m in items:
                        t = (m.get('title') or m.get('question') or '').lower()
                        is_sol = "solana" in t and "60" in t and "140" in t
                        is_eth = "eth" in t and ("1k" in t or "1000" in t or "1,000" in t) and ("3k" in t or "3000" in t or "3,000" in t)
                        is_bnb = "bnb" in t and "400" in t and "800" in t
                        if is_sol or is_eth or is_bnb:
                            m['source'] = 'Predict.fun'
                            found_markets.append(m)
                    cursor = data.get('cursor') if isinstance(data, dict) else None
                    if not cursor:
                        break
            except Exception:
                break
        return found_markets

    # ─── ORDERBOOK & CALC ─────────────────────────────────────────────────────
    @staticmethod
    def _yes_is_low(question: str) -> bool:
        nums = re.findall(r'[\d,]+\.?\d*', question)
        if len(nums) >= 2:
            return float(nums[0].replace(',', '')) <= float(nums[1].replace(',', ''))
        return True

    async def analyze_market(self, session, m: dict, p: MarketParsed) -> list:
        src = m['source']
        q   = m.get('title') or m.get('question') or ''
        p_low = p_high = None

        if src == 'Polymarket':
            tokens = m.get('clobTokenIds', [])
            if len(tokens) >= 2:
                try:
                    async with session.get(
                        f"https://clob.polymarket.com/book?token_id={tokens[0]}"
                    ) as r_yes, session.get(
                        f"https://clob.polymarket.com/book?token_id={tokens[1]}"
                    ) as r_no:
                        d_yes = await r_yes.json()
                        d_no  = await r_no.json()
                        if d_yes.get('asks') and d_no.get('asks'):
                            py = float(min(d_yes['asks'], key=lambda x: float(x['price']))['price'])
                            pn = float(min(d_no['asks'],  key=lambda x: float(x['price']))['price'])
                            p_low, p_high = (py, pn) if self._yes_is_low(q) else (pn, py)
                except Exception:
                    pass

        elif src == 'Predict.fun':
            try:
                market_id = m.get('id')
                async with session.get(
                    f"https://api.predict.fun/v1/markets/{market_id}/orderbook",
                    headers=self._pred_headers,
                ) as r:
                    if r.status == 200:
                        data = await r.json(content_type=None)
                        ob   = data.get('data', data)
                        asks, bids = ob.get('asks', []), ob.get('bids', [])
                        if asks and bids:
                            p_yes = float(min(asks, key=lambda x: float(x[0]))[0])
                            p_no  = 1.0 - float(max(bids, key=lambda x: float(x[0]))[0])
                            p_low, p_high = (p_yes, p_no) if self._yes_is_low(q) else (p_no, p_yes)
            except Exception:
                pass

        deals = []
        for price, btype in ((p_low, "LOW"), (p_high, "HIGH")):
            if not price or not (0.05 < price < 0.85):
                continue

            is_low   = btype == "LOW"
            pct_up   = ((p.target_high - p.current) / p.current) if is_low else ((p.current - p.target_low)  / p.current)
            pct_down = ((p.current - p.target_low)  / p.current) if is_low else ((p.target_high - p.current) / p.current)

            if pct_up <= 0 or pct_down <= 0:
                continue

            shares = BET_AMOUNT / price
            fee = 0.0
            if src == 'Predict.fun':
                fee = PREDICT_FEE_RATE * min(price, 1 - price) * shares
            elif src == 'Polymarket':
                fee = shares * POLYMARKET_CRYPTO_FEE * price * (1 - price)

            net_payout = shares - fee
            denom = pct_up + pct_down
            if denom <= 0:
                continue

            pos_usd  = net_payout / denom
            fut_fee  = 2 * BINANCE_FEE_RATE
            net_up   = pos_usd * pct_up   - pos_usd * fut_fee - BET_AMOUNT
            net_down = net_payout - BET_AMOUNT - pos_usd * pct_down - pos_usd * fut_fee
            profit   = min(net_up, net_down)

            if profit < MIN_PROFIT_USD:
                continue

            dist     = abs(p.current - (p.target_low if is_low else p.target_high)) / p.current
            lev      = min(max(1, int(1 / (dist * SAFETY_FACTOR))), 20) if dist else 1
            invested = BET_AMOUNT + (pos_usd / lev)
            roi      = (profit / invested) * 100

            deals.append(Deal(btype, price, "Long" if is_low else "Short",
                              pos_usd, lev, profit, roi, fee, src, q, p))
        return deals

    # ─── TELEGRAM ─────────────────────────────────────────────────────────────
    async def send_telegram(self, msg: str):
        try:
            await self.bot.send_message(
                chat_id=TELEGRAM_CHAT_ID, text=msg, parse_mode='Markdown'
            )
        except Exception as e:
            print(f"  ❌ Telegram error: {e}")

    # ─── MAIN ─────────────────────────────────────────────────────────────────
    async def run(self):
        async with aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(limit=MAX_CONCURRENT)
        ) as session:
            await self.fetch_prices(session)
            if not self.prices:
                return

            print("  [2/4] Fetching specific targets...")
            poly_markets, pred_markets = await asyncio.gather(
                self.fetch_poly_targets(session),
                self.fetch_pred_targets(session),
            )
            print(f"       ✅ Polymarket: {len(poly_markets)} target(s)")
            print(f"       ✅ Predict.fun: {len(pred_markets)} target(s)")

            print("  [3/4] Parsing & Fetching Orderbooks...")
            tasks = []
            for m in poly_markets + pred_markets:
                p = self.parse_market(m.get('title') or m.get('question') or '')
                if p:
                    tasks.append(self.analyze_market(session, m, p))

            deals = []
            for res in await asyncio.gather(*tasks):
                deals.extend(res)

            print(f"  [4/4] {len(deals)} deals found\n")

            if not deals:
                print("  No profitable opportunities found.\n")
                # Повідомлення якщо нічого не знайдено
                if self.bot:
                    await self.send_telegram("ℹ️ Scan complete — no profitable opportunities found.")
                return

            deals.sort(key=lambda d: d.roi, reverse=True)
            print("=" * 65)
            print("  RESULTS — Strategy 5: Delta-Neutral Synthetic")
            print("=" * 65)

            alerts = []
            for d in deals:
                margin       = d.pos_size_usd / d.leverage
                payout_gross = BET_AMOUNT / d.bet_price
                invested     = BET_AMOUNT + margin

                print(f"\n▸ {d.question}")
                print(f"  {d.source} | {d.parsed.ticker} ${d.parsed.current:,.2f} → "
                      f"[${d.parsed.target_low:,.0f} / ${d.parsed.target_high:,.0f}]")
                print(f"  Profit: ${d.profit_usd:.2f}  |  ROI: +{d.roi:.1f}%  |  Fee: ${d.fee_usd:.2f}")
                print(f"  1. Buy {d.bet_type} @ {d.bet_price:.3f} → "
                      f"Cost: ${BET_AMOUNT:.0f} (Net Payout: ${payout_gross - d.fee_usd:.2f})")
                print(f"  2. {d.hedge_dir} Futures @ ${d.parsed.current:,.2f} → "
                      f"${margin:.2f} (x{d.leverage})")
                print("-" * 65)

                # ── TELEGRAM ALERT ────────────────────────────────────────────
                if d.roi >= ROI_THRESHOLD_ALERT:
                    alerts.append(
                        f"🚨 *ARBITRAGE ALERT*\n\n"
                        f"*Market:* {d.question}\n"
                        f"*Platform:* {d.source}\n"
                        f"*ROI:* +{d.roi:.1f}%  |  *Profit:* ${d.profit_usd:.2f}\n"
                        f"*Invested:* ${invested:.2f}  |  *Fee:* ${d.fee_usd:.2f}\n\n"
                        f"1️⃣ Buy *{d.bet_type}* @ {d.bet_price:.3f} → ${BET_AMOUNT:.0f}\n"
                        f"2️⃣ {d.hedge_dir} Futures @ ${d.parsed.current:,.2f} → "
                        f"${margin:.2f} (x{d.leverage})"
                    )

            if alerts and self.bot:
                for alert in alerts:
                    await self.send_telegram(alert)
                print(f"\n📱 {len(alerts)} Telegram alert(s) sent")
            elif not self.bot:
                print("\n⚠️  Telegram не налаштований (перевір TELEGRAM_BOT_TOKEN і TELEGRAM_CHAT_ID)")


# ── ENTRY ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    sys.stdout.reconfigure(line_buffering=True)
    print(f"🤖 Scan started | Budget: ${BET_AMOUNT}")
    try:
        asyncio.run(ArbitrageScanner().run())
        print("✅ Scan complete.")
    except Exception as e:
        import traceback
        print(f"❌ Fatal: {e}")
        traceback.print_exc()
        sys.exit(1)
