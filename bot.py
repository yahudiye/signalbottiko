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

# Extended Exchange Available Coins
COINS = [
    # Core & Major Assets
    "BTC", "ETH", "SOL", "XRP", "BNB", "ADA", "LTC", "DOT", "LINK", "SUI",
    "AVAX", "APT", "NEAR", "TRX",
    # Meme & Trending
    "DOGE", "PEPE", "BONK", "SHIB", "POPCAT", "WIF", "PENGU", "TRUMP", "MELANIA",
    # DeFi & Infrastructure
    "AAVE", "UNI", "SNX", "CAKE", "ONDO", "PENDLE", "JUP", "EIGEN", "ARB", "OP",
    "STRK", "TAO", "SEI", "MKR",
    # Other Listed
    "XMR", "ZEC", "VIRTUAL"
]

# ============================================
# STRICT CONFIGURATION
# ============================================
MIN_SCORE = 75          # Only A+ setups
MIN_ADX = 25            # Strong trend required
RSI_OVERSOLD = 35       # Below this = don't short
RSI_OVERBOUGHT = 65     # Above this = don't long
REQUIRE_CONFLUENCE = True  # All indicators must align

SIGNALS_TODAY = 0

# ============================================
# INDICATORS (PURE PYTHON)
# ============================================

def ema(series, period):
    return series.ewm(span=period, adjust=False).mean()

def sma(series, period):
    return series.rolling(window=period).mean()

def rsi(series, period=14):
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def macd(series, fast=12, slow=26, signal=9):
    ema_fast = ema(series, fast)
    ema_slow = ema(series, slow)
    macd_line = ema_fast - ema_slow
    signal_line = ema(macd_line, signal)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram

def bollinger_bands(series, period=20, std_dev=2):
    middle = sma(series, period)
    std = series.rolling(window=period).std()
    upper = middle + (std * std_dev)
    lower = middle - (std * std_dev)
    return upper, middle, lower

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
    
    plus_di = 100 * (plus_dm.rolling(window=period).mean() / atr_val)
    minus_di = 100 * (minus_dm.rolling(window=period).mean() / atr_val)
    
    dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di + 0.0001)
    adx_val = dx.rolling(window=period).mean()
    
    return adx_val, plus_di, minus_di

def stochastic(high, low, close, k_period=14, d_period=3):
    lowest_low = low.rolling(window=k_period).min()
    highest_high = high.rolling(window=k_period).max()
    k = 100 * (close - lowest_low) / (highest_high - lowest_low + 0.0001)
    d = k.rolling(window=d_period).mean()
    return k, d

def detect_swing_points(df, left=5, right=5):
    """Detect swing highs and lows"""
    swing_highs = []
    swing_lows = []
    
    for i in range(left, len(df) - right):
        # Swing High
        is_high = True
        for j in range(1, left + 1):
            if df['high'].iloc[i] <= df['high'].iloc[i - j]:
                is_high = False
                break
        for j in range(1, right + 1):
            if df['high'].iloc[i] <= df['high'].iloc[i + j]:
                is_high = False
                break
        if is_high:
            swing_highs.append((i, df['high'].iloc[i]))
        
        # Swing Low
        is_low = True
        for j in range(1, left + 1):
            if df['low'].iloc[i] >= df['low'].iloc[i - j]:
                is_low = False
                break
        for j in range(1, right + 1):
            if df['low'].iloc[i] >= df['low'].iloc[i + j]:
                is_low = False
                break
        if is_low:
            swing_lows.append((i, df['low'].iloc[i]))
    
    return swing_highs, swing_lows

def market_structure(df):
    """Analyze HH/HL or LH/LL structure"""
    swing_highs, swing_lows = detect_swing_points(df, 5, 5)
    
    if len(swing_highs) < 3 or len(swing_lows) < 3:
        return "UNCLEAR", 0
    
    # Check last 3 swing points
    recent_highs = [h[1] for h in swing_highs[-3:]]
    recent_lows = [l[1] for l in swing_lows[-3:]]
    
    # Higher Highs & Higher Lows = Bullish
    hh = recent_highs[-1] > recent_highs[-2] > recent_highs[-3]
    hl = recent_lows[-1] > recent_lows[-2] > recent_lows[-3]
    
    # Lower Highs & Lower Lows = Bearish
    lh = recent_highs[-1] < recent_highs[-2] < recent_highs[-3]
    ll = recent_lows[-1] < recent_lows[-2] < recent_lows[-3]
    
    if hh and hl:
        return "BULLISH", 100
    elif lh and ll:
        return "BEARISH", 100
    elif hh or hl:
        return "BULLISH", 60
    elif lh or ll:
        return "BEARISH", 60
    else:
        return "RANGING", 30

def volume_analysis(df):
    """Volume strength"""
    vol_sma = sma(df['volume'], 20)
    current = df['volume'].iloc[-1]
    avg = vol_sma.iloc[-1]
    
    if pd.isna(avg) or avg == 0:
        return "NORMAL", 50
    
    ratio = current / avg
    if ratio > 2:
        return "EXPLOSIVE", 100
    elif ratio > 1.5:
        return "HIGH", 80
    elif ratio > 1:
        return "ABOVE_AVG", 60
    else:
        return "LOW", 30

def momentum_check(df):
    """Check momentum alignment"""
    close = df['close']
    
    roc_5 = (close.iloc[-1] / close.iloc[-5] - 1) * 100
    roc_10 = (close.iloc[-1] / close.iloc[-10] - 1) * 100
    
    if roc_5 > 0 and roc_10 > 0:
        return "BULLISH", abs(roc_5)
    elif roc_5 < 0 and roc_10 < 0:
        return "BEARISH", abs(roc_5)
    else:
        return "MIXED", 0

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
# PRO ANALYSIS ENGINE (STRICT VERSION)
# ============================================

def analyze_coin(symbol):
    """Professional analysis with strict confluence"""
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
    bb_upper, bb_middle, bb_lower = bollinger_bands(close)
    atr_val = atr(high, low, close, 14)
    adx_val, plus_di, minus_di = adx(high, low, close, 14)
    stoch_k, stoch_d = stochastic(high, low, close)
    
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
        'bb_upper': bb_upper.iloc[-1],
        'bb_lower': bb_lower.iloc[-1],
        'atr': atr_val.iloc[-1],
        'adx': adx_val.iloc[-1],
        'plus_di': plus_di.iloc[-1],
        'minus_di': minus_di.iloc[-1],
        'stoch_k': stoch_k.iloc[-1],
        'stoch_d': stoch_d.iloc[-1]
    }
    
    # Skip if NaN
    if any(pd.isna(v) for v in last.values()):
        return None
    
    # ============================================
    # STRICT FILTERS (MUST PASS ALL)
    # ============================================
    
    # Filter 1: ADX must be > 25 (Strong trend required)
    if last['adx'] < MIN_ADX:
        return None
    
    # Filter 2: Determine trend direction from multiple sources
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
    # CONFLUENCE CHECK (ALL MUST ALIGN)
    # ============================================
    
    signals = []
    
    # Count bullish/bearish confirmations
    bullish_count = 0
    bearish_count = 0
    
    # Structure
    if structure == "BULLISH":
        bullish_count += 1
        signals.append(f"� Structure: HH/HL Confirmed ({struct_conf}%)")
    elif structure == "BEARISH":
        bearish_count += 1
        signals.append(f"📉 Structure: LH/LL Confirmed ({struct_conf}%)")
    
    # EMA
    if ema_bullish:
        bullish_count += 1
        signals.append("✅ EMA: 9 > 21 > 50 (Bullish Stack)")
    elif ema_bearish:
        bearish_count += 1
        signals.append("✅ EMA: 9 < 21 < 50 (Bearish Stack)")
    
    # ADX Direction
    if adx_bullish:
        bullish_count += 1
        signals.append(f"💪 ADX: {last['adx']:.0f} | DI+ > DI-")
    elif adx_bearish:
        bearish_count += 1
        signals.append(f"💪 ADX: {last['adx']:.0f} | DI- > DI+")
    
    # MACD
    if macd_bullish:
        bullish_count += 1
        signals.append("✅ MACD: Bullish Crossover")
    elif macd_bearish:
        bearish_count += 1
        signals.append("✅ MACD: Bearish Crossover")
    
    # Momentum
    if momentum == "BULLISH":
        bullish_count += 1
        signals.append(f"🚀 Momentum: Bullish ({mom_strength:.1f}%)")
    elif momentum == "BEARISH":
        bearish_count += 1
        signals.append(f"📉 Momentum: Bearish ({mom_strength:.1f}%)")
    
    # ============================================
    # REQUIRE MINIMUM 4/5 CONFLUENCE
    # ============================================
    
    if REQUIRE_CONFLUENCE:
        if bullish_count < 4 and bearish_count < 4:
            return None  # Not enough confluence
    
    # Determine direction
    if bullish_count >= 4:
        direction = "LONG"
        confluence = bullish_count
    elif bearish_count >= 4:
        direction = "SHORT"
        confluence = bearish_count
    else:
        return None
    
    # ============================================
    # RSI CONFLICT CHECK
    # ============================================
    
    if direction == "SHORT" and last['rsi'] < RSI_OVERSOLD:
        signals.append(f"❌ BLOCKED: RSI {last['rsi']:.1f} Oversold")
        return None
    
    if direction == "LONG" and last['rsi'] > RSI_OVERBOUGHT:
        signals.append(f"❌ BLOCKED: RSI {last['rsi']:.1f} Overbought")
        return None
    
    # ============================================
    # STOCHASTIC CHECK
    # ============================================
    
    stoch_oversold = last['stoch_k'] < 25
    stoch_overbought = last['stoch_k'] > 75
    
    if direction == "SHORT" and stoch_oversold:
        return None  # Don't short oversold
    
    if direction == "LONG" and stoch_overbought:
        return None  # Don't long overbought
    
    # Add RSI info
    signals.append(f"📊 RSI: {last['rsi']:.1f}")
    signals.append(f"📊 Stoch: {last['stoch_k']:.0f}")
    
    # Volume
    if vol_status in ["EXPLOSIVE", "HIGH"]:
        signals.append(f"📊 Volume: {vol_status}")
    
    # ============================================
    # CALCULATE SCORE
    # ============================================
    
    base_score = confluence * 15  # 4*15=60, 5*15=75
    
    # Bonus points
    if last['adx'] > 35:
        base_score += 10
        signals.append("🔥 ADX > 35: Very Strong Trend")
    
    if vol_status in ["EXPLOSIVE", "HIGH"]:
        base_score += 5
    
    if struct_conf == 100:
        base_score += 5
    
    score = min(base_score, 100)
    
    # Check minimum score
    if score < MIN_SCORE:
        return None
    
    # ============================================
    # CALCULATE TRADE LEVELS
    # ============================================
    
    price = last['price']
    atr_value = last['atr']
    
    if direction == "LONG":
        entry = price
        sl = price - (atr_value * 1.5)
        tp1 = price + (atr_value * 2)
        tp2 = price + (atr_value * 3)
        tp3 = price + (atr_value * 5)
    else:
        entry = price
        sl = price + (atr_value * 1.5)
        tp1 = price - (atr_value * 2)
        tp2 = price - (atr_value * 3)
        tp3 = price - (atr_value * 5)
    
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
        'signals': signals
    }

def format_signal(s):
    """Format professional signal"""
    emoji = "🟢" if s['direction'] == "LONG" else "🔴"
    
    if s['score'] >= 85:
        grade = "⭐⭐⭐ A+ PREMIUM"
    elif s['score'] >= 75:
        grade = "⭐⭐ A QUALITY"
    else:
        grade = "⭐ B STANDARD"
    
    msg = f"""{emoji} **{s['symbol']}** | {s['direction']}
━━━━━━━━━━━━━━━━━━━━
{grade}
📊 Score: **{s['score']}/100**
🎯 Confluence: **{s['confluence']}/5**

💰 **TRADE LEVELS:**
• Entry: ${s['entry']:.4f}
• Stop Loss: ${s['sl']:.4f}
• TP1: ${s['tp1']:.4f} ({s['rr']:.1f}R)
• TP2: ${s['tp2']:.4f}
• TP3: ${s['tp3']:.4f}

📈 RSI: {s['rsi']:.1f} | ADX: {s['adx']:.0f} | Stoch: {s['stoch']:.0f}

📋 **CONFIRMATIONS:**
"""
    for sig in s['signals'][:6]:
        msg += f"{sig}\n"
    
    msg += """━━━━━━━━━━━━━━━━━━━━
⚠️ Risk: 1-2% max per trade"""
    return msg

# ============================================
# SCAN
# ============================================

def run_scan():
    signals = []
    for coin in COINS:
        try:
            result = analyze_coin(coin)
            if result:
                signals.append(result)
                logger.info(f"✅ {coin}: {result['direction']} | Score: {result['score']} | Conf: {result['confluence']}/5")
            time.sleep(0.4)
        except Exception as e:
            logger.error(f"Error {coin}: {e}")
    return sorted(signals, key=lambda x: x['score'], reverse=True)

# ============================================
# TELEGRAM
# ============================================

async def auto_scan(context):
    global SIGNALS_TODAY
    logger.info("Auto scanning...")
    
    loop = asyncio.get_event_loop()
    signals = await loop.run_in_executor(None, run_scan)
    
    job = context.job
    if job and job.chat_id and signals:
        for sig in signals[:3]:
            SIGNALS_TODAY += 1
            await context.bot.send_message(chat_id=job.chat_id, text=format_signal(sig))

async def start_cmd(update: Update, context):
    chat_id = update.effective_message.chat_id
    await update.message.reply_text(
        f"🏆 **PRO SIGNAL SCANNER v2**\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 Coins: {len(COINS)}\n"
        f"⏱ Timeframe: 15m\n\n"
        f"**STRICT FILTERS:**\n"
        f"• Min Score: {MIN_SCORE}/100\n"
        f"• Min ADX: {MIN_ADX}\n"
        f"• Min Confluence: 4/5\n"
        f"• RSI Conflict: Blocked\n\n"
        f"**Confluence Required:**\n"
        f"• Market Structure\n"
        f"• EMA Stack\n"
        f"• ADX Direction\n"
        f"• MACD Crossover\n"
        f"• Momentum\n\n"
        f"Scanning every 10 min..."
    )
    
    jobs = context.job_queue.get_jobs_by_name(str(chat_id))
    for j in jobs:
        j.schedule_removal()
    
    context.job_queue.run_repeating(auto_scan, interval=600, first=10, chat_id=chat_id, name=str(chat_id))

async def stop_cmd(update: Update, context):
    chat_id = update.effective_message.chat_id
    jobs = context.job_queue.get_jobs_by_name(str(chat_id))
    for j in jobs:
        j.schedule_removal()
    await update.message.reply_text("🛑 Stopped")

async def scan_cmd(update: Update, context):
    await update.message.reply_text(f"🔍 Pro Scan with STRICT filters...")
    
    loop = asyncio.get_event_loop()
    signals = await loop.run_in_executor(None, run_scan)
    
    if signals:
        summary = f"📊 **SCAN COMPLETE**\n"
        summary += f"Strict Mode: MIN_SCORE={MIN_SCORE}, 4/5 Confluence\n"
        summary += f"Found: {len(signals)} A+ setups\n\n"
        
        for s in signals[:10]:
            emoji = "🟢" if s['direction'] == "LONG" else "🔴"
            stars = "⭐⭐⭐" if s['score'] >= 85 else "⭐⭐" if s['score'] >= 75 else "⭐"
            summary += f"{emoji} {s['symbol']}: {s['direction']} ({s['score']}) {stars}\n"
        
        await update.message.reply_text(summary)
        
        for sig in signals[:3]:
            await update.message.reply_text(format_signal(sig))
    else:
        await update.message.reply_text(
            "❌ No A+ setups found.\n\n"
            "This is GOOD - means we're being selective.\n"
            "No trade > Bad trade"
        )

async def status_cmd(update: Update, context):
    await update.message.reply_text(
        f"📊 **STATUS**\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Min Score: {MIN_SCORE}/100\n"
        f"Min ADX: {MIN_ADX}\n"
        f"Min Confluence: 4/5\n"
        f"Signals Today: {SIGNALS_TODAY}"
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
    
    print(f"🏆 Pro Scanner v2 | MIN_SCORE={MIN_SCORE} | ADX>{MIN_ADX} | 4/5 Confluence")
    app.run_polling()
