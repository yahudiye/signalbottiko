import os
import time
import logging
import requests
import json
from datetime import datetime
import pandas as pd
import numpy as np
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from dotenv import load_dotenv
import asyncio

# Logging
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")  # Your chat ID for auto notifications

# API
API_BASE = "https://min-api.cryptocompare.com"

# Coins
COINS = [
    "BTC", "ETH", "SOL", "XRP", "BNB", "ADA", "LTC", "DOT", "LINK", "SUI",
    "AVAX", "APT", "NEAR", "TRX", "DOGE", "PEPE", "BONK", "SHIB", "WIF",
    "AAVE", "UNI", "SNX", "CAKE", "ONDO", "PENDLE", "JUP", "ARB", "OP",
    "STRK", "TAO", "SEI", "MKR", "XMR", "ZEC"
]

# ============================================
# STRICT CONFIGURATION (Reduced stops)
# ============================================
MIN_SCORE = 85              # Higher threshold (was 80)
MIN_ADX = 35                # Stronger trend (was 30)
RSI_OVERSOLD = 25           # Stricter (was 30)
RSI_OVERBOUGHT = 75         # Stricter (was 70)
MIN_CONFLUENCE = 5          # All 5 must align (was 4)
ATR_SL_MULTIPLIER = 2.5     # Wider SL (was 2.0)
ATR_TP_MULTIPLIER = 3.5     # Better R:R (was 3.0)
SCAN_INTERVAL = 300         # 5 minutes (was 10)

# State
SIGNALS_TODAY = 0
SIGNAL_CACHE = {}
CACHE_DURATION = 1800

# ============================================
# INDICATORS
# ============================================

def ema(series, period):
    return series.ewm(span=period, adjust=False).mean()

def sma(series, period):
    return series.rolling(window=period).mean()

def rsi(series, period=14):
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / (loss + 0.0001)
    return 100 - (100 / (1 + rs))

def macd(series, fast=12, slow=26, signal=9):
    ema_fast = ema(series, fast)
    ema_slow = ema(series, slow)
    macd_line = ema_fast - ema_slow
    signal_line = ema(macd_line, signal)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram

def atr(high, low, close, period=14):
    tr1 = high - low
    tr2 = abs(high - close.shift(1))
    tr3 = abs(low - close.shift(1))
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.rolling(window=period).mean()

def adx(high, low, close, period=14):
    plus_dm = high.diff()
    minus_dm = -low.diff()
    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0)
    
    tr = atr(high, low, close, 1)
    atr_val = tr.rolling(window=period).mean()
    
    plus_di = 100 * (plus_dm.rolling(window=period).mean() / (atr_val + 0.0001))
    minus_di = 100 * (minus_dm.rolling(window=period).mean() / (atr_val + 0.0001))
    
    dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di + 0.0001)
    adx_val = dx.rolling(window=period).mean()
    
    return adx_val, plus_di, minus_di

def stochastic(high, low, close, k_period=14, d_period=3):
    lowest_low = low.rolling(window=k_period).min()
    highest_high = high.rolling(window=k_period).max()
    k = 100 * (close - lowest_low) / (highest_high - lowest_low + 0.0001)
    d = k.rolling(window=d_period).mean()
    return k, d

def find_support_resistance(df, lookback=50):
    highs = df['high'].tail(lookback)
    lows = df['low'].tail(lookback)
    
    swing_highs = []
    swing_lows = []
    
    for i in range(2, len(highs) - 2):
        if highs.iloc[i] > highs.iloc[i-1] and highs.iloc[i] > highs.iloc[i-2] and \
           highs.iloc[i] > highs.iloc[i+1] and highs.iloc[i] > highs.iloc[i+2]:
            swing_highs.append(highs.iloc[i])
        
        if lows.iloc[i] < lows.iloc[i-1] and lows.iloc[i] < lows.iloc[i-2] and \
           lows.iloc[i] < lows.iloc[i+1] and lows.iloc[i] < lows.iloc[i+2]:
            swing_lows.append(lows.iloc[i])
    
    resistance = max(swing_highs) if swing_highs else df['high'].tail(20).max()
    support = min(swing_lows) if swing_lows else df['low'].tail(20).min()
    
    return support, resistance

def check_higher_timeframe(symbol):
    """Check 1H AND 4H trend"""
    trends = []
    
    for tf, agg in [("1H", "histohour"), ("4H", "histohour")]:
        limit = 50 if tf == "1H" else 50
        url = f"{API_BASE}/data/v2/{agg}?fsym={symbol}&tsym=USDT&limit={limit}"
        if tf == "4H":
            url = f"{API_BASE}/data/v2/histohour?fsym={symbol}&tsym=USDT&limit=200&aggregate=4"
        
        try:
            resp = requests.get(url, timeout=10)
            data = resp.json()
            if data.get('Response') == 'Success':
                ohlcv = data['Data']['Data']
                df = pd.DataFrame(ohlcv)
                df['close'] = df['close'].astype(float)
                
                ema_20 = ema(df['close'], 20).iloc[-1]
                ema_50 = ema(df['close'], 50).iloc[-1]
                price = df['close'].iloc[-1]
                
                if price > ema_20 > ema_50:
                    trends.append("BULL")
                elif price < ema_20 < ema_50:
                    trends.append("BEAR")
                else:
                    trends.append("NEUTRAL")
        except:
            trends.append("NEUTRAL")
    
    # Both must align
    if trends[0] == "BULL" and trends[1] == "BULL":
        return "BULLISH"
    elif trends[0] == "BEAR" and trends[1] == "BEAR":
        return "BEARISH"
    return "NEUTRAL"

def market_structure(df):
    highs = df['high'].tail(50)
    lows = df['low'].tail(50)
    
    swing_highs = []
    swing_lows = []
    
    for i in range(5, len(highs) - 5):
        is_high = True
        is_low = True
        for j in range(1, 6):
            if highs.iloc[i] <= highs.iloc[i-j] or highs.iloc[i] <= highs.iloc[i+j]:
                is_high = False
            if lows.iloc[i] >= lows.iloc[i-j] or lows.iloc[i] >= lows.iloc[i+j]:
                is_low = False
        if is_high:
            swing_highs.append(highs.iloc[i])
        if is_low:
            swing_lows.append(lows.iloc[i])
    
    if len(swing_highs) >= 2 and len(swing_lows) >= 2:
        hh = swing_highs[-1] > swing_highs[-2]
        hl = swing_lows[-1] > swing_lows[-2]
        lh = swing_highs[-1] < swing_highs[-2]
        ll = swing_lows[-1] < swing_lows[-2]
        
        if hh and hl:
            return "BULLISH", 100
        elif lh and ll:
            return "BEARISH", 100
    
    return "NEUTRAL", 0

def momentum_check(df):
    close = df['close']
    roc_5 = (close.iloc[-1] / close.iloc[-5] - 1) * 100
    roc_10 = (close.iloc[-1] / close.iloc[-10] - 1) * 100
    roc_20 = (close.iloc[-1] / close.iloc[-20] - 1) * 100
    
    # All 3 must align
    if roc_5 > 0 and roc_10 > 0 and roc_20 > 0:
        return "BULLISH", abs(roc_5)
    elif roc_5 < 0 and roc_10 < 0 and roc_20 < 0:
        return "BEARISH", abs(roc_5)
    return "NEUTRAL", 0

def volume_spike(df):
    vol_sma = sma(df['volume'], 20)
    current = df['volume'].iloc[-1]
    avg = vol_sma.iloc[-1]
    
    if pd.isna(avg) or avg == 0:
        return False
    return current > avg * 1.5

# ============================================
# DATA
# ============================================

def fetch_data(symbol, limit=200):
    url = f"{API_BASE}/data/v2/histominute?fsym={symbol}&tsym=USDT&limit={limit}&aggregate=15"
    try:
        resp = requests.get(url, timeout=15)
        data = resp.json()
        if data.get('Response') == 'Success':
            ohlcv = data['Data']['Data']
            df = pd.DataFrame(ohlcv)
            df['open'] = df['open'].astype(float)
            df['high'] = df['high'].astype(float)
            df['low'] = df['low'].astype(float)
            df['close'] = df['close'].astype(float)
            df['volume'] = df['volumeto'].astype(float)
            return df
    except Exception as e:
        logger.error(f"Fetch error {symbol}: {e}")
    return None

# ============================================
# ULTRA-STRICT ANALYSIS
# ============================================

def analyze_coin(symbol):
    df = fetch_data(symbol, 200)
    if df is None or len(df) < 100:
        return None
    
    close = df['close']
    high = df['high']
    low = df['low']
    
    ema_9 = ema(close, 9)
    ema_21 = ema(close, 21)
    ema_50 = ema(close, 50)
    
    rsi_val = rsi(close, 14)
    macd_line, signal_line, macd_hist = macd(close)
    atr_val = atr(high, low, close, 14)
    adx_val, plus_di, minus_di = adx(high, low, close, 14)
    stoch_k, stoch_d = stochastic(high, low, close)
    
    support, resistance = find_support_resistance(df)
    
    last = {
        'price': close.iloc[-1],
        'ema_9': ema_9.iloc[-1],
        'ema_21': ema_21.iloc[-1],
        'ema_50': ema_50.iloc[-1],
        'rsi': rsi_val.iloc[-1],
        'macd': macd_line.iloc[-1],
        'macd_signal': signal_line.iloc[-1],
        'macd_hist': macd_hist.iloc[-1],
        'atr': atr_val.iloc[-1],
        'adx': adx_val.iloc[-1],
        'plus_di': plus_di.iloc[-1],
        'minus_di': minus_di.iloc[-1],
        'stoch_k': stoch_k.iloc[-1],
        'support': support,
        'resistance': resistance
    }
    
    if any(pd.isna(v) for v in last.values()):
        return None
    
    # ULTRA-STRICT: ADX must be very strong
    if last['adx'] < MIN_ADX:
        return None
    
    # Check multi-timeframe trend (1H + 4H)
    htf_trend = check_higher_timeframe(symbol)
    if htf_trend == "NEUTRAL":
        return None  # Must have clear HTF trend
    
    structure, struct_conf = market_structure(df)
    momentum, mom_strength = momentum_check(df)
    has_volume = volume_spike(df)
    
    ema_bullish = last['ema_9'] > last['ema_21'] > last['ema_50']
    ema_bearish = last['ema_9'] < last['ema_21'] < last['ema_50']
    
    adx_bullish = last['plus_di'] > last['minus_di']
    adx_bearish = last['minus_di'] > last['plus_di']
    
    macd_bullish = last['macd'] > last['macd_signal'] and last['macd_hist'] > 0
    macd_bearish = last['macd'] < last['macd_signal'] and last['macd_hist'] < 0
    
    signals = []
    bullish_count = 0
    bearish_count = 0
    
    # 1. Structure (must be clear)
    if structure == "BULLISH":
        bullish_count += 1
        signals.append("📈 Structure: HH/HL")
    elif structure == "BEARISH":
        bearish_count += 1
        signals.append("📉 Structure: LH/LL")
    else:
        return None  # No clear structure = no trade
    
    # 2. EMA Stack
    if ema_bullish:
        bullish_count += 1
        signals.append("✅ EMA: Bullish")
    elif ema_bearish:
        bearish_count += 1
        signals.append("✅ EMA: Bearish")
    else:
        return None  # No clear EMA = no trade
    
    # 3. ADX
    if adx_bullish:
        bullish_count += 1
        signals.append(f"💪 ADX: {last['adx']:.0f}")
    elif adx_bearish:
        bearish_count += 1
        signals.append(f"💪 ADX: {last['adx']:.0f}")
    
    # 4. MACD
    if macd_bullish:
        bullish_count += 1
        signals.append("✅ MACD: Bullish")
    elif macd_bearish:
        bearish_count += 1
        signals.append("✅ MACD: Bearish")
    else:
        return None  # No MACD confirmation = no trade
    
    # 5. Momentum (all 3 ROC must align)
    if momentum == "BULLISH":
        bullish_count += 1
        signals.append("🚀 Momentum: Strong")
    elif momentum == "BEARISH":
        bearish_count += 1
        signals.append("📉 Momentum: Strong")
    else:
        return None  # Mixed momentum = no trade
    
    # HTF must align
    htf_aligned = False
    if htf_trend == "BULLISH" and bullish_count >= 5:
        htf_aligned = True
        signals.append("🕐 1H+4H: BULLISH ✓")
    elif htf_trend == "BEARISH" and bearish_count >= 5:
        htf_aligned = True
        signals.append("🕐 1H+4H: BEARISH ✓")
    
    if not htf_aligned:
        return None
    
    # ALL 5 must align
    if bullish_count < MIN_CONFLUENCE and bearish_count < MIN_CONFLUENCE:
        return None
    
    # Determine direction
    if bullish_count >= MIN_CONFLUENCE:
        direction = "LONG"
        confluence = bullish_count
    elif bearish_count >= MIN_CONFLUENCE:
        direction = "SHORT"
        confluence = bearish_count
    else:
        return None
    
    # Strict RSI check
    if direction == "SHORT" and last['rsi'] < RSI_OVERSOLD:
        return None
    if direction == "LONG" and last['rsi'] > RSI_OVERBOUGHT:
        return None
    
    # Stochastic extreme check
    if direction == "SHORT" and last['stoch_k'] < 20:
        return None
    if direction == "LONG" and last['stoch_k'] > 80:
        return None
    
    # Volume must confirm
    if not has_volume:
        return None
    signals.append("📊 Volume: Confirmed")
    
    price = last['price']
    
    # Calculate score
    score = 85  # Base for full confluence
    if last['adx'] > 45:
        score += 5
    if has_volume:
        score += 5
    if struct_conf == 100:
        score += 5
    
    score = min(score, 100)
    
    if score < MIN_SCORE:
        return None
    
    # Trade levels with wider SL
    atr_value = last['atr']
    
    if direction == "LONG":
        entry = price
        sl = max(price - (atr_value * ATR_SL_MULTIPLIER), support * 0.99)
        tp1 = price + (atr_value * ATR_TP_MULTIPLIER)
        tp2 = price + (atr_value * 5)
        tp3 = price + (atr_value * 7)
    else:
        entry = price
        sl = min(price + (atr_value * ATR_SL_MULTIPLIER), resistance * 1.01)
        tp1 = price - (atr_value * ATR_TP_MULTIPLIER)
        tp2 = price - (atr_value * 5)
        tp3 = price - (atr_value * 7)
    
    risk = abs(entry - sl)
    rr = abs(tp1 - entry) / risk if risk > 0 else 1
    
    signals.append(f"📈 RSI: {last['rsi']:.0f} | Stoch: {last['stoch_k']:.0f}")
    
    return {
        'symbol': f"{symbol}/USDT",
        'direction': direction,
        'score': score,
        'confluence': confluence,
        'price': price,
        'entry': entry,
        'sl': sl,
        'tp1': tp1,
        'tp2': tp2,
        'tp3': tp3,
        'rr': rr,
        'rsi': last['rsi'],
        'adx': last['adx'],
        'stoch': last['stoch_k'],
        'htf_trend': htf_trend,
        'signals': signals
    }

def format_signal(s):
    emoji = "🟢" if s['direction'] == "LONG" else "🔴"
    
    if s['score'] >= 95:
        grade = "💎 S-TIER PREMIUM"
    elif s['score'] >= 90:
        grade = "⭐⭐⭐ A+ QUALITY"
    else:
        grade = "⭐⭐ A QUALITY"
    
    sl_pct = abs(s['entry'] - s['sl']) / s['entry'] * 100
    tp1_pct = abs(s['tp1'] - s['entry']) / s['entry'] * 100
    
    msg = f"""{emoji} **{s['symbol']}** | {s['direction']}
━━━━━━━━━━━━━━━━━━━━
{grade}
📊 Score: **{s['score']}/100**
🕐 HTF: **{s['htf_trend']}** (1H+4H aligned)

💰 **LEVELS:**
• Entry: ${s['entry']:.4f}
• SL: ${s['sl']:.4f} (-{sl_pct:.1f}%)
• TP1: ${s['tp1']:.4f} (+{tp1_pct:.1f}%) [{s['rr']:.1f}R]
• TP2: ${s['tp2']:.4f}
• TP3: ${s['tp3']:.4f}

📈 RSI: {s['rsi']:.0f} | ADX: {s['adx']:.0f} | Stoch: {s['stoch']:.0f}

📋 **CONFIRMATIONS:**
"""
    for sig in s['signals'][:7]:
        msg += f"{sig}\n"
    
    msg += """━━━━━━━━━━━━━━━━━━━━
⚠️ Risk: 0.5-1% max
🎯 Move SL to BE at TP1"""
    return msg

def run_scan():
    global SIGNAL_CACHE
    signals = []
    current_time = time.time()
    
    logger.info(f"📡 Scanning {len(COINS)} coins with ULTRA-STRICT filters...")
    
    for coin in COINS:
        try:
            result = analyze_coin(coin)
            if result:
                signals.append(result)
                SIGNAL_CACHE[coin] = {'signal': result, 'time': current_time}
                logger.info(f"✅ SIGNAL: {coin} {result['direction']} Score:{result['score']}")
            else:
                if coin in SIGNAL_CACHE:
                    cached = SIGNAL_CACHE[coin]
                    age = current_time - cached['time']
                    if age < CACHE_DURATION:
                        signals.append(cached['signal'])
                        logger.info(f"📦 CACHED: {coin} ({int(age/60)}min)")
                    else:
                        del SIGNAL_CACHE[coin]
            
            time.sleep(0.5)
        except Exception as e:
            logger.error(f"Error {coin}: {e}")
    
    expired = [k for k, v in SIGNAL_CACHE.items() if current_time - v['time'] > CACHE_DURATION]
    for k in expired:
        del SIGNAL_CACHE[k]
    
    logger.info(f"📊 Done. {len(signals)} signals.")
    return sorted(signals, key=lambda x: x['score'], reverse=True)

# ============================================
# BACKGROUND SCANNER
# ============================================

async def background_scanner(app):
    global SIGNALS_TODAY
    
    logger.info("🚀 Background scanner starting...")
    
    # Wait for app to fully initialize
    await asyncio.sleep(10)
    
    # Send startup notification if admin chat is set
    if ADMIN_CHAT_ID:
        try:
            await app.bot.send_message(
                chat_id=int(ADMIN_CHAT_ID),
                text="🤖 Bot started! Auto scanning enabled."
            )
        except:
            pass
    
    while True:
        try:
            logger.info("🔄 AUTO SCAN cycle starting...")
            
            loop = asyncio.get_event_loop()
            signals = await loop.run_in_executor(None, run_scan)
            
            if signals and ADMIN_CHAT_ID:
                logger.info(f"📤 Sending {len(signals)} signals")
                try:
                    for sig in signals[:2]:  # Max 2 signals
                        SIGNALS_TODAY += 1
                        await app.bot.send_message(chat_id=int(ADMIN_CHAT_ID), text=format_signal(sig))
                        await asyncio.sleep(2)
                except Exception as e:
                    logger.error(f"Send error: {e}")
            else:
                logger.info("No signals or no admin chat")
            
            logger.info(f"⏰ Sleeping {SCAN_INTERVAL}s...")
            await asyncio.sleep(SCAN_INTERVAL)
            
        except Exception as e:
            logger.error(f"Scanner error: {e}")
            await asyncio.sleep(60)

# ============================================
# TELEGRAM
# ============================================

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_message.chat_id
    
    await update.message.reply_text(
        f"🏆 **ULTRA-STRICT SCANNER v4**\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 Coins: {len(COINS)}\n"
        f"⏱ TF: 15m + 1H + 4H\n\n"
        f"**FILTERS:**\n"
        f"• Score ≥ {MIN_SCORE}\n"
        f"• ADX ≥ {MIN_ADX}\n"
        f"• 5/5 Confluence\n"
        f"• 1H + 4H Aligned\n"
        f"• Volume Confirmed\n\n"
        f"Your Chat ID: `{chat_id}`\n"
        f"Add this as ADMIN_CHAT_ID in Railway!"
    )

async def scan_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 Ultra-strict scan starting...")
    
    loop = asyncio.get_event_loop()
    signals = await loop.run_in_executor(None, run_scan)
    
    if signals:
        summary = f"📊 **FOUND {len(signals)} SIGNALS**\n\n"
        for s in signals[:5]:
            emoji = "🟢" if s['direction'] == "LONG" else "🔴"
            summary += f"{emoji} {s['symbol']}: {s['direction']} ({s['score']})\n"
        await update.message.reply_text(summary)
        
        for sig in signals[:2]:
            await update.message.reply_text(format_signal(sig))
    else:
        await update.message.reply_text(
            "❌ No premium signals.\n\n"
            "Ultra-strict mode: Only 5/5 confluence with HTF alignment."
        )

async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"📊 **STATUS**\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Score: ≥{MIN_SCORE} | ADX: ≥{MIN_ADX}\n"
        f"Confluence: 5/5 required\n"
        f"HTF: 1H + 4H must align\n"
        f"Signals Today: {SIGNALS_TODAY}\n"
        f"Scan Interval: {SCAN_INTERVAL//60}min"
    )

async def post_init(app):
    asyncio.create_task(background_scanner(app))

if __name__ == '__main__':
    print("Testing API...")
    test = fetch_data("BTC", 10)
    if test is not None:
        print("✅ API OK")
    else:
        print("❌ API Failed")
    
    if not ADMIN_CHAT_ID:
        print("⚠️ ADMIN_CHAT_ID not set! Use /start to get your chat ID")
    
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).post_init(post_init).build()
    
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("scan", scan_cmd))
    app.add_handler(CommandHandler("status", status_cmd))
    
    print(f"🏆 Ultra-Strict Scanner v4")
    app.run_polling()
