""" CRYPTO ARBITRAGE TERMINAL v36.1 — Fast """
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
MAX_CONCURRENT          = 25
BINANCE_FEE_RATE        = 0.0005
PREDICT_DEFAULT_FEE_BPS = 200
POLYMARKET_CRYPTO_FEE   = 0.07

load_dotenv()
PREDICT_API_KEY    = os.getenv("PREDICT_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")

ASSETS_MAP = {
    "BTC": "BTCUSDT", "Bitcoin": "BTCUSDT", "$BTC": "BTCUSDT",
    "ETH": "ETHUSDT", "Ethereum": "ETHUSDT", "$ETH": "ETHUSDT", "Ether": "ETHUSDT",
    "SOL": "SOLUSDT", "Solana": "SOLUSDT", "$SOL": "SOLUSDT",
    "BNB": "BNBUSDT", "Binance Coin": "BNBUSDT",
    "XRP": "XRPUSDT", "Ripple": "XRPUSDT",
    "DOGE": "DOGEUSDT", "DOGECOIN": "DOGEUSDT",
    "ADA": "ADAUSDT", "Cardano": "ADAUSDT",
    "AVAX": "AVAXUSDT", "Avalanche": "AVAXUSDT",
    "LINK": "LINKUSDT", "Chainlink": "LINKUSDT",
    "LTC": "LTCUSDT", "Litecoin": "LTCUSDT",
    "DOT": "DOTUSDT", "Polkadot": "DOTUSDT",
    "TRX": "TRXUSDT", "SHIB": "SHIBUSDT",
    "GOLD": "XAUUSDT", "XAU": "XAUUSDT", "Gold": "XAUUSDT",
    "SILVER": "XAGUSDT", "XAG": "XAGUSDT", "Silver": "XAGUSDT",
    "XMR": "XMRUSDT", "Monero": "XMRUSDT",
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
            "User-Agent": "ArbitrageBot/36.1",
        }
        print(f"\n{'='*65}")
        print(f"  ARBITRAGE TERMINAL v36.1 | Budget: ${BET_AMOUNT:.0f}")
        print(f"  Polymarket + Predict.fun | Strategy 5")
        print(f"{'='*65}\n")

    # ─── PRICES ───────────────────────────────────────────────────────────────
    async def fetch_prices(self, session: aiohttp.ClientSession):
        print("  [1/4] Fetching prices...")

        # 1. Binance Futures / Spot
        for url, label in [
            ("https://fapi.binance.com/fapi/v1/ticker/price", "Binance Futures"),
            ("https://api.binance.com/api/v3/ticker/price",   "Binance Spot"),
        ]:
            try:
                async with session.get(url) as r:
                    print(f"       {label}: HTTP {r.status}")
                    if r.status == 200:
                        data = await r.json(content_type=None)
                        self.prices = {x['symbol']: float(x['price']) for x in data}
                        print(f"       ✅ {label}: {len(self.prices)} pairs")
                        return
            except Exception as e:
                print(f"       ❌ {label}: {e}")
                continue

        # 2. CoinGecko (EU-friendly, без API ключа)
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
                print(f"       CoinGecko: HTTP {r.status}")
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
                    for gecko_id, symbol in GECKO_MAP.items():
                        if gecko_id in data and 'usd' in data[gecko_id]:
                            self.prices[symbol] = float(data[gecko_id]['usd'])
                    if self.prices:
                        print(f"       ✅ CoinGecko: {len(self.prices)} pairs")
                        return
        except Exception as e:
            print(f"       ❌ CoinGecko: {e}")

        # 3. CryptoCompare
        try:
            fsyms = ",".join(["BTC","ETH","SOL","BNB","XRP","DOGE","ADA",
                              "AVAX","LINK","LTC","DOT","TRX","XMR","NEAR",
                              "ATOM","SUI","APT","TON","ARB","OP","BCH",
                              "UNI","AAVE","MATIC","XLM","HBAR","ETC","FIL","INJ"])
            async with session.get(
                f"https://min-api.cryptocompare.com/data/pricemulti?fsyms={fsyms}&tsyms=USD"
            ) as r:
                print(f"       CryptoCompare: HTTP {r.status}")
                if r.status == 200:
                    data = await r.json(content_type=None)
                    for coin, vals in data.items():
                        self.prices[f"{coin}USDT"] = float(vals['USD'])
                    if self.prices:
                        print(f"       ✅ CryptoCompare: {len(self.prices)} pairs")
                        return
        except Exception as e:
            print(f"       ❌ CryptoCompare: {e}")

        # 4. Kraken (MiCA-licensed, EU-compliant)
        try:
            KRAKEN_MAP = {
                "XXBTZUSD": "BTCUSDT", "XETHZUSD": "ETHUSDT", "SOLUSD": "SOLUSDT",
                "XRPUSD": "XRPUSDT", "DOGEUSD": "DOGEUSDT", "ADAUSD": "ADAUSDT",
                "AVAXUSD": "AVAXUSDT", "LINKUSD": "LINKUSDT", "LTCUSD": "LTCUSDT",
                "DOTUSD": "DOTUSDT", "XMRUSD": "XMRUSDT", "ATOMUSD": "ATOMUSDT",
                "NEARUSD": "NEARUSDT", "UNIUSD": "UNIUSDT", "MATICUSD": "MATICUSDT",
            }
            pairs = ",".join(KRAKEN_MAP.keys())
            async with session.get(f"https://api.kraken.com/0/public/Ticker?pair={pairs}") as r:
                print(f"       Kraken: HTTP {r.status}")
                if r.status == 200:
                    data = await r.json(content_type=None)
                    result = data.get('result', {})
                    for kraken_sym, our_sym in KRAKEN_MAP.items():
                        if kraken_sym in result:
                            self.prices[our_sym] = float(result[kraken_sym]['c'][0])
                    if self.prices:
                        print(f"       ✅ Kraken: {len(self.prices)} pairs")
                        return
        except Exception as e:
            print(f"       ❌ Kraken: {e}")

        print("       ❌ All price sources failed!")

    # ─── PARSING ──────────────────────────────────────────────────────────────
    def parse_market(self, question: str) -> Optional[MarketParsed]:
        if not question:
            return None
        q = question.lower()
        if not ((" or " in q or " before " in q) and
                any(x in q for x in ("hit", "reach", "touch", "first"))):
            return None
        if "above" in q or "below" in q:
            return None

        ticker = asset = None
        for name in sorted(ASSETS_MAP, key=len, reverse=True):
            if re.search(r'(?<![a-z])' + re.escape(name.lower()) + r'(?![a-z])', q):
                tk = ASSETS_MAP[name]
                if tk in self.prices:
                    ticker, asset = tk, name
                    break
        if not ticker:
            return None

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

        if len(nums) < 2:
            return None

        return MarketParsed(ticker=ticker, asset=asset,
                            target_low=min(nums), target_high=max(nums), current=cur)

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

    # ─── ORDERBOOKS ───────────────────────────────────────────────────────────
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
                    if asks:
                        return float(min(asks, key=lambda x: float(x['price']))['price'])
        except Exception:
            pass
        return None

    async def _pred_prices(self, session, market_id: str, question: str):
        try:
            async with session.get(
                f"https://api.predict.fun/v1/markets/{market_id}/orderbook",
                headers=self._pred_headers
            ) as r:
                if r.status != 200:
                    return None, None
                data = await r.json(content_type=None)
                ob   = data.get('data', data)
                asks, bids = ob.get('asks', []), ob.get('bids', [])
                if not asks or not bids:
                    return None, None
                p_yes = float(min(asks, key=lambda x: float(x[0]))[0])
                p_no  = 1.0 - float(max(bids, key=lambda x: float(x[0]))[0])
                if self._yes_is_low(question):
                    return p_yes, p_no
                return p_no, p_yes
        except Exception:
            return None, None

    # ─── ANALYZE ──────────────────────────────────────────────────────────────
    async def analyze(self, session, m: dict, parsed: MarketParsed) -> list:
        src = m['source']
        q   = m.get('title') or m.get('question') or ''
        cur, t_low, t_high = parsed.current, parsed.target_low, parsed.target_high

        if cur <= t_low or cur >= t_high:
            return []

        p_low = p_high = None

        if src == 'Polymarket':
            tokens = m.get('clobTokenIds', [])
            if len(tokens) >= 2:
                p_yes, p_no = await asyncio.gather(
                    self._poly_ask(session, tokens[0]),
                    self._poly_ask(session, tokens[1]),
                )
                if p_yes and p_no:
                    if self._yes_is_low(q):
                        p_low, p_high = p_yes, p_no
                    else:
                        p_low, p_high = p_no, p_yes

        elif src == 'Predict.fun':
            p_low, p_high = await self._pred_prices(session, m['id'], q)

        deals = []
        for price, btype in ((p_low, "LOW"), (p_high, "HIGH")):
            if price:
                d = self.calc_deal(price, btype, parsed, src, q)
                if d:
                    deals.append(d)
        return deals

    # ─── FETCH MARKETS ────────────────────────────────────────────────────────
    async def fetch_polymarket(self, session) -> list:
        SLUGS = [
            "will-solana-hit-60-or-140-first",
            "will-ethereum-hit-1k-or-3k-first",
        ]
        results = await asyncio.gather(
            *[self._poly_slug(session, s) for s in SLUGS],
            return_exceptions=True
        )
        markets = [r for r in results if isinstance(r, dict)]
        print(f"       Polymarket: {len(markets)} markets")
        return markets

    async def _poly_slug(self, session, slug: str) -> Optional[dict]:
        try:
            async with session.get(f"https://gamma-api.polymarket.com/markets/slug/{slug}") as r:
                if r.status == 200:
                    data   = await r.json(content_type=None)
                    m      = data[0] if isinstance(data, list) and data else data
                    raw    = m.get('clobTokenIds')
                    tokens = json.loads(raw) if isinstance(raw, str) else raw
                    if tokens and len(tokens) >= 2:
                        m['source'] = 'Polymarket'
                        m['clobTokenIds'] = tokens
                        return m
        except Exception:
            pass
        return None

    async def fetch_predictfun(self, session) -> list:
        IGNORE     = {'RESOLVED', 'CLOSED', 'CANCELLED'}
        asset_keys = {k.lower() for k in ASSETS_MAP}
        markets    = []
        cursor     = None

        while len(markets) < 2000:
            try:
                params = {"limit": 200}
                if cursor:
                    params["after"] = cursor
                async with session.get(
                    "https://api.predict.fun/v1/markets",
                    headers=self._pred_headers,
                    params=params,
                ) as r:
                    if r.status != 200:
                        break
                    data  = await r.json(content_type=None)
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
                    if not cursor:
                        break
            except Exception as e:
                print(f"       Predict.fun error: {e}")
                break

        print(f"       Predict.fun: {len(markets)} markets")
        return markets

    # ─── MAIN ─────────────────────────────────────────────────────────────────
    async def run(self):
        timeout = aiohttp.ClientTimeout(total=5, connect=2)
        conn    = aiohttp.TCPConnector(limit=MAX_CONCURRENT, ttl_dns_cache=300,
                                       enable_cleanup_closed=True)

        async with aiohttp.ClientSession(connector=conn, timeout=timeout) as session:
            await self.fetch_prices(session)
            if not self.prices:
                print("❌ No prices — aborting.")
                return

            print("  [2/4] Fetching markets...")
            poly_m, pred_m = await asyncio.gather(
                self.fetch_polymarket(session),
                self.fetch_predictfun(session),
            )
            all_markets = poly_m + pred_m
            print(f"       Total: {len(all_markets)}\n")

            print("  [3/4] Analyzing...")

            parseable = []
            for m in all_markets:
                q = m.get('title') or m.get('question') or ''
                p = self.parse_market(q)
                if p:
                    parseable.append((m, p))

            print(f"       Parseable: {len(parseable)}/{len(all_markets)}")

            sem = asyncio.Semaphore(MAX_CONCURRENT)
            all_deals = []
            done      = 0
            total     = len(parseable)

            async def safe(m, p):
                async with sem:
                    try:
                        return await self.analyze(session, m, p)
                    except Exception:
                        return []

            for coro in asyncio.as_completed([safe(m, p) for m, p in parseable]):
                deals = await coro
                all_deals.extend(deals)
                done += 1
                if done % 25 == 0 or done == total:
                    sys.stdout.write(f"\r       {done}/{total}")
                    sys.stdout.flush()

            print(f"\n  [4/4] {len(all_deals)} deals found\n")

        alerts = self.print_results(all_deals)
        if alerts and self.bot:
            for alert in alerts:
                await self.send_telegram(alert)
            print(f"\n📱 {len(alerts)} Telegram alerts sent")

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

        for d in deals[:15]:
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
