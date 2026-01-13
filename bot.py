import os
import time
import logging
import requests
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

# API
API_BASE = "https://min-api.cryptocompare.com"

# Extended Exchange Coins
COINS = [
    "BTC", "ETH", "SOL", "XRP", "BNB", "ADA", "LTC", "DOT", "LINK", "SUI",
    "AVAX", "APT", "NEAR", "TRX", "DOGE", "PEPE", "BONK", "SHIB", "WIF",
    "AAVE", "UNI", "SNX", "CAKE", "ONDO", "PENDLE", "JUP", "ARB", "OP",
    "STRK", "TAO", "SEI", "MKR", "XMR", "ZEC"
]

# ============================================
# ULTRA-STRICT CONFIGURATION
# ============================================
MIN_SCORE = 80              # Only premium setups (was 75)
MIN_ADX = 30                # Very strong trend (was 25)
RSI_OVERSOLD = 30           # Stricter (was 35)
RSI_OVERBOUGHT = 70         # Stricter (was 65)
MIN_CONFLUENCE = 4          # Minimum 4/5 confluence
ATR_SL_MULTIPLIER = 2.0     # Wider SL (was 1.5)
ATR_TP_MULTIPLIER = 3.0     # Better R:R (was 2.0)

SIGNALS_TODAY = 0
CHAT_ID = None  # Store chat_id globally

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
    """Find key support/resistance levels"""
    highs = df['high'].tail(lookback)
    lows = df['low'].tail(lookback)
    
    # Find swing points
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

def check_higher_timeframe_trend(symbol):
    """Check 1H trend for confirmation"""
    url = f"{API_BASE}/data/v2/histohour?fsym={symbol}&tsym=USDT&limit=50"
    try:
        resp = requests.get(url, timeout=15)
        data = resp.json()
        if data.get('Response') == 'Success':
            ohlcv = data['Data']['Data']
            df = pd.DataFrame(ohlcv)
            df['close'] = df['close'].astype(float)
            
            ema_20 = ema(df['close'], 20).iloc[-1]
            ema_50 = ema(df['close'], 50).iloc[-1]
            price = df['close'].iloc[-1]
            
            if price > ema_20 > ema_50:
                return "BULLISH"
            elif price < ema_20 < ema_50:
                return "BEARISH"
    except:
        pass
    return "NEUTRAL"

def market_structure(df):
    """Detect HH/HL or LH/LL"""
    highs = df['high'].tail(50)
    lows = df['low'].tail(50)
    
    # Find swing points
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
        elif hh or hl:
            return "BULLISH", 70
        elif lh or ll:
            return "BEARISH", 70
    
    return "NEUTRAL", 0

def momentum_check(df):
    """Check momentum"""
    close = df['close']
    roc_5 = (close.iloc[-1] / close.iloc[-5] - 1) * 100
    roc_10 = (close.iloc[-1] / close.iloc[-10] - 1) * 100
    
    if roc_5 > 0 and roc_10 > 0:
        return "BULLISH", abs(roc_5)
    elif roc_5 < 0 and roc_10 < 0:
        return "BEARISH", abs(roc_5)
    return "NEUTRAL", 0

def volume_analysis(df):
    vol_sma = sma(df['volume'], 20)
    current = df['volume'].iloc[-1]
    avg = vol_sma.iloc[-1]
    
    if pd.isna(avg) or avg == 0:
        return "NORMAL", 50
    
    ratio = current / avg
    if ratio > 2:
        return "EXPLOSIVE", 100
    elif ratio > 1.5:
        return "HIGH", 75
    return "NORMAL", 50

# ============================================
# DATA FETCHING
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
    """Ultra-strict analysis with HTF confirmation"""
    df = fetch_data(symbol, 200)
    if df is None or len(df) < 100:
        return None
    
    close = df['close']
    high = df['high']
    low = df['low']
    
    # Calculate indicators
    ema_9 = ema(close, 9)
    ema_21 = ema(close, 21)
    ema_50 = ema(close, 50)
    ema_200 = ema(close, 200)
    
    rsi_val = rsi(close, 14)
    macd_line, signal_line, macd_hist = macd(close)
    atr_val = atr(high, low, close, 14)
    adx_val, plus_di, minus_di = adx(high, low, close, 14)
    stoch_k, stoch_d = stochastic(high, low, close)
    
    # Get S/R levels
    support, resistance = find_support_resistance(df)
    
    # Get latest values
    last = {
        'price': close.iloc[-1],
        'ema_9': ema_9.iloc[-1],
        'ema_21': ema_21.iloc[-1],
        'ema_50': ema_50.iloc[-1],
        'ema_200': ema_200.iloc[-1],
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
    
    # Skip if NaN
    if any(pd.isna(v) for v in last.values()):
        return None
    
    # ============================================
    # ULTRA-STRICT FILTERS
    # ============================================
    
    # Filter 1: ADX must be > 30 (Very strong trend)
    if last['adx'] < MIN_ADX:
        return None
    
    # Filter 2: Check higher timeframe trend
    htf_trend = check_higher_timeframe_trend(symbol)
    
    # Get structure and momentum
    structure, struct_conf = market_structure(df)
    momentum, mom_strength = momentum_check(df)
    vol_status, vol_score = volume_analysis(df)
    
    # EMA trend
    ema_bullish = last['ema_9'] > last['ema_21'] > last['ema_50']
    ema_bearish = last['ema_9'] < last['ema_21'] < last['ema_50']
    
    # ADX trend
    adx_bullish = last['plus_di'] > last['minus_di']
    adx_bearish = last['minus_di'] > last['plus_di']
    
    # MACD trend
    macd_bullish = last['macd'] > last['macd_signal'] and last['macd_hist'] > 0
    macd_bearish = last['macd'] < last['macd_signal'] and last['macd_hist'] < 0
    
    # ============================================
    # CONFLUENCE CHECK
    # ============================================
    
    signals = []
    bullish_count = 0
    bearish_count = 0
    
    # 1. Structure
    if structure == "BULLISH":
        bullish_count += 1
        signals.append(f"📈 Structure: HH/HL")
    elif structure == "BEARISH":
        bearish_count += 1
        signals.append(f"📉 Structure: LH/LL")
    
    # 2. EMA
    if ema_bullish:
        bullish_count += 1
        signals.append("✅ EMA: Bullish Stack")
    elif ema_bearish:
        bearish_count += 1
        signals.append("✅ EMA: Bearish Stack")
    
    # 3. ADX Direction
    if adx_bullish and last['adx'] >= MIN_ADX:
        bullish_count += 1
        signals.append(f"💪 ADX: {last['adx']:.0f} Bullish")
    elif adx_bearish and last['adx'] >= MIN_ADX:
        bearish_count += 1
        signals.append(f"💪 ADX: {last['adx']:.0f} Bearish")
    
    # 4. MACD
    if macd_bullish:
        bullish_count += 1
        signals.append("✅ MACD: Bullish")
    elif macd_bearish:
        bearish_count += 1
        signals.append("✅ MACD: Bearish")
    
    # 5. Momentum
    if momentum == "BULLISH":
        bullish_count += 1
        signals.append(f"🚀 Momentum: Bullish")
    elif momentum == "BEARISH":
        bearish_count += 1
        signals.append(f"📉 Momentum: Bearish")
    
    # 6. HTF Trend (BONUS - must align!)
    htf_aligned = False
    if htf_trend == "BULLISH" and bullish_count > bearish_count:
        htf_aligned = True
        signals.append("🕐 1H Trend: BULLISH ✓")
    elif htf_trend == "BEARISH" and bearish_count > bullish_count:
        htf_aligned = True
        signals.append("🕐 1H Trend: BEARISH ✓")
    
    # ============================================
    # REQUIRE 4/5 + HTF ALIGNMENT
    # ============================================
    
    if bullish_count < MIN_CONFLUENCE and bearish_count < MIN_CONFLUENCE:
        return None
    
    # HTF must align for high-quality signals
    if not htf_aligned:
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
    
    # ============================================
    # RSI CONFLICT CHECK
    # ============================================
    
    if direction == "SHORT" and last['rsi'] < RSI_OVERSOLD:
        return None  # Don't short oversold
    
    if direction == "LONG" and last['rsi'] > RSI_OVERBOUGHT:
        return None  # Don't long overbought
    
    # ============================================
    # STOCHASTIC CHECK
    # ============================================
    
    if direction == "SHORT" and last['stoch_k'] < 25:
        return None
    
    if direction == "LONG" and last['stoch_k'] > 75:
        return None
    
    # ============================================
    # S/R LEVEL CHECK
    # ============================================
    
    price = last['price']
    
    # For LONG: price should be above support
    if direction == "LONG" and price < support * 1.01:
        return None  # Too close to support, risky
    
    # For SHORT: price should be below resistance
    if direction == "SHORT" and price > resistance * 0.99:
        return None  # Too close to resistance, risky
    
    # Add level info
    signals.append(f"📊 RSI: {last['rsi']:.0f} | Stoch: {last['stoch_k']:.0f}")
    
    if vol_status in ["EXPLOSIVE", "HIGH"]:
        signals.append(f"📊 Volume: {vol_status}")
    
    # ============================================
    # CALCULATE SCORE
    # ============================================
    
    base_score = confluence * 16  # 5*16=80
    
    if last['adx'] > 40:
        base_score += 10
        signals.append("🔥 ADX > 40: Very Strong")
    
    if htf_aligned:
        base_score += 5
    
    if vol_status in ["EXPLOSIVE", "HIGH"]:
        base_score += 5
    
    score = min(base_score, 100)
    
    if score < MIN_SCORE:
        return None
    
    # ============================================
    # TRADE LEVELS (WIDER SL)
    # ============================================
    
    atr_value = last['atr']
    
    if direction == "LONG":
        entry = price
        sl = max(price - (atr_value * ATR_SL_MULTIPLIER), support * 0.995)  # Use support as SL
        tp1 = price + (atr_value * ATR_TP_MULTIPLIER)
        tp2 = price + (atr_value * 4.5)
        tp3 = price + (atr_value * 6)
    else:
        entry = price
        sl = min(price + (atr_value * ATR_SL_MULTIPLIER), resistance * 1.005)  # Use resistance as SL
        tp1 = price - (atr_value * ATR_TP_MULTIPLIER)
        tp2 = price - (atr_value * 4.5)
        tp3 = price - (atr_value * 6)
    
    risk = abs(entry - sl)
    rr = abs(tp1 - entry) / risk if risk > 0 else 1
    
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
    
    if s['score'] >= 90:
        grade = "⭐⭐⭐ S-TIER SETUP"
    elif s['score'] >= 85:
        grade = "⭐⭐ A+ PREMIUM"
    else:
        grade = "⭐ A QUALITY"
    
    msg = f"""{emoji} **{s['symbol']}** | {s['direction']}
━━━━━━━━━━━━━━━━━━━━
{grade}
📊 Score: **{s['score']}/100**
🎯 Confluence: **{s['confluence']}/5**
🕐 1H Trend: **{s['htf_trend']}**

💰 **TRADE LEVELS:**
• Entry: ${s['entry']:.4f}
• Stop Loss: ${s['sl']:.4f}
• TP1: ${s['tp1']:.4f} ({s['rr']:.1f}R)
• TP2: ${s['tp2']:.4f}
• TP3: ${s['tp3']:.4f}

📈 RSI: {s['rsi']:.0f} | ADX: {s['adx']:.0f} | Stoch: {s['stoch']:.0f}

📋 **CONFIRMATIONS:**
"""
    for sig in s['signals'][:6]:
        msg += f"{sig}\n"
    
    msg += """━━━━━━━━━━━━━━━━━━━━
⚠️ Risk: 1% max per trade
🎯 Move SL to BE after TP1"""
    return msg

# ============================================
# SCAN
# ============================================

def run_scan():
    signals = []
    logger.info(f"Scanning {len(COINS)} coins...")
    
    for coin in COINS:
        try:
            result = analyze_coin(coin)
            if result:
                signals.append(result)
                logger.info(f"✅ SIGNAL: {coin} {result['direction']} | Score: {result['score']} | Conf: {result['confluence']}/5 | HTF: {result['htf_trend']}")
            time.sleep(0.5)
        except Exception as e:
            logger.error(f"Error {coin}: {e}")
    
    logger.info(f"Scan complete. Found {len(signals)} signals.")
    return sorted(signals, key=lambda x: x['score'], reverse=True)

# ============================================
# TELEGRAM
# ============================================

async def auto_scan(context):
    global SIGNALS_TODAY
    logger.info("🔄 AUTO SCAN STARTING...")
    
    loop = asyncio.get_event_loop()
    signals = await loop.run_in_executor(None, run_scan)
    
    job = context.job
    if job and job.chat_id:
        if signals:
            logger.info(f"📤 Sending {min(len(signals), 3)} signals to chat {job.chat_id}")
            for sig in signals[:3]:
                SIGNALS_TODAY += 1
                await context.bot.send_message(chat_id=job.chat_id, text=format_signal(sig))
                await asyncio.sleep(1)
        else:
            logger.info("No signals found this scan")
            # Notify every hour if no signals
            current_min = datetime.now().minute
            if current_min < 10:  # Only on the hour
                await context.bot.send_message(
                    chat_id=job.chat_id, 
                    text="🔍 Auto scan complete - No premium setups found.\nWaiting for better opportunities..."
                )

async def start_cmd(update: Update, context):
    global CHAT_ID
    chat_id = update.effective_message.chat_id
    CHAT_ID = chat_id
    
    await update.message.reply_text(
        f"🏆 **PRO SIGNAL SCANNER v3**\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 Coins: {len(COINS)}\n"
        f"⏱ Timeframe: 15m + 1H\n\n"
        f"**ULTRA-STRICT FILTERS:**\n"
        f"• Min Score: {MIN_SCORE}/100\n"
        f"• Min ADX: {MIN_ADX}\n"
        f"• Min Confluence: {MIN_CONFLUENCE}/5\n"
        f"• 1H Trend Alignment: Required\n"
        f"• S/R Level Check: Enabled\n"
        f"• RSI Conflict: Blocked\n\n"
        f"**Auto scanning every 10 min...**\n"
        f"You will receive signals automatically!"
    )
    
    # Remove old jobs
    jobs = context.job_queue.get_jobs_by_name(str(chat_id))
    for j in jobs:
        j.schedule_removal()
    
    # Start auto scan
    context.job_queue.run_repeating(
        auto_scan, 
        interval=600,  # 10 minutes
        first=5,       # Start in 5 seconds
        chat_id=chat_id, 
        name=str(chat_id)
    )
    
    logger.info(f"✅ Auto scan started for chat {chat_id}")

async def stop_cmd(update: Update, context):
    chat_id = update.effective_message.chat_id
    jobs = context.job_queue.get_jobs_by_name(str(chat_id))
    for j in jobs:
        j.schedule_removal()
    await update.message.reply_text("🛑 Auto scanning stopped")
    logger.info(f"Auto scan stopped for chat {chat_id}")

async def scan_cmd(update: Update, context):
    await update.message.reply_text(f"🔍 Manual scan starting...")
    
    loop = asyncio.get_event_loop()
    signals = await loop.run_in_executor(None, run_scan)
    
    if signals:
        summary = f"📊 **SCAN COMPLETE**\n"
        summary += f"Found: {len(signals)} premium setups\n\n"
        
        for s in signals[:10]:
            emoji = "🟢" if s['direction'] == "LONG" else "🔴"
            stars = "⭐⭐⭐" if s['score'] >= 90 else "⭐⭐" if s['score'] >= 85 else "⭐"
            summary += f"{emoji} {s['symbol']}: {s['direction']} ({s['score']}) {stars}\n"
        
        await update.message.reply_text(summary)
        
        for sig in signals[:3]:
            await update.message.reply_text(format_signal(sig))
    else:
        await update.message.reply_text(
            "❌ No premium setups found.\n\n"
            "Ultra-strict filters active:\n"
            f"• Score ≥ {MIN_SCORE}\n"
            f"• ADX ≥ {MIN_ADX}\n"
            f"• 4/5 confluence + HTF alignment\n\n"
            "This is GOOD - we wait for the best!"
        )

async def status_cmd(update: Update, context):
    chat_id = update.effective_message.chat_id
    jobs = context.job_queue.get_jobs_by_name(str(chat_id))
    auto_status = "🟢 Active" if jobs else "🔴 Stopped"
    
    await update.message.reply_text(
        f"📊 **STATUS**\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Auto Scan: {auto_status}\n"
        f"Min Score: {MIN_SCORE}/100\n"
        f"Min ADX: {MIN_ADX}\n"
        f"Min Confluence: {MIN_CONFLUENCE}/5\n"
        f"Signals Today: {SIGNALS_TODAY}\n"
        f"━━━━━━━━━━━━━━━━━━━━"
    )

if __name__ == '__main__':
    print("Testing API...")
    test = fetch_data("BTC", 10)
    if test is not None:
        print(f"✅ API OK")
    else:
        print("❌ API Failed")
    
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("auto", start_cmd))
    app.add_handler(CommandHandler("stop", stop_cmd))
    app.add_handler(CommandHandler("scan", scan_cmd))
    app.add_handler(CommandHandler("status", status_cmd))
    
    print(f"🏆 Pro Scanner v3 | MIN_SCORE={MIN_SCORE} | ADX>{MIN_ADX} | HTF Required")
    app.run_polling()
