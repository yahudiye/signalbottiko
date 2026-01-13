import os
import time
import logging
from datetime import datetime
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from dotenv import load_dotenv
import asyncio

# TradingView TA
from tradingview_ta import TA_Handler, Interval

# Logging
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

# QUALITY SCALP CONFIG
SCAN_INTERVAL = 60
SIGNALS_TODAY = 0
MIN_SCORE = 75

# SCALP LEVELS
TP1_PCT = 0.6
TP2_PCT = 1.2
TP3_PCT = 2.0
SL_PCT = 0.5

# Duplicate filter
RECENT_SIGNALS = {}
DUPLICATE_COOLDOWN = 300

def get_tv(symbol, tf=Interval.INTERVAL_5_MINUTES):
    for ex in ["BINANCE", "BYBIT", "OKX"]:
        try:
            h = TA_Handler(symbol=f"{symbol}USDT", screener="crypto", exchange=ex, interval=tf)
            return h.get_analysis()
        except:
            continue
    return None

def scalp_analyze(symbol):
    """Quality Scalp Analysis - 1m/5m/15m with strict filters"""
    
    tf_1m = get_tv(symbol, Interval.INTERVAL_1_MINUTE)
    tf_5m = get_tv(symbol, Interval.INTERVAL_5_MINUTES)
    tf_15m = get_tv(symbol, Interval.INTERVAL_15_MINUTES)
    
    if not tf_1m or not tf_5m:
        return None
    
    # Recommendations
    rec_1m = tf_1m.summary.get('RECOMMENDATION', 'NEUTRAL')
    rec_5m = tf_5m.summary.get('RECOMMENDATION', 'NEUTRAL')
    rec_15m = tf_15m.summary.get('RECOMMENDATION', 'NEUTRAL') if tf_15m else 'NEUTRAL'
    
    buy_1m = tf_1m.summary.get('BUY', 0)
    sell_1m = tf_1m.summary.get('SELL', 0)
    buy_5m = tf_5m.summary.get('BUY', 0)
    sell_5m = tf_5m.summary.get('SELL', 0)
    
    # 5m indicators
    ind = tf_5m.indicators
    close = ind.get('close', 0)
    if not close:
        return None
    
    rsi = ind.get('RSI', 50)
    rsi_1m = tf_1m.indicators.get('RSI', 50)
    macd = ind.get('MACD.macd', 0)
    macd_sig = ind.get('MACD.signal', 0)
    stoch_k = ind.get('Stoch.K', 50)
    stoch_d = ind.get('Stoch.D', 50)
    ema_9 = ind.get('EMA10', 0)
    ema_21 = ind.get('EMA20', 0)
    mom = ind.get('Mom', 0)
    ao = ind.get('AO', 0)
    
    signals = []
    bull = 0
    bear = 0
    
    # 1. RSI momentum
    if rsi and rsi_1m:
        if 45 <= rsi <= 65 and rsi_1m > rsi:
            bull += 15
            signals.append(f"RSI Rising: {rsi:.0f}")
        elif 35 <= rsi <= 55 and rsi_1m < rsi:
            bear += 15
            signals.append(f"RSI Falling: {rsi:.0f}")
        elif rsi > 70 or rsi < 30:
            return None
    
    # 2. MACD cross
    if macd is not None and macd_sig is not None:
        diff = macd - macd_sig
        if 0 < diff < abs(macd) * 0.5:
            bull += 15
            signals.append("MACD: Bull Cross")
        elif -abs(macd) * 0.5 < diff < 0:
            bear += 15
            signals.append("MACD: Bear Cross")
    
    # 3. Stochastic
    if stoch_k and stoch_d:
        if 30 < stoch_k < 70:
            if stoch_k > stoch_d:
                bull += 15
                signals.append(f"Stoch K>D: {stoch_k:.0f}")
            else:
                bear += 15
                signals.append(f"Stoch K<D: {stoch_k:.0f}")
        else:
            return None
    
    # 4. EMA cross
    if ema_9 and ema_21 and close:
        diff = ((ema_9 - ema_21) / ema_21) * 100 if ema_21 else 0
        if 0 < diff < 0.3:
            bull += 15
            signals.append("EMA: Crossed Up")
        elif -0.3 < diff < 0:
            bear += 15
            signals.append("EMA: Crossed Down")
    
    # 5. Momentum
    if mom and ao:
        if mom > 0 and ao > 0:
            bull += 10
            signals.append("Momentum: +")
        elif mom < 0 and ao < 0:
            bear += 10
            signals.append("Momentum: -")
    
    # 6. TF alignment (1m + 5m REQUIRED)
    if rec_1m in ['STRONG_BUY', 'BUY'] and rec_5m in ['STRONG_BUY', 'BUY']:
        bull += 20
        signals.append(f"1m+5m: Bullish ({buy_1m+buy_5m})")
    elif rec_1m in ['STRONG_SELL', 'SELL'] and rec_5m in ['STRONG_SELL', 'SELL']:
        bear += 20
        signals.append(f"1m+5m: Bearish ({sell_1m+sell_5m})")
    else:
        return None
    
    # 7. 15m REQUIRED
    if rec_15m in ['STRONG_BUY', 'BUY'] and bull > bear:
        bull += 15
        signals.append("15m: Bullish")
    elif rec_15m in ['STRONG_SELL', 'SELL'] and bear > bull:
        bear += 15
        signals.append("15m: Bearish")
    else:
        return None
    
    # Direction
    if bull >= 70 and bull > bear:
        direction = "LONG"
        score = min(bull, 100)
    elif bear >= 70 and bear > bull:
        direction = "SHORT"
        score = min(bear, 100)
    else:
        return None
    
    # Score filter
    if score < MIN_SCORE:
        return None
    
    # Duplicate check
    now = time.time()
    if symbol in RECENT_SIGNALS:
        last = RECENT_SIGNALS[symbol]
        if last['dir'] == direction and (now - last['time']) < DUPLICATE_COOLDOWN:
            return None
    
    # Trade levels
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
    
    rr = abs(tp1 - entry) / abs(entry - sl) if abs(entry - sl) > 0 else 1
    
    RECENT_SIGNALS[symbol] = {'dir': direction, 'time': now}
    
    return {
        'symbol': f"{symbol}/USDT",
        'direction': direction,
        'score': score,
        'price': close,
        'entry': entry,
        'sl': sl,
        'tp1': tp1,
        'tp2': tp2,
        'tp3': tp3,
        'rr': rr,
        'rsi': rsi,
        'stoch': stoch_k,
        'rec_1m': rec_1m,
        'rec_5m': rec_5m,
        'rec_15m': rec_15m,
        'signals': signals
    }

def format_signal(s):
    e = "LONG" if s['direction'] == "LONG" else "SHORT"
    emoji = "GREEN" if e == "LONG" else "RED"
    lev = "15-20x" if s['score'] >= 85 else "10-15x"
    
    msg = f"""{'🟢' if e == 'LONG' else '🔴'} **{s['symbol']}** | {e}
━━━━━━━━━━━━━━━━━━━━
⚡ SCALP | Score: **{s['score']}/100**
🎯 Leverage: **{lev}**

🕐 **TFs:** 1m:{s['rec_1m']} | 5m:{s['rec_5m']} | 15m:{s['rec_15m']}

💰 **LEVELS:**
• Entry: ${s['entry']:.4f}
• SL: ${s['sl']:.4f} (-{SL_PCT}%)
• TP1: ${s['tp1']:.4f} (+{TP1_PCT}%) [{s['rr']:.1f}R]
• TP2: ${s['tp2']:.4f} (+{TP2_PCT}%)
• TP3: ${s['tp3']:.4f} (+{TP3_PCT}%)

📊 RSI:{s['rsi']:.0f} | Stoch:{s['stoch']:.0f}

📋 **CONFIRMATIONS:**
"""
    for sig in s['signals'][:6]:
        msg += f"✅ {sig}\n"
    
    msg += """━━━━━━━━━━━━━━━━━━━━
⚡ SCALP: Close at TP1/TP2!"""
    return msg

def run_scan():
    global RECENT_SIGNALS
    signals = []
    now = time.time()
    
    RECENT_SIGNALS = {k: v for k, v in RECENT_SIGNALS.items() if now - v['time'] < DUPLICATE_COOLDOWN}
    
    logger.info(f"⚡ Scanning {len(COINS)} coins...")
    
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
    
    logger.info("⚡ SCALP BOT starting...")
    await asyncio.sleep(3)
    
    if ADMIN_CHAT_ID:
        try:
            await app.bot.send_message(
                chat_id=int(ADMIN_CHAT_ID),
                text=f"⚡ **QUALITY SCALP BOT**\n"
                     f"━━━━━━━━━━━━━━━━━━━━\n"
                     f"📊 Coins: {len(COINS)}\n"
                     f"🕐 TFs: 1m+5m+15m\n"
                     f"🎯 TP: {TP1_PCT}%/{TP2_PCT}%/{TP3_PCT}%\n"
                     f"🛡️ SL: {SL_PCT}%\n"
                     f"⏱ Scan: {SCAN_INTERVAL}s\n"
                     f"━━━━━━━━━━━━━━━━━━━━"
            )
        except:
            pass
    
    while True:
        try:
            logger.info("⚡ SCANNING...")
            
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
        f"⚡ **QUALITY SCALP BOT**\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 EdgeX Coins: {len(COINS)}\n"
        f"🕐 TFs: 1m + 5m + 15m (all must align)\n"
        f"🎯 TP: {TP1_PCT}% / {TP2_PCT}% / {TP3_PCT}%\n"
        f"🛡️ SL: {SL_PCT}%\n"
        f"📈 Leverage: 10-20x\n\n"
        f"Chat ID: `{chat_id}`"
    )

async def scan_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⚡ Scalp scanning...")
    loop = asyncio.get_event_loop()
    signals = await loop.run_in_executor(None, run_scan)
    
    if signals:
        msg = f"⚡ **{len(signals)} SCALP SIGNALS**\n\n"
        for s in signals[:5]:
            e = "🟢" if s['direction'] == "LONG" else "🔴"
            msg += f"{e} {s['symbol']}: {s['direction']} ({s['score']})\n"
        await update.message.reply_text(msg)
        for sig in signals[:2]:
            await update.message.reply_text(format_signal(sig))
    else:
        await update.message.reply_text("❌ No quality scalp signals. All 3 TFs must align.")

async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"📊 **STATS**\n"
        f"Signals Today: {SIGNALS_TODAY}\n"
        f"Coins: {len(COINS)}\n"
        f"Cooldowns: {len(RECENT_SIGNALS)}"
    )

async def post_init(app):
    asyncio.create_task(background_scanner(app))

if __name__ == '__main__':
    print("⚡ QUALITY SCALP BOT")
    print(f"   TP: {TP1_PCT}%/{TP2_PCT}%/{TP3_PCT}%")
    print(f"   SL: {SL_PCT}%")
    
    test = get_tv("BTC", Interval.INTERVAL_5_MINUTES)
    if test:
        print(f"✅ BTC 5m: {test.summary.get('RECOMMENDATION')}")
    
    if not ADMIN_CHAT_ID:
        print("⚠️ ADMIN_CHAT_ID not set!")
    
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("scan", scan_cmd))
    app.add_handler(CommandHandler("stats", stats_cmd))
    
    print("🚀 Running...")
    app.run_polling()
