import os
import time
import logging
from datetime import datetime
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from dotenv import load_dotenv
import asyncio

from tradingview_ta import TA_Handler, Interval

logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")

# ALL EdgeX Pro Coins
COINS = [
    "BTC", "ETH", "SOL", "XRP", "BNB", "LTC", "BCH", "DOT", "LINK", "SUI",
    "AVAX", "APT", "NEAR", "TRX", "DOGE", "ATOM", "ETC", "XLM", "XMR", "ZEC",
    "AAVE", "UNI", "CRV", "LDO", "PENDLE", "JUP", "OP", "ARB", "ICP", "FIL",
    "PEPE", "BONK", "SHIB", "WIF", "TRUMP", "HYPE",
    "TON", "TAO", "SEI", "CAKE", "CFX", "CRO", "ONDO", "ENS", "ENA", "HBAR",
    "PYTH", "RAY", "ORDI", "WLD", "ALGO", "OKB", "MNT", "ZK", "GRASS"
]

# CONFIG
SCAN_INTERVAL = 60
SIGNALS_TODAY = 0
MAX_SIGNALS_DAY = 10  # Overtrade prevention
MIN_SCORE = 75

# SCALP LEVELS
TP1_PCT = 0.6
TP2_PCT = 1.2
TP3_PCT = 2.0
SL_PCT = 0.5

RECENT_SIGNALS = {}
DUPLICATE_COOLDOWN = 300

# BTC trend cache
BTC_RSI = 50

# DANGEROUS HOURS (UTC) - NY Open, London Open, Funding
DANGEROUS_HOURS = [12, 13, 20, 21, 0]  # Skip these hours

def get_tv(symbol, tf=Interval.INTERVAL_5_MINUTES):
    for ex in ["BINANCE", "BYBIT", "OKX"]:
        try:
            h = TA_Handler(symbol=f"{symbol}USDT", screener="crypto", exchange=ex, interval=tf)
            return h.get_analysis()
        except:
            continue
    return None

def get_btc_trend():
    global BTC_RSI
    try:
        btc = get_tv("BTC", Interval.INTERVAL_5_MINUTES)
        if btc:
            BTC_RSI = btc.indicators.get('RSI', 50)
    except:
        pass
    return BTC_RSI

def is_dangerous_hour():
    """Check if current hour is dangerous for scalping"""
    hour = datetime.utcnow().hour
    return hour in DANGEROUS_HOURS

def scalp_analyze(symbol):
    """
    SCALP BOT PRO v2 with ALL fixes:
    - EMA9/21 fix
    - ATR-based SL
    - BTC filter
    - Smart fake breakout filter
    - 15m flexible filter
    - Time filter
    - Daily limit
    """
    global SIGNALS_TODAY
    
    # Daily limit check
    if SIGNALS_TODAY >= MAX_SIGNALS_DAY:
        return None
    
    # Dangerous hour check
    if is_dangerous_hour():
        return None
    
    tf_1m = get_tv(symbol, Interval.INTERVAL_1_MINUTE)
    tf_5m = get_tv(symbol, Interval.INTERVAL_5_MINUTES)
    tf_15m = get_tv(symbol, Interval.INTERVAL_15_MINUTES)
    
    if not tf_1m or not tf_5m:
        return None
    
    # Recommendations (FILTER only)
    rec_1m = tf_1m.summary.get('RECOMMENDATION', 'NEUTRAL')
    rec_5m = tf_5m.summary.get('RECOMMENDATION', 'NEUTRAL')
    rec_15m = tf_15m.summary.get('RECOMMENDATION', 'NEUTRAL') if tf_15m else 'NEUTRAL'
    
    buy_1m = tf_1m.summary.get('BUY', 0)
    sell_1m = tf_1m.summary.get('SELL', 0)
    buy_5m = tf_5m.summary.get('BUY', 0)
    sell_5m = tf_5m.summary.get('SELL', 0)
    
    ind = tf_5m.indicators
    
    close = ind.get('close', 0)
    high = ind.get('high', 0)
    low = ind.get('low', 0)
    
    if not close:
        return None
    
    # CORRECT INDICATORS
    rsi = ind.get('RSI', 50)
    rsi_1m = tf_1m.indicators.get('RSI', 50)
    macd = ind.get('MACD.macd', 0)
    macd_sig = ind.get('MACD.signal', 0)
    stoch_k = ind.get('Stoch.K', 50)
    stoch_d = ind.get('Stoch.D', 50)
    ema_9 = ind.get('EMA9', ind.get('EMA10', 0))
    ema_21 = ind.get('EMA21', ind.get('EMA20', 0))
    atr = ind.get('ATR', 0)
    mom = ind.get('Mom', 0)
    ao = ind.get('AO', 0)
    
    signals = []
    bull = 0
    bear = 0
    
    btc_rsi = BTC_RSI
    
    # 1. RSI MOMENTUM
    if rsi and rsi_1m:
        rsi_mom = rsi_1m - rsi
        
        if 40 <= rsi <= 60:
            if rsi_mom > 2:
                bull += 20
                signals.append(f"RSI: {rsi:.0f} Rising")
            elif rsi_mom < -2:
                bear += 20
                signals.append(f"RSI: {rsi:.0f} Falling")
        elif rsi > 70 or rsi < 30:
            return None
        elif 60 < rsi <= 70 and rsi_mom > 0:
            bull += 10
        elif 30 <= rsi < 40 and rsi_mom < 0:
            bear += 10
    
    # 2. MACD FRESH CROSS
    if macd is not None and macd_sig is not None:
        macd_hist = macd - macd_sig
        if 0 < macd_hist < abs(macd) * 0.3:
            bull += 20
            signals.append("MACD: Fresh Bull")
        elif -abs(macd) * 0.3 < macd_hist < 0:
            bear += 20
            signals.append("MACD: Fresh Bear")
    
    # 3. STOCHASTIC
    if stoch_k and stoch_d:
        if stoch_k > 80 or stoch_k < 20:
            return None
        if stoch_k > stoch_d and 30 < stoch_k < 70:
            bull += 15
            signals.append(f"Stoch: {stoch_k:.0f}>{stoch_d:.0f}")
        elif stoch_k < stoch_d and 30 < stoch_k < 70:
            bear += 15
            signals.append(f"Stoch: {stoch_k:.0f}<{stoch_d:.0f}")
    
    # 4. EMA CROSS
    if ema_9 and ema_21 and close:
        ema_diff = ((ema_9 - ema_21) / ema_21) * 100 if ema_21 else 0
        if 0 < ema_diff < 0.2:
            bull += 15
            signals.append("EMA9>21 Cross")
        elif -0.2 < ema_diff < 0:
            bear += 15
            signals.append("EMA9<21 Cross")
        elif ema_diff > 0.5:
            bull += 5
        elif ema_diff < -0.5:
            bear += 5
    
    # 5. MOMENTUM
    if mom and ao:
        if mom > 0 and ao > 0:
            bull += 5
            signals.append("Mom: +")
        elif mom < 0 and ao < 0:
            bear += 5
            signals.append("Mom: -")
    elif mom:
        if mom > 0:
            bull += 3
        else:
            bear += 3
    
    # 6. TF ALIGNMENT
    if rec_1m in ['STRONG_BUY', 'BUY'] and rec_5m in ['STRONG_BUY', 'BUY']:
        if bull > bear:
            bull += 15
            signals.append(f"1m+5m: Bull ({buy_1m+buy_5m})")
    elif rec_1m in ['STRONG_SELL', 'SELL'] and rec_5m in ['STRONG_SELL', 'SELL']:
        if bear > bull:
            bear += 15
            signals.append(f"1m+5m: Bear ({sell_1m+sell_5m})")
    else:
        return None
    
    # Calculate preliminary score
    prelim_score = max(bull, bear)
    
    # 7. 15m FLEXIBLE (not always required)
    if rec_15m in ['STRONG_BUY', 'BUY'] and bull > bear:
        bull += 10
        signals.append("15m: Bull")
    elif rec_15m in ['STRONG_SELL', 'SELL'] and bear > bull:
        bear += 10
        signals.append("15m: Bear")
    elif prelim_score >= 85:
        # High score signals can bypass 15m
        signals.append("15m: Bypassed (High Score)")
    else:
        return None  # 15m required for lower scores
    
    # DIRECTION
    if bull >= 65 and bull > bear:
        direction = "LONG"
        score = min(bull, 100)
    elif bear >= 65 and bear > bull:
        direction = "SHORT"
        score = min(bear, 100)
    else:
        return None
    
    # BTC TREND FILTER
    if direction == "LONG" and btc_rsi < 45:
        return None
    if direction == "SHORT" and btc_rsi > 55:
        return None
    
    # SMART FAKE BREAKOUT FILTER
    if high and low and close:
        if direction == "LONG":
            if close >= high * 0.999 and score < 85:
                return None  # Too close to high for low score
        if direction == "SHORT":
            if close <= low * 1.001 and score < 85:
                return None  # Too close to low for low score
    
    # SCORE FILTER
    if score < MIN_SCORE:
        return None
    
    # DUPLICATE CHECK
    now = time.time()
    if symbol in RECENT_SIGNALS:
        last = RECENT_SIGNALS[symbol]
        if last['dir'] == direction and (now - last['time']) < DUPLICATE_COOLDOWN:
            return None
    
    # DYNAMIC SL/TP
    if atr and atr > 0:
        if direction == "LONG":
            entry = close
            sl = close - (atr * 0.6)
            tp1 = close + (atr * 0.8)
            tp2 = close + (atr * 1.5)
            tp3 = close + (atr * 2.5)
        else:
            entry = close
            sl = close + (atr * 0.6)
            tp1 = close - (atr * 0.8)
            tp2 = close - (atr * 1.5)
            tp3 = close - (atr * 2.5)
    else:
        if direction == "LONG":
            entry = close
            sl = close * (1 - SL_PCT / 100)
            tp1 = close * (1 + TP1_PCT / 100)
            tp2 = close * (1 + TP2_PCT / 100)
            tp3 = close * (1 + TP3_PCT / 100)
        else:
            entry = close
            sl = close * (1 + SL_PCT / 100)
            tp1 = close * (1 - TP1_PCT / 100)
            tp2 = close * (1 - TP2_PCT / 100)
            tp3 = close * (1 - TP3_PCT / 100)
    
    sl_pct = abs(entry - sl) / entry * 100
    tp1_pct = abs(tp1 - entry) / entry * 100
    
    risk = abs(entry - sl)
    rr1 = abs(tp1 - entry) / risk if risk > 0 else 1
    rr2 = abs(tp2 - entry) / risk if risk > 0 else 1
    
    RECENT_SIGNALS[symbol] = {'dir': direction, 'time': now}
    
    return {
        'symbol': f"{symbol}/USDT",
        'direction': direction,
        'score': score,
        'price': close,
        'entry': entry,
        'sl': sl,
        'sl_pct': sl_pct,
        'tp1': tp1,
        'tp1_pct': tp1_pct,
        'tp2': tp2,
        'tp3': tp3,
        'rr1': rr1,
        'rr2': rr2,
        'rsi': rsi,
        'stoch': stoch_k,
        'btc_rsi': btc_rsi,
        'atr': atr if atr else 0,
        'rec_1m': rec_1m,
        'rec_5m': rec_5m,
        'rec_15m': rec_15m,
        'signals': signals
    }

def format_signal(s):
    e = "🟢" if s['direction'] == "LONG" else "🔴"
    lev = "15-20x" if s['score'] >= 85 else "10-15x"
    
    msg = f"""{e} **{s['symbol']}** | {s['direction']}
━━━━━━━━━━━━━━━━━━━━
⚡ Score: **{s['score']}/100** | Lev: **{lev}**
📊 BTC RSI: {s['btc_rsi']:.0f}

🕐 1m:{s['rec_1m']} | 5m:{s['rec_5m']} | 15m:{s['rec_15m']}

💰 **LEVELS (ATR):**
• Entry: ${s['entry']:.4f}
• SL: ${s['sl']:.4f} (-{s['sl_pct']:.2f}%)
• TP1: ${s['tp1']:.4f} (+{s['tp1_pct']:.2f}%) [{s['rr1']:.1f}R]
• TP2: ${s['tp2']:.4f} [{s['rr2']:.1f}R]
• TP3: ${s['tp3']:.4f}

📋 **CONFIRMATIONS:**
"""
    for sig in s['signals'][:5]:
        msg += f"✅ {sig}\n"
    
    msg += f"""
🧠 **TRADE MANAGEMENT:**
• TP1 → Close 40% ⚠️ MUTLAKA AL
• SL → Move to BE after TP1
• TP2 → Trail stop (50%)
• TP3 → Moon bag (10%)

⚠️ TP1 scalp hedefidir, atlama!
━━━━━━━━━━━━━━━━━━━━"""
    return msg

def run_scan():
    global RECENT_SIGNALS, SIGNALS_TODAY
    signals = []
    now = time.time()
    
    get_btc_trend()
    
    # Reset daily counter at midnight UTC
    current_hour = datetime.utcnow().hour
    if current_hour == 0:
        SIGNALS_TODAY = 0
    
    RECENT_SIGNALS = {k: v for k, v in RECENT_SIGNALS.items() if now - v['time'] < DUPLICATE_COOLDOWN}
    
    # Check dangerous hours
    if is_dangerous_hour():
        logger.info(f"⚠️ Dangerous hour (UTC {current_hour}), skipping scan")
        return []
    
    logger.info(f"⚡ Scanning {len(COINS)} coins (BTC:{BTC_RSI:.0f}, Signals:{SIGNALS_TODAY}/{MAX_SIGNALS_DAY})...")
    
    for coin in COINS:
        try:
            r = scalp_analyze(coin)
            if r:
                signals.append(r)
                logger.info(f"✅ {coin} {r['direction']} Score:{r['score']}")
            time.sleep(0.1)
        except Exception as e:
            logger.error(f"Err {coin}: {str(e)[:20]}")
    
    logger.info(f"Found {len(signals)} signals")
    return sorted(signals, key=lambda x: x['score'], reverse=True)

async def background_scanner(app):
    global SIGNALS_TODAY
    
    logger.info("⚡ SCALP BOT PRO v2 starting...")
    await asyncio.sleep(3)
    
    if ADMIN_CHAT_ID:
        try:
            await app.bot.send_message(
                chat_id=int(ADMIN_CHAT_ID),
                text=f"⚡ **SCALP BOT PRO v2**\n"
                     f"━━━━━━━━━━━━━━━━━━━━\n"
                     f"✅ EMA9/21 Fixed\n"
                     f"✅ ATR-based SL/TP\n"
                     f"✅ BTC Trend Filter\n"
                     f"✅ Smart Breakout Filter\n"
                     f"✅ 15m Flexible Filter\n"
                     f"✅ NY/London Hour Skip\n"
                     f"✅ Max {MAX_SIGNALS_DAY} signals/day\n"
                     f"━━━━━━━━━━━━━━━━━━━━"
            )
        except:
            pass
    
    while True:
        try:
            loop = asyncio.get_event_loop()
            signals = await loop.run_in_executor(None, run_scan)
            
            if signals and ADMIN_CHAT_ID:
                for sig in signals[:2]:
                    SIGNALS_TODAY += 1
                    await app.bot.send_message(chat_id=int(ADMIN_CHAT_ID), text=format_signal(sig))
                    await asyncio.sleep(0.5)
            
            await asyncio.sleep(SCAN_INTERVAL)
            
        except Exception as e:
            logger.error(f"Error: {e}")
            await asyncio.sleep(20)

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_message.chat_id
    await update.message.reply_text(
        f"⚡ **SCALP BOT PRO v2**\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"✅ All fixes applied\n"
        f"✅ Max {MAX_SIGNALS_DAY} signals/day\n"
        f"✅ Skip NY/London open\n\n"
        f"Chat ID: `{chat_id}`"
    )

async def scan_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⚡ Pro scanning...")
    loop = asyncio.get_event_loop()
    signals = await loop.run_in_executor(None, run_scan)
    
    if signals:
        msg = f"⚡ **{len(signals)} SIGNALS** (BTC:{BTC_RSI:.0f})\n\n"
        for s in signals[:5]:
            e = "🟢" if s['direction'] == "LONG" else "🔴"
            msg += f"{e} {s['symbol']}: {s['direction']} ({s['score']})\n"
        await update.message.reply_text(msg)
        for sig in signals[:2]:
            await update.message.reply_text(format_signal(sig))
    else:
        hour = datetime.utcnow().hour
        if is_dangerous_hour():
            await update.message.reply_text(f"⚠️ Dangerous hour (UTC {hour}). Waiting...")
        else:
            await update.message.reply_text(f"❌ No signals (BTC:{BTC_RSI:.0f})")

async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    hour = datetime.utcnow().hour
    await update.message.reply_text(
        f"📊 **STATS**\n"
        f"Signals: {SIGNALS_TODAY}/{MAX_SIGNALS_DAY}\n"
        f"BTC RSI: {BTC_RSI:.0f}\n"
        f"Hour (UTC): {hour}\n"
        f"Dangerous: {'YES' if is_dangerous_hour() else 'NO'}"
    )

async def post_init(app):
    asyncio.create_task(background_scanner(app))

if __name__ == '__main__':
    print("⚡ SCALP BOT PRO v2")
    
    test = get_tv("BTC", Interval.INTERVAL_5_MINUTES)
    if test:
        print(f"✅ BTC: {test.summary.get('RECOMMENDATION')}")
    
    if not ADMIN_CHAT_ID:
        print("⚠️ ADMIN_CHAT_ID not set!")
    
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("scan", scan_cmd))
    app.add_handler(CommandHandler("stats", stats_cmd))
    
    print("🚀 Running...")
    app.run_polling()
