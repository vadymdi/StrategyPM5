""" CRYPTO ARBITRAGE TERMINAL v37.0 — Fast (hardcoded markets) """
import os
import sys
import asyncio
import aiohttp
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
MAX_CONCURRENT          = 10
BINANCE_FEE_RATE        = 0.0005
PREDICT_DEFAULT_FEE_BPS = 200
POLYMARKET_CRYPTO_FEE   = 0.07

load_dotenv()
PREDICT_API_KEY    = os.getenv("PREDICT_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")

# ── HARDCODED MARKETS ─────────────────────────────────────────────────────────
POLY_MARKETS = [
    {
        "source": "Polymarket",
        "title": "Will Solana hit $60 or $140 first?",
        "slug": "will-solana-hit-60-or-140-first",
        "ticker": "SOLUSDT",
        "target_low": 60.0,
        "target_high": 140.0,
    },
    {
        "source": "Polymarket",
        "title": "Will Ethereum hit $1,000 or $3,000 first?",
        "slug": "will-ethereum-hit-1k-or-3k-first",
        "ticker": "ETHUSDT",
        "target_low": 1000.0,
        "target_high": 3000.0,
    },
    {
        "source": "Polymarket",
        "title": "Will BNB hit $400 or $800 first?",
        "slug": "will-bnb-hit-400-or-800-first",
        "ticker": "BNBUSDT",
        "target_low": 400.0,
        "target_high": 800.0,
    },
]

PRED_MARKETS = [
    {
        "source": "Predict.fun",
        "title": "Will Solana hit $60 or $140 first?",
        "slug": "will-solana-hit-60-or-140-first",
        "ticker": "SOLUSDT",
        "target_low": 60.0,
        "target_high": 140.0,
    },
    {
        "source": "Predict.fun",
        "title": "Will Ethereum hit $1,000 or $3,000 first?",
        "slug": "will-ethereum-hit-1k-or-3k-first",
        "ticker": "ETHUSDT",
        "target_low": 1000.0,
        "target_high": 3000.0,
    },
    {
        "source": "Predict.fun",
        "title": "Will BNB hit $400 or $800 first?",
        "slug": "will-bnb-hit-400-or-800-first",
        "ticker": "BNBUSDT",
        "target_low": 400.0,
        "target_high": 800.0,
    },
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
            "User-Agent": "ArbitrageBot/37.0",
        }
        print(f"\n{'='*65}")
        print(f"  ARBITRAGE TERMINAL v37.0 | Budget: ${BET_AMOUNT:.0f}")
        print(f"  Polymarket + Predict.fun | Fast mode ({len(POLY_MARKETS)+len(PRED_MARKETS)} markets)")
        print(f"{'='*65}\n")

    # ─── PRICES ───────────────────────────────────────────────────────────────
    async def fetch_prices(self, session: aiohttp.ClientSession):
        print("  [1/4] Fetching prices...")

        for url, label in [
            ("https://fapi.binance.com/fapi/v1/ticker/price", "Binance Futures"),
            ("https://api.binance.com/api/v3/ticker/price",   "Binance Spot"),
        ]:
            try:
                async with session.get(url) as r:
                    if r.status == 200:
                        data = await r.json(content_type=None)
                        NEED = {"ETHUSDT", "SOLUSDT", "BNBUSDT"}
                        for x in data:
                            if x['symbol'] in NEED:
                                self.prices[x['symbol']] = float(x['price'])
                        if self.prices:
                            print(f"       ✅ {label}: {len(self.prices)} pairs")
                            return
            except Exception as e:
                print(f"       ❌ {label}: {e}")

        try:
            url = ("https://api.coingecko.com/api/v3/simple/price"
                   "?ids=ethereum,solana,binancecoin&vs_currencies=usd")
            async with session.get(url) as r:
                if r.status == 200:
                    data = await r.json(content_type=None)
                    MAP = {"ethereum": "ETHUSDT", "solana": "SOLUSDT", "binancecoin": "BNBUSDT"}
                    for gid, sym in MAP.items():
                        if gid in data:
                            self.prices[sym] = float(data[gid]['usd'])
                    print(f"       ✅ CoinGecko: {len(self.prices)} pairs")
                    return
        except Exception as e:
            print(f"       ❌ CoinGecko: {e}")

        print("       ❌ All price sources failed!")

    # ─── FEES ─────────────────────────────────────────────────────────────────
    def calc_fee(self, source: str, price: float, investment: float) -> float:
        if not (0 < price < 1):
            return 0.0
        shares = investment / price
        if source == 'Predict.fun':
            return shares * min(price, 1 - price) * (PREDICT_DEFAULT_FEE_BPS / 10_000)
        if source == 'Polymarket':
            return round(shares * POLYMARKET_CRYPTO_FEE * price * (1 - price), 5)
        return 0.0

    # ─── STRATEGY CALC ────────────────────────────────────────────────────────
    def calc_deal(self, price_bet: float, bet_type: str,
                  parsed: MarketParsed, source: str, question: str) -> Optional[Deal]:
        if not (0.05 < price_bet < 0.85):
            return None

        cur, t_low, t_high = parsed.current, parsed.target_low, parsed.target_high
        is_low = bet_type == "LOW"

        pct_up   = ((t_high - cur) / cur) if is_low else ((cur - t_low) / cur)
        pct_down = ((cur - t_low) / cur)  if is_low else ((t_high - cur) / cur)

        if pct_up <= 0 or pct_down <= 0:
            return None

        fee     = self.calc_fee(source, price_bet, BET_AMOUNT)
        fut_fee = 2 * BINANCE_FEE_RATE
        denom   = pct_up + pct_down
        if denom <= 0:
            return None

        payout  = BET_AMOUNT / price_bet
        pos_usd = payout / denom

        net_up   = pos_usd * pct_up   - pos_usd * fut_fee - BET_AMOUNT - fee
        net_down = payout - BET_AMOUNT - fee - pos_usd * pct_down - pos_usd * fut_fee
        profit   = min(net_up, net_down)

        if profit < MIN_PROFIT_USD:
            return None

        strike   = t_low if is_low else t_high
        dist     = abs(cur - strike) / cur
        lev      = min(max(1, int(1 / (dist * SAFETY_FACTOR))), 20) if dist else 1
        invested = BET_AMOUNT + pos_usd / lev + fee
        roi      = (profit / invested) * 100

        return Deal(bet_type=bet_type, bet_price=price_bet,
                    hedge_dir="Long" if is_low else "Short",
                    pos_size_usd=pos_usd, leverage=lev,
                    profit_usd=profit, roi=roi,
                    source=source, question=question, parsed=parsed)

    # ─── POLYMARKET ───────────────────────────────────────────────────────────
    async def _poly_ask(self, session, token_id: str) -> Optional[float]:
        try:
            async with session.get(f"https://clob.polymarket.com/book?token_id={token_id}") as r:
                if r.status == 200:
                    data = await r.json(content_type=None)
                    asks = data.get('asks', [])
                    if asks:
                        return float(min(asks, key=lambda x: float(x['price']))['price'])
        except Exception:
            pass
        return None

    async def fetch_poly_market(self, session, m: dict) -> list:
        cur = self.prices.get(m['ticker'])
        if not cur:
            return []
        if cur <= m['target_low'] or cur >= m['target_high']:
            print(f"       ⏭  Poly {m['ticker']} ${cur:.2f} поза діапазоном")
            return []

        try:
            async with session.get(
                f"https://gamma-api.polymarket.com/markets/slug/{m['slug']}"
            ) as r:
                if r.status != 200:
                    return []
                data   = await r.json(content_type=None)
                raw    = data[0] if isinstance(data, list) and data else data
                tokens_raw = raw.get('clobTokenIds')
                tokens = json.loads(tokens_raw) if isinstance(tokens_raw, str) else tokens_raw
                if not tokens or len(tokens) < 2:
                    return []
        except Exception as e:
            print(f"       ❌ Poly {m['slug']}: {e}")
            return []

        p_yes, p_no = await asyncio.gather(
            self._poly_ask(session, tokens[0]),
            self._poly_ask(session, tokens[1]),
        )
        if not p_yes or not p_no:
            return []

        parsed = MarketParsed(ticker=m['ticker'], target_low=m['target_low'],
                              target_high=m['target_high'], current=cur)
        deals = []
        for price, btype in ((p_yes, "LOW"), (p_no, "HIGH")):
            d = self.calc_deal(price, btype, parsed, "Polymarket", m['title'])
            if d:
                deals.append(d)
        return deals

    # ─── PREDICT.FUN ──────────────────────────────────────────────────────────
    async def _pred_find_active_id(self, session, slug: str) -> Optional[str]:
        """Точковий пошук ID ринку з обов'язковою перевіркою статусу ACTIVE."""
        try:
            for i in range(5):
                async with session.get(
                    "https://api.predict.fun/v1/markets",
                    headers=self._pred_headers,
                    params={
                        "limit": 100, 
                        "offset": i * 100, 
                        "status": "ACTIVE"
                    },
                ) as r:
                    if r.status != 200: 
                        break
                    
                    data = await r.json(content_type=None)
                    items = data.get('data', []) if isinstance(data, dict) else data
                    if not items: 
                        break
                    
                    for item in items:
                        s = str(item.get('slug', '') or item.get('id', ''))
                        if slug == s or slug in s:
                            return str(item['id'])
        except Exception:
            pass
        return None

    async def fetch_pred_market(self, session, m: dict) -> list:
        cur = self.prices.get(m['ticker'])
        if not cur:
            return []
        if cur <= m['target_low'] or cur >= m['target_high']:
            return []

        market_id = await self._pred_find_active_id(session, m['slug'])
        if not market_id:
            print(f"       ❌ Predict.fun: Активний ID не знайдено для {m['slug']}")
            return []

        try:
            async with session.get(
                f"https://api.predict.fun/v1/markets/{market_id}/orderbook",
                headers=self._pred_headers,
            ) as r:
                if r.status != 200:
                    print(f"       ❌ Predict.fun HTTP {r.status} для {m['slug']}")
                    return []
                
                data = await r.json(content_type=None)
                ob   = data.get('data', data)
                asks, bids = ob.get('asks', []), ob.get('bids', [])
                
                if not asks or not bids:
                    print(f"       ⚠️ Predict.fun: Пустий стакан для {m['slug']} (asks: {len(asks)}, bids: {len(bids)})")
                    return []
                
                p_yes = float(min(asks, key=lambda x: float(x[0]))[0])
                p_no  = 1.0 - float(max(bids, key=lambda x: float(x[0]))[0])
        except Exception as e:
            print(f"       ❌ Predict.fun orderbook {market_id}: {e}")
            return []

        parsed = MarketParsed(ticker=m['ticker'], target_low=m['target_low'],
                              target_high=m['target_high'], current=cur)
        deals = []
        for price, btype in ((p_yes, "LOW"), (p_no, "HIGH")):
            d = self.calc_deal(price, btype, parsed, "Predict.fun", m['title'])
            if d:
                deals.append(d)
        return deals

    async def fetch_pred_market(self, session, m: dict) -> list:
        cur = self.prices.get(m['ticker'])
        if not cur:
            return []
        if cur <= m['target_low'] or cur >= m['target_high']:
            return []

        market_id = await self._pred_find_id(session, m['slug'])
        if not market_id:
            print(f"       ❌ Predict.fun: ID не знайдено для {m['slug']}")
            return []

        try:
            async with session.get(
                f"https://api.predict.fun/v1/markets/{market_id}/orderbook",
                headers=self._pred_headers,
            ) as r:
                if r.status != 200:
                    return []
                data = await r.json(content_type=None)
                ob   = data.get('data', data)
                asks, bids = ob.get('asks', []), ob.get('bids', [])
                if not asks or not bids:
                    return []
                p_yes = float(min(asks, key=lambda x: float(x[0]))[0])
                p_no  = 1.0 - float(max(bids, key=lambda x: float(x[0]))[0])
        except Exception as e:
            print(f"       ❌ Predict.fun orderbook {market_id}: {e}")
            return []

        parsed = MarketParsed(ticker=m['ticker'], target_low=m['target_low'],
                              target_high=m['target_high'], current=cur)
        deals = []
        for price, btype in ((p_yes, "LOW"), (p_no, "HIGH")):
            d = self.calc_deal(price, btype, parsed, "Predict.fun", m['title'])
            if d:
                deals.append(d)
        return deals

    # ─── TELEGRAM ─────────────────────────────────────────────────────────────
    async def send_telegram(self, msg: str):
        try:
            await self.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=msg, parse_mode='Markdown')
        except Exception as e:
            print(f"  Telegram: {e}")

    # ─── PRINT ────────────────────────────────────────────────────────────────
    def print_results(self, deals: list) -> list:
        print("=" * 65)
        print("  RESULTS — Strategy 5: Delta-Neutral Synthetic")
        print("=" * 65)

        if not deals:
            print("\n  No profitable opportunities found.\n")
            return []

        deals.sort(key=lambda d: d.roi, reverse=True)
        alerts = []

        for d in deals:
            p        = d.parsed
            fee      = self.calc_fee(d.source, d.bet_price, BET_AMOUNT)
            margin   = d.pos_size_usd / d.leverage
            invested = BET_AMOUNT + margin + fee

            print(f"\n▸ {d.question}")
            print(f"  {d.source} | {p.ticker} ${p.current:,.2f} → "
                  f"[${p.target_low:,.0f} / ${p.target_high:,.0f}]")
            print(f"  Profit: ${d.profit_usd:.2f}  ROI: +{d.roi:.1f}%  Fee: ${fee:.2f}")
            print(f"  1. Buy {d.bet_type} @ {d.bet_price:.3f} → ${BET_AMOUNT:.0f} (MARKET)")
            print(f"  2. {d.hedge_dir} Futures @ ${p.current:,.2f} → ${margin:.2f} (x{d.leverage})")
            print("-" * 65)

            if d.roi >= ROI_THRESHOLD_ALERT:
                alerts.append(
                    f"🚨 *HIGH ROI ALERT*\n\n"
                    f"*Market:* {d.question}\n"
                    f"*Platform:* {d.source}\n"
                    f"*ROI:* +{d.roi:.1f}%  |  *Profit:* ${d.profit_usd:.2f}\n"
                    f"*Invested:* ${invested:.2f}  |  *Fee:* ${fee:.2f}\n\n"
                    f"1. Buy {d.bet_type} @ {d.bet_price:.3f} for ${BET_AMOUNT:.0f}\n"
                    f"2. {d.hedge_dir} @ ${p.current:,.2f} for ${margin:.2f} (x{d.leverage})"
                )

        return alerts

    # ─── MAIN ─────────────────────────────────────────────────────────────────
    async def run(self):
        timeout = aiohttp.ClientTimeout(total=10, connect=3)
        conn    = aiohttp.TCPConnector(limit=MAX_CONCURRENT, ttl_dns_cache=300,
                                       enable_cleanup_closed=True)

        async with aiohttp.ClientSession(connector=conn, timeout=timeout) as session:
            await self.fetch_prices(session)
            if not self.prices:
                print("❌ No prices — aborting.")
                return

            print("  [2/4] Fetching markets (паралельно)...")
            all_results = await asyncio.gather(
                *[self.fetch_poly_market(session, m) for m in POLY_MARKETS],
                *[self.fetch_pred_market(session, m) for m in PRED_MARKETS],
                return_exceptions=True
            )

            all_deals = []
            for r in all_results:
                if isinstance(r, list):
                    all_deals.extend(r)

            print(f"       Перевірено: {len(POLY_MARKETS)+len(PRED_MARKETS)} ринків")
            print(f"  [3/4] Аналіз завершено")
            print(f"  [4/4] {len(all_deals)} deals found\n")

        alerts = self.print_results(all_deals)
        if alerts and self.bot:
            for alert in alerts:
                await self.send_telegram(alert)
            print(f"\n📱 {len(alerts)} Telegram alerts sent")


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
