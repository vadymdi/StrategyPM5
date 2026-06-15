""" CRYPTO ARBITRAGE TERMINAL v40.0 — Hybrid Bulk-Scan & Fast Orderbooks """
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
MAX_CONCURRENT          = 50
BINANCE_FEE_RATE        = 0.0005
PREDICT_DEFAULT_FEE_BPS = 200
POLYMARKET_CRYPTO_FEE   = 0.07

load_dotenv()
PREDICT_API_KEY    = os.getenv("PREDICT_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")

# Розширений список з v36.1 для максимального охоплення
ASSETS_MAP = {
    "BTC": "BTCUSDT", "Bitcoin": "BTCUSDT",
    "ETH": "ETHUSDT", "Ethereum": "ETHUSDT", "Ether": "ETHUSDT",
    "SOL": "SOLUSDT", "Solana": "SOLUSDT",
    "BNB": "BNBUSDT", "Binance Coin": "BNBUSDT",
    "XRP": "XRPUSDT", "Ripple": "XRPUSDT",
    "DOGE": "DOGEUSDT", "DOGECOIN": "DOGEUSDT",
    "ADA": "ADAUSDT", "Cardano": "ADAUSDT",
    "AVAX": "AVAXUSDT", "Avalanche": "AVAXUSDT",
    "LINK": "LINKUSDT", "Chainlink": "LINKUSDT",
    "LTC": "LTCUSDT", "Litecoin": "LTCUSDT",
    "DOT": "DOTUSDT", "Polkadot": "DOTUSDT",
    "TRX": "TRXUSDT", "SHIB": "SHIBUSDT",
    "SUI": "SUIUSDT", "Sui": "SUIUSDT",
    "APT": "APTUSDT", "Aptos": "APTUSDT",
    "TON": "TONUSDT", "Toncoin": "TONUSDT",
    "NEAR": "NEARUSDT", "ATOM": "ATOMUSDT",
    "ARB": "ARBUSDT", "OP": "OPUSDT",
    "PEPE": "PEPEUSDT", "WIF": "WIFUSDT",
    "BCH": "BCHUSDT", "BITCOIN CASH": "BCHUSDT",
    "UNI": "UNIUSDT", "AAVE": "AAVEUSDT", "MKR": "MKRUSDT",
    "LDO": "LDOUSDT", "ENA": "ENAUSDT",
    "MATIC": "MATICUSDT", "Polygon": "MATICUSDT",
    "XLM": "XLMUSDT", "Stellar": "XLMUSDT",
    "HBAR": "HBARUSDT", "KAS": "KASUSDT",
    "ETC": "ETCUSDT", "FIL": "FILUSDT", "ICP": "ICPUSDT",
    "INJ": "INJUSDT", "TIA": "TIAUSDT", "SEI": "SEIUSDT",
    "FTM": "FTMUSDT", "TAO": "TAOUSDT", "WLD": "WLDUSDT",
    "BONK": "1000BONKUSDT", "FLOKI": "FLOKIUSDT",
    "RNDR": "RNDRUSDT", "RENDER": "RENDERUSDT",
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
            "User-Agent": "ArbitrageBot/40.0",
        }
        print(f"\n{'='*65}")
        print(f"  ARBITRAGE TERMINAL v40.0 | Budget: ${BET_AMOUNT:.0f}")
        print(f"  Polymarket + Predict.fun | Hybrid Logic")
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
            ids = ("bitcoin,ethereum,solana,binancecoin,ripple,dogecoin,cardano,"
                   "avalanche-2,chainlink,litecoin,polkadot,tron,monero,near,"
                   "cosmos,shiba-inu,sui,aptos,the-open-network,arbitrum,optimism,"
                   "pepe,dogwifcoin,bitcoin-cash,uniswap,aave,maker,lido-dao,ethena,"
                   "matic-network,stellar,hedera-hashgraph,kaspa,ethereum-classic,"
                   "filecoin,internet-computer,injective-protocol,celestia,sei-network,"
                   "fantom,bittensor,worldcoin-wld,bonk,floki,render-token")
            url = f"https://api.coingecko.com/api/v3/simple/price?ids={ids}&vs_currencies=usd"
            async with session.get(url) as r:
                if r.status == 200:
                    data = await r.json(content_type=None)
                    GECKO_MAP = {
                        "bitcoin": "BTCUSDT", "ethereum": "ETHUSDT", "solana": "SOLUSDT",
                        "binancecoin": "BNBUSDT", "ripple": "XRPUSDT", "dogecoin": "DOGEUSDT",
                        "cardano": "ADAUSDT", "avalanche-2": "AVAXUSDT", "chainlink": "LINKUSDT",
                        "litecoin": "LTCUSDT", "polkadot": "DOTUSDT", "tron": "TRXUSDT",
                        "monero": "XMRUSDT", "near": "NEARUSDT", "cosmos": "ATOMUSDT",
                        "shiba-inu": "SHIBUSDT", "sui": "SUIUSDT", "aptos": "APTUSDT",
                        "the-open-network": "TONUSDT", "arbitrum": "ARBUSDT", "optimism": "OPUSDT",
                        "pepe": "PEPEUSDT", "dogwifcoin": "WIFUSDT", "bitcoin-cash": "BCHUSDT",
                        "uniswap": "UNIUSDT", "aave": "AAVEUSDT", "maker": "MKRUSDT",
                        "lido-dao": "LDOUSDT", "ethena": "ENAUSDT", "matic-network": "MATICUSDT",
                        "stellar": "XLMUSDT", "hedera-hashgraph": "HBARUSDT", "kaspa": "KASUSDT",
                        "ethereum-classic": "ETCUSDT", "filecoin": "FILUSDT",
                        "internet-computer": "ICPUSDT", "injective-protocol": "INJUSDT",
                        "celestia": "TIAUSDT", "sei-network": "SEIUSDT", "fantom": "FTMUSDT",
                        "bittensor": "TAOUSDT", "worldcoin-wld": "WLDUSDT", "bonk": "1000BONKUSDT",
                        "floki": "FLOKIUSDT", "render-token": "RNDRUSDT",
                    }
                    for gid, sym in GECKO_MAP.items():
                        if gid in data and 'usd' in data[gid]:
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
        if "above" in q or "below" in q: return None

        ticker = asset = None
        for name in sorted(ASSETS_MAP, key=len, reverse=True):
            if re.search(r'(?<![a-z])' + re.escape(name.lower()) + r'(?![a-z])', q):
                if ASSETS_MAP[name] in self.prices:
                    ticker, asset = ASSETS_MAP[name], name
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
        markets = [r for r in res if r]
        print(f"       ✅ Polymarket: {len(markets)} markets")
        return markets

    async def fetch_pred_metadata(self, session) -> list:
        IGNORE = {'RESOLVED', 'CLOSED', 'CANCELLED'}
        asset_keys = {k.lower() for k in ASSETS_MAP}
        markets = []
        cursor = None
        pages = 0

        sys.stdout.write("       🔄 Predict.fun scanning: ")
        # Ліміт у 100 сторінок (20 000 ринків), щоб не потрапити в timeout, 
        # або до 1000 знайдених крипто-ринків
        while len(markets) < 1000 and pages < 100:
            try:
                params = {"limit": 200}
                if cursor: params["after"] = cursor
                
                async with session.get("https://api.predict.fun/v1/markets", headers=self._pred_headers, params=params) as r:
                    if r.status != 200: 
                        break
                    data = await r.json(content_type=None)
                    items = data.get('data', []) if isinstance(data, dict) else data
                    if not items: 
                        break
                    
                    for m in items:
                        if m.get('status') in IGNORE or m.get('resolution'): 
                            continue
                        q = (m.get('title') or m.get('question') or '').lower()
                        if any(k in q for k in asset_keys):
                            m['source'] = 'Predict.fun'
                            markets.append(m)
                            
                    cursor = data.get('cursor') if isinstance(data, dict) else None
                    pages += 1
                    sys.stdout.write(".")
                    sys.stdout.flush()
                    if not cursor: 
                        break
            except Exception:
                break
                
        print(f"\n       ✅ Predict.fun: {len(markets)} matched markets (checked {pages} pages)")
        return markets

    # ─── ORDERBOOKS & CALC ────────────────────────────────────────────────────
    @staticmethod
    def _yes_is_low(question: str) -> bool:
        nums = re.findall(r'[\d,]+\.?\d*', question)
        if len(nums) >= 2:
            return float(nums[0].replace(',', '')) <= float(nums[1].replace(',', ''))
        return True

    async def _poly_ask(self, session, token_id: str) -> Optional[float]:
        try:
            async with session.get(f"https://clob.polymarket.com/book?token_id={token_id}") as r:
                if r.status == 200:
                    data = await r.json(content_type=None)
                    asks = data.get('asks', [])
                    if asks: return float(min(asks, key=lambda x: float(x['price']))['price'])
        except Exception:
            pass
        return None

    async def _pred_prices(self, session, market_id: str, question: str):
        try:
            async with session.get(f"https://api.predict.fun/v1/markets/{market_id}/orderbook", headers=self._pred_headers) as r:
                if r.status == 200:
                    data = await r.json(content_type=None)
                    ob = data.get('data', data)
                    asks, bids = ob.get('asks', []), ob.get('bids', [])
                    if asks and bids:
                        p_yes = float(min(asks, key=lambda x: float(x[0]))[0])
                        p_no  = 1.0 - float(max(bids, key=lambda x: float(x[0]))[0])
                        return (p_yes, p_no) if self._yes_is_low(question) else (p_no, p_yes)
        except Exception:
            pass
        return None, None

    def calc_deal(self, price_bet: float, bet_type: str, p: MarketParsed, source: str, question: str) -> Optional[Deal]:
        if not (0.05 < price_bet < 0.85): return None
        is_low = bet_type == "LOW"

        pct_up   = ((p.target_high - p.current) / p.current) if is_low else ((p.current - p.target_low) / p.current)
        pct_down = ((p.current - p.target_low) / p.current)  if is_low else ((p.target_high - p.current) / p.current)
        if pct_up <= 0 or pct_down <= 0: return None

        fee = 0.0
        shares = BET_AMOUNT / price_bet
        if source == 'Predict.fun':
            fee = shares * min(price_bet, 1 - price_bet) * (PREDICT_DEFAULT_FEE_BPS / 10_000)
        elif source == 'Polymarket':
            fee = shares * POLYMARKET_CRYPTO_FEE * price_bet * (1 - price_bet)

        denom = pct_up + pct_down
        if denom <= 0: return None

        payout = BET_AMOUNT / price_bet
        pos_usd = payout / denom
        fut_fee = 2 * BINANCE_FEE_RATE

        net_up   = pos_usd * pct_up   - pos_usd * fut_fee - BET_AMOUNT - fee
        net_down = payout - BET_AMOUNT - fee - pos_usd * pct_down - pos_usd * fut_fee
        profit   = min(net_up, net_down)

        if profit < MIN_PROFIT_USD: return None

        strike = p.target_low if is_low else p.target_high
        dist   = abs(p.current - strike) / p.current
        lev    = min(max(1, int(1 / (dist * SAFETY_FACTOR))), 20) if dist else 1
        inv    = BET_AMOUNT + pos_usd / lev + fee

        return Deal(bet_type, price_bet, "Long" if is_low else "Short", pos_usd, lev, profit, (profit / inv) * 100, source, question, p)

    async def analyze_market(self, session, m: dict, parsed: MarketParsed) -> list:
        src, q = m['source'], m.get('title') or m.get('question') or ''
        p_low = p_high = None

        if src == 'Polymarket':
            tokens = m.get('clobTokenIds', [])
            if len(tokens) >= 2:
                p_yes, p_no = await asyncio.gather(self._poly_ask(session, tokens[0]), self._poly_ask(session, tokens[1]))
                if p_yes and p_no:
                    p_low, p_high = (p_yes, p_no) if self._yes_is_low(q) else (p_no, p_yes)

        elif src == 'Predict.fun':
            p_low, p_high = await self._pred_prices(session, m['id'], q)

        deals = []
        for price, btype in ((p_low, "LOW"), (p_high, "HIGH")):
            if price:
                d = self.calc_deal(price, btype, parsed, src, q)
                if d: deals.append(d)
        return deals

    # ─── MAIN EXECUTOR ────────────────────────────────────────────────────────
    async def run(self):
        conn = aiohttp.TCPConnector(limit=MAX_CONCURRENT, ttl_dns_cache=300, enable_cleanup_closed=True)
        timeout = aiohttp.ClientTimeout(total=30)
        
        async with aiohttp.ClientSession(connector=conn, timeout=timeout) as session:
            await self.fetch_prices(session)
            if not self.prices: return

            print("  [2/4] Fetching bulk metadata...")
            poly_m = await self.fetch_poly_metadata(session)
            pred_m = await self.fetch_pred_metadata(session)
            all_m = poly_m + pred_m
            
            parseable = []
            for m in all_m:
                q = m.get('title') or m.get('question') or ''
                p = self.parse_market(q)
                if p: parseable.append((m, p))
                
            print(f"       Total Valid Formats to Scan: {len(parseable)}")
            if not parseable:
                print("❌ No matching markets found. Aborting.")
                return
                
            print("  [3/4] Fetching specific orderbooks concurrently...")

            sem = asyncio.Semaphore(MAX_CONCURRENT)
            async def safe_analyze(m, p):
                async with sem:
                    return await self.analyze_market(session, m, p)

            all_deals = []
            tasks = [safe_analyze(m, p) for m, p in parseable]
            for i, coro in enumerate(asyncio.as_completed(tasks), 1):
                try:
                    res = await coro
                    all_deals.extend(res)
                except Exception:
                    pass
                sys.stdout.write(f"\r       Orderbooks: {i}/{len(tasks)}")
                sys.stdout.flush()

            print(f"\n  [4/4] {len(all_deals)} deals found\n")
            
            # Print & Alert
            if not all_deals:
                print("  No profitable opportunities found.\n")
                return

            all_deals.sort(key=lambda d: d.roi, reverse=True)
            print("=" * 65)
            print("  RESULTS — Strategy 5: Delta-Neutral Synthetic")
            print("=" * 65)
            
            for d in all_deals:
                margin = d.pos_size_usd / d.leverage
                print(f"\n▸ {d.question}\n  {d.source} | {d.parsed.ticker} ${d.parsed.current:,.2f} → [${d.parsed.target_low:,.0f} / ${d.parsed.target_high:,.0f}]")
                print(f"  Profit: ${d.profit_usd:.2f}  ROI: +{d.roi:.1f}%")
                print(f"  1. Buy {d.bet_type} @ {d.bet_price:.3f} → ${BET_AMOUNT:.0f} (MARKET)")
                print(f"  2. {d.hedge_dir} Futures @ ${d.parsed.current:,.2f} → ${margin:.2f} (x{d.leverage})")
                print("-" * 65)
                
                if d.roi >= ROI_THRESHOLD_ALERT and self.bot:
                    msg = (f"🚨 *HIGH ROI ALERT*\n\n*Market:* {d.question}\n*Platform:* {d.source}\n"
                           f"*ROI:* +{d.roi:.1f}%  |  *Profit:* ${d.profit_usd:.2f}\n"
                           f"1. Buy {d.bet_type} @ {d.bet_price:.3f}\n"
                           f"2. {d.hedge_dir} @ ${d.parsed.current:,.2f} (x{d.leverage})")
                    try:
                        await self.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=msg, parse_mode='Markdown')
                    except Exception:
                        pass

if __name__ == "__main__":
    sys.stdout.reconfigure(line_buffering=True)
    try:
        asyncio.run(ArbitrageScanner().run())
    except Exception as e:
        print(f"\n❌ Fatal Execution Error: {e}")
        sys.exit(1)
