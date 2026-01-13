import os
import time
import logging
import requests
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

# EdgeX Pro Coins
COINS = [
    "BTC", "ETH", "SOL", "XRP", "BNB", "LTC", "BCH", "DOT", "LINK", "SUI",
    "AVAX", "APT", "NEAR", "TRX", "DOGE", "ATOM", "ETC", "XLM", "XMR", "ZEC",
    "AAVE", "UNI", "CRV", "LDO", "PENDLE", "JUP", "OP", "ARB", "ICP", "FIL",
    "PEPE", "BONK", "SHIB", "WIF", "TRUMP", "HYPE",
    "TON", "TAO", "SEI", "CAKE", "CFX", "CRO", "ONDO", "ENS", "ENA", "HBAR",
    "PYTH", "RAY", "ORDI", "WLD", "ALGO", "OKB", "MNT", "ZK", "GRASS"
]

# Configuration
SCAN_INTERVAL = 120
SIGNALS_TODAY = 0
MIN_SCORE = 85  # Higher for quality
MIN_ADX = 25

# Duplicate filter
RECENT_SIGNALS = {}
DUPLICATE_COOLDOWN = 900

# Signal history
SIGNAL_HISTORY = []

# ============================================
# TRADINGVIEW ANALYSIS
# ============================================

def get_tv_analysis(symbol, timeframe=Interval.INTERVAL_15_MINUTES):
    """Get TradingView Technical Analysis"""
    exchanges = ["BINANCE", "BYBIT", "OKX", "COINBASE"]
    
    for exchange in exchanges:
        try:
            handler = TA_Handler(
                symbol=f"{symbol}USDT",
                screener="crypto",
                exchange=exchange,
                interval=timeframe
            )
            return handler.get_analysis()
        except Exception:
            continue
    
    return None

def check_market_regime(adx, atr_pct):
    """Determine market regime"""
    if adx >= 25:
        if atr_pct >= 3:
            return "VOLATILE_TREND"
        return "STRONG_TREND"
    elif adx >= 20:
        return "WEAK_TREND"
    else:
        return "RANGING"

def analyze_coin_pro(symbol):
    """
    PRO Analysis with ALL important crypto indicators:
    - RSI, MACD, Stochastic, ADX
    - Bollinger Bands
    - Volume analysis
    - Pivot Points
    - SuperTrend (via indicator)
    - CCI, Williams %R
    - EMA Stack
    """
    
    # Multi-timeframe analysis
    tf_15m = get_tv_analysis(symbol, Interval.INTERVAL_15_MINUTES)
    tf_1h = get_tv_analysis(symbol, Interval.INTERVAL_1_HOUR)
    tf_4h = get_tv_analysis(symbol, Interval.INTERVAL_4_HOURS)
    
    if not tf_15m or not tf_1h:
        return None
    
    # Get recommendations
    rec_15m = tf_15m.summary.get('RECOMMENDATION', 'NEUTRAL')
    rec_1h = tf_1h.summary.get('RECOMMENDATION', 'NEUTRAL')
    rec_4h = tf_4h.summary.get('RECOMMENDATION', 'NEUTRAL') if tf_4h else 'NEUTRAL'
    
    # Get vote counts
    buy_15m = tf_15m.summary.get('BUY', 0)
    sell_15m = tf_15m.summary.get('SELL', 0)
    buy_1h = tf_1h.summary.get('BUY', 0)
    sell_1h = tf_1h.summary.get('SELL', 0)
    
    # Oscillators and MAs
    osc_rec = tf_15m.oscillators.get('RECOMMENDATION', 'NEUTRAL')
    ma_rec = tf_15m.moving_averages.get('RECOMMENDATION', 'NEUTRAL')
    
    # ============================================
    # GET ALL INDICATORS
    # ============================================
    ind = tf_15m.indicators
    
    # Price data
    close = ind.get('close', 0)
    high = ind.get('high', 0)
    low = ind.get('low', 0)
    open_price = ind.get('open', 0)
    volume = ind.get('volume', 0)
    
    if not close or close == 0:
        return None
    
    # Trend Indicators
    rsi = ind.get('RSI', 50)
    macd = ind.get('MACD.macd', 0)
    macd_signal = ind.get('MACD.signal', 0)
    stoch_k = ind.get('Stoch.K', 50)
    stoch_d = ind.get('Stoch.D', 50)
    adx = ind.get('ADX', 0)
    adx_plus = ind.get('ADX+DI', 0)
    adx_minus = ind.get('ADX-DI', 0)
    cci = ind.get('CCI20', 0)
    williams_r = ind.get('W.R', -50)
    
    # Moving Averages
    ema_10 = ind.get('EMA10', 0)
    ema_20 = ind.get('EMA20', 0)
    ema_50 = ind.get('EMA50', 0)
    ema_100 = ind.get('EMA100', 0)
    ema_200 = ind.get('EMA200', 0)
    sma_20 = ind.get('SMA20', 0)
    sma_50 = ind.get('SMA50', 0)
    
    # Volatility - Bollinger Bands
    bb_upper = ind.get('BB.upper', 0)
    bb_lower = ind.get('BB.lower', 0)
    bb_middle = sma_20  # BB middle is usually SMA20
    
    # ATR
    atr = ind.get('ATR', 0)
    
    # Volume indicators
    ao = ind.get('AO', 0)  # Awesome Oscillator
    momentum = ind.get('Mom', 0)
    
    # Pivot Points
    pivot = ind.get('Pivot.M.Classic.Middle', 0)
    pivot_r1 = ind.get('Pivot.M.Classic.R1', 0)
    pivot_s1 = ind.get('Pivot.M.Classic.S1', 0)
    pivot_r2 = ind.get('Pivot.M.Classic.R2', 0)
    pivot_s2 = ind.get('Pivot.M.Classic.S2', 0)
    
    # Ichimoku
    ichimoku_base = ind.get('Ichimoku.BLine', 0)
    ichimoku_conv = ind.get('Ichimoku.CLine', 0)
    
    # ============================================
    # CALCULATE COMPOSITE SCORES
    # ============================================
    
    atr_pct = (atr / close * 100) if atr and close else 2.0
    regime = check_market_regime(adx if adx else 0, atr_pct)
    
    # Skip ranging markets
    if regime == "RANGING":
        return None
    
    signals = []
    bull_score = 0
    bear_score = 0
    confirmations = 0
    
    # ============================================
    # 1. EMA STACK CHECK
    # ============================================
    if ema_10 and ema_20 and ema_50:
        if ema_10 > ema_20 > ema_50:
            bull_score += 15
            confirmations += 1
            signals.append("📈 EMA Stack: Bullish ✓")
        elif ema_10 < ema_20 < ema_50:
            bear_score += 15
            confirmations += 1
            signals.append("📉 EMA Stack: Bearish ✓")
    
    # ============================================
    # 2. RSI ANALYSIS
    # ============================================
    if rsi:
        if 40 <= rsi <= 60:
            # Neutral zone - good for entries
            if rsi > 50:
                bull_score += 10
            else:
                bear_score += 10
            confirmations += 1
            signals.append(f"📊 RSI: {rsi:.0f} (Neutral Zone ✓)")
        elif rsi > 70:
            return None  # Overbought - skip
        elif rsi < 30:
            return None  # Oversold - skip
        elif rsi > 60:
            bull_score += 5
            signals.append(f"📊 RSI: {rsi:.0f} (Bullish)")
        elif rsi < 40:
            bear_score += 5
            signals.append(f"📊 RSI: {rsi:.0f} (Bearish)")
    
    # ============================================
    # 3. MACD ANALYSIS
    # ============================================
    if macd is not None and macd_signal is not None:
        macd_histogram = macd - macd_signal
        if macd > macd_signal and macd_histogram > 0:
            bull_score += 10
            confirmations += 1
            signals.append("✅ MACD: Bullish Cross")
        elif macd < macd_signal and macd_histogram < 0:
            bear_score += 10
            confirmations += 1
            signals.append("✅ MACD: Bearish Cross")
    
    # ============================================
    # 4. STOCHASTIC ANALYSIS
    # ============================================
    if stoch_k and stoch_d:
        if stoch_k > 80 or stoch_d > 80:
            return None  # Overbought
        elif stoch_k < 20 or stoch_d < 20:
            return None  # Oversold
        elif stoch_k > stoch_d and stoch_k > 50:
            bull_score += 10
            confirmations += 1
            signals.append(f"📈 Stoch: {stoch_k:.0f} > {stoch_d:.0f}")
        elif stoch_k < stoch_d and stoch_k < 50:
            bear_score += 10
            confirmations += 1
            signals.append(f"📉 Stoch: {stoch_k:.0f} < {stoch_d:.0f}")
    
    # ============================================
    # 5. ADX / DMI ANALYSIS
    # ============================================
    if adx and adx >= MIN_ADX:
        if adx_plus and adx_minus:
            if adx_plus > adx_minus:
                bull_score += 15
                confirmations += 1
                signals.append(f"� ADX: {adx:.0f} (+DI > -DI)")
            elif adx_minus > adx_plus:
                bear_score += 15
                confirmations += 1
                signals.append(f"� ADX: {adx:.0f} (-DI > +DI)")
    else:
        return None  # Weak trend
    
    # ============================================
    # 6. BOLLINGER BANDS POSITION
    # ============================================
    if bb_upper and bb_lower and close:
        bb_width = (bb_upper - bb_lower) / close * 100
        bb_position = (close - bb_lower) / (bb_upper - bb_lower) if bb_upper != bb_lower else 0.5
        
        if bb_position > 0.8:
            return None  # Near upper band - risk of pullback
        elif bb_position < 0.2:
            return None  # Near lower band - risk of bounce
        elif 0.4 <= bb_position <= 0.6:
            # Middle zone - good for trend continuation
            confirmations += 1
            signals.append(f"� BB: Middle Zone ({bb_position:.1%})")
    
    # ============================================
    # 7. CCI ANALYSIS
    # ============================================
    if cci:
        if cci > 100:
            bull_score += 5
            signals.append(f"� CCI: {cci:.0f} (Strong)")
        elif cci < -100:
            bear_score += 5
            signals.append(f"📉 CCI: {cci:.0f} (Strong)")
        elif cci > 0:
            bull_score += 3
        else:
            bear_score += 3
    
    # ============================================
    # 8. WILLIAMS %R
    # ============================================
    if williams_r:
        if williams_r > -20:
            return None  # Overbought
        elif williams_r < -80:
            return None  # Oversold
        elif williams_r > -50:
            bull_score += 5
        else:
            bear_score += 5
    
    # ============================================
    # 9. MOMENTUM / AWESOME OSCILLATOR
    # ============================================
    if ao:
        if ao > 0:
            bull_score += 5
            signals.append("� AO: Positive")
        else:
            bear_score += 5
            signals.append("� AO: Negative")
    
    if momentum and momentum > 0:
        bull_score += 5
    elif momentum and momentum < 0:
        bear_score += 5
    
    # ============================================
    # 10. PIVOT POINT ANALYSIS
    # ============================================
    if pivot and close:
        if close > pivot:
            bull_score += 5
            if pivot_r1 and close < pivot_r1:
                signals.append(f"📍 Above Pivot, Below R1")
        else:
            bear_score += 5
            if pivot_s1 and close > pivot_s1:
                signals.append(f"📍 Below Pivot, Above S1")
    
    # ============================================
    # 11. PRICE VS EMA DISTANCE CHECK
    # ============================================
    if ema_20 and close:
        distance = ((close - ema_20) / ema_20) * 100
        if abs(distance) > 3:
            return None  # Too extended
        elif abs(distance) < 1:
            confirmations += 1
            signals.append(f"📏 Near EMA20 ({distance:+.1f}%)")
    
    # ============================================
    # 12. MULTI-TIMEFRAME CHECK
    # ============================================
    htf_aligned = False
    if rec_15m in ['STRONG_BUY', 'BUY'] and rec_1h in ['STRONG_BUY', 'BUY'] and rec_4h in ['STRONG_BUY', 'BUY']:
        htf_aligned = True
        bull_score += 20
        confirmations += 1
        signals.append("🕐 All TFs Bullish ✓")
    elif rec_15m in ['STRONG_SELL', 'SELL'] and rec_1h in ['STRONG_SELL', 'SELL'] and rec_4h in ['STRONG_SELL', 'SELL']:
        htf_aligned = True
        bear_score += 20
        confirmations += 1
        signals.append("🕐 All TFs Bearish ✓")
    
    if not htf_aligned:
        return None  # TFs not aligned
    
    # ============================================
    # DETERMINE DIRECTION
    # ============================================
    
    if bull_score > bear_score and bull_score >= 50:
        direction = "LONG"
        score = min(bull_score, 100)
    elif bear_score > bull_score and bear_score >= 50:
        direction = "SHORT"
        score = min(bear_score, 100)
    else:
        return None
    
    # Minimum confirmations required
    if confirmations < 4:
        return None
    
    # ============================================
    # DUPLICATE CHECK
    # ============================================
    current_time = time.time()
    if symbol in RECENT_SIGNALS:
        last = RECENT_SIGNALS[symbol]
        if last['direction'] == direction and (current_time - last['time']) < DUPLICATE_COOLDOWN:
            return None
    
    # ============================================
    # CALCULATE TRADE LEVELS
    # ============================================
    
    if regime == "VOLATILE_TREND":
        sl_mult = 3.5
        tp_mult = 4.5
    elif regime == "STRONG_TREND":
        sl_mult = 3.0
        tp_mult = 4.0
    else:
        sl_mult = 2.5
        tp_mult = 3.0
    
    atr_val = atr if atr and atr > 0 else close * 0.02
    
    if direction == "LONG":
        entry = close
        # Use support levels if available
        if pivot_s1 and pivot_s1 < close:
            sl = max(close - (atr_val * sl_mult), pivot_s1 * 0.995)
        else:
            sl = close - (atr_val * sl_mult)
        # Use resistance for TP if available
        if pivot_r1 and pivot_r1 > close:
            tp1 = min(close + (atr_val * tp_mult), pivot_r1 * 0.995)
        else:
            tp1 = close + (atr_val * tp_mult)
        tp2 = close + (atr_val * (tp_mult + 2))
        tp3 = close + (atr_val * (tp_mult + 4))
    else:
        entry = close
        if pivot_r1 and pivot_r1 > close:
            sl = min(close + (atr_val * sl_mult), pivot_r1 * 1.005)
        else:
            sl = close + (atr_val * sl_mult)
        if pivot_s1 and pivot_s1 < close:
            tp1 = max(close - (atr_val * tp_mult), pivot_s1 * 1.005)
        else:
            tp1 = close - (atr_val * tp_mult)
        tp2 = close - (atr_val * (tp_mult + 2))
        tp3 = close - (atr_val * (tp_mult + 4))
    
    risk = abs(entry - sl)
    rr = abs(tp1 - entry) / risk if risk > 0 else 1
    sl_pct = (risk / close) * 100
    
    # Record signal
    RECENT_SIGNALS[symbol] = {'direction': direction, 'time': current_time}
    SIGNAL_HISTORY.append({
        'symbol': symbol,
        'direction': direction,
        'entry': entry,
        'time': datetime.now().isoformat()
    })
    
    return {
        'symbol': f"{symbol}/USDT",
        'direction': direction,
        'score': score,
        'confirmations': confirmations,
        'price': close,
        'entry': entry,
        'sl': sl,
        'sl_pct': sl_pct,
        'tp1': tp1,
        'tp2': tp2,
        'tp3': tp3,
        'rr': rr,
        'rsi': rsi if rsi else 50,
        'adx': adx if adx else 0,
        'stoch': stoch_k if stoch_k else 50,
        'cci': cci if cci else 0,
        'regime': regime,
        'rec_15m': rec_15m,
        'rec_1h': rec_1h,
        'rec_4h': rec_4h,
        'signals': signals
    }

def format_signal(s):
    emoji = "🟢" if s['direction'] == "LONG" else "🔴"
    
    if s['score'] >= 90:
        grade = "💎 PREMIUM"
    elif s['score'] >= 80:
        grade = "⭐⭐⭐ A+"
    elif s['score'] >= 70:
        grade = "⭐⭐ A"
    else:
        grade = "⭐ B+"
    
    sl_pct = s['sl_pct']
    if sl_pct <= 2:
        lev = "10-15x" if s['score'] >= 85 else "7-10x"
        lev_emoji = "🔥"
    elif sl_pct <= 3:
        lev = "7-10x" if s['score'] >= 85 else "5-7x"
        lev_emoji = "💪"
    elif sl_pct <= 4:
        lev = "5-7x" if s['score'] >= 85 else "3-5x"
        lev_emoji = "✅"
    else:
        lev = "3-5x"
        lev_emoji = "⚡"
    
    tp1_pct = abs(s['tp1'] - s['entry']) / s['entry'] * 100
    
    msg = f"""{emoji} **{s['symbol']}** | {s['direction']}
━━━━━━━━━━━━━━━━━━━━
{grade} | Score: **{s['score']}/100**
{lev_emoji} Leverage: **{lev}**
🎯 Confirmations: **{s['confirmations']}**
🌊 Regime: **{s['regime']}**

🕐 **TIMEFRAMES:**
• 15m: {s['rec_15m']}
• 1H: {s['rec_1h']}
• 4H: {s['rec_4h']}

💰 **LEVELS:**
• Entry: ${s['entry']:.4f}
• SL: ${s['sl']:.4f} (-{sl_pct:.1f}%)
• TP1: ${s['tp1']:.4f} (+{tp1_pct:.1f}%) [{s['rr']:.1f}R]
• TP2: ${s['tp2']:.4f}
• TP3: ${s['tp3']:.4f}

📊 RSI: {s['rsi']:.0f} | ADX: {s['adx']:.0f} | CCI: {s['cci']:.0f}

📋 **CONFIRMATIONS:**
"""
    for sig in s['signals'][:8]:
        msg += f"{sig}\n"
    
    msg += """━━━━━━━━━━━━━━━━━━━━
⚠️ Risk: 1% max | SL to BE at TP1"""
    return msg

def run_scan():
    global RECENT_SIGNALS
    signals = []
    current_time = time.time()
    
    RECENT_SIGNALS = {k: v for k, v in RECENT_SIGNALS.items() 
                      if current_time - v['time'] < DUPLICATE_COOLDOWN}
    
    logger.info(f"📡 PRO Scanning {len(COINS)} coins...")
    
    for coin in COINS:
        try:
            result = analyze_coin_pro(coin)
            if result and result['score'] >= MIN_SCORE:
                signals.append(result)
                logger.info(f"✅ {coin} {result['direction']} Score:{result['score']} Conf:{result['confirmations']}")
            time.sleep(0.15)
        except Exception as e:
            logger.error(f"Error {coin}: {str(e)[:50]}")
    
    logger.info(f"📊 Found {len(signals)} signals")
    return sorted(signals, key=lambda x: x['score'], reverse=True)

# ============================================
# BACKGROUND SCANNER
# ============================================

async def background_scanner(app):
    global SIGNALS_TODAY
    
    logger.info("🚀 Pro Scanner v7 starting...")
    await asyncio.sleep(5)
    
    if ADMIN_CHAT_ID:
        try:
            await app.bot.send_message(
                chat_id=int(ADMIN_CHAT_ID),
                text="🤖 **Pro Scanner v7**\n"
                     "━━━━━━━━━━━━━━━━━━━━\n"
                     "📊 12 Indicators Analysis\n"
                     "• EMA Stack, RSI, MACD\n"
                     "• Stoch, ADX/DMI, BB\n"
                     "• CCI, Williams %R, AO\n"
                     "• Pivots, Momentum\n"
                     "• Multi-TF Confirmation\n"
                     "━━━━━━━━━━━━━━━━━━━━"
            )
        except:
            pass
    
    while True:
        try:
            logger.info("🔄 SCANNING...")
            
            loop = asyncio.get_event_loop()
            signals = await loop.run_in_executor(None, run_scan)
            
            if signals and ADMIN_CHAT_ID:
                logger.info(f"🎯 Sending {min(len(signals), 3)} signals")
                for sig in signals[:3]:
                    SIGNALS_TODAY += 1
                    await app.bot.send_message(chat_id=int(ADMIN_CHAT_ID), text=format_signal(sig))
                    await asyncio.sleep(1)
            
            await asyncio.sleep(SCAN_INTERVAL)
            
        except Exception as e:
            logger.error(f"Error: {e}")
            await asyncio.sleep(30)

# ============================================
# TELEGRAM
# ============================================

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_message.chat_id
    
    await update.message.reply_text(
        f"🏆 **PRO SCANNER v7**\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 **12 Indicators:**\n"
        f"• EMA Stack (10/20/50)\n"
        f"• RSI (Neutral Zone)\n"
        f"• MACD Cross\n"
        f"• Stochastic K/D\n"
        f"• ADX/DMI\n"
        f"• Bollinger Bands\n"
        f"• CCI\n"
        f"• Williams %R\n"
        f"• Awesome Oscillator\n"
        f"• Momentum\n"
        f"• Pivot Points\n"
        f"• EMA Distance\n\n"
        f"🎯 Min Score: {MIN_SCORE}\n"
        f"🎯 Min Confirmations: 4\n\n"
        f"Chat ID: `{chat_id}`"
    )

async def scan_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 Pro 12-indicator scan...")
    
    loop = asyncio.get_event_loop()
    signals = await loop.run_in_executor(None, run_scan)
    
    if signals:
        summary = f"📊 **FOUND {len(signals)} SIGNALS**\n\n"
        for s in signals[:5]:
            emoji = "🟢" if s['direction'] == "LONG" else "🔴"
            summary += f"{emoji} {s['symbol']}: {s['direction']} (Score:{s['score']} Conf:{s['confirmations']})\n"
        await update.message.reply_text(summary)
        
        for sig in signals[:3]:
            await update.message.reply_text(format_signal(sig))
    else:
        await update.message.reply_text("❌ No signals passed all 12 indicator filters")

async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    total = len(SIGNAL_HISTORY)
    longs = len([s for s in SIGNAL_HISTORY if s['direction'] == 'LONG'])
    shorts = total - longs
    
    await update.message.reply_text(
        f"📊 **STATISTICS**\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Signals Today: {SIGNALS_TODAY}\n"
        f"Total History: {total}\n"
        f"Longs: {longs} | Shorts: {shorts}\n"
        f"Active Cooldowns: {len(RECENT_SIGNALS)}"
    )

async def post_init(app):
    asyncio.create_task(background_scanner(app))

if __name__ == '__main__':
    print("🏆 Pro Scanner v7 - 12 Indicators")
    print("Testing TradingView...")
    
    test = get_tv_analysis("BTC", Interval.INTERVAL_15_MINUTES)
    if test:
        ind = test.indicators
        print(f"✅ BTC: {test.summary.get('RECOMMENDATION')}")
        print(f"   RSI: {ind.get('RSI', 0):.0f} | ADX: {ind.get('ADX', 0):.0f}")
        print(f"   CCI: {ind.get('CCI20', 0):.0f} | Stoch: {ind.get('Stoch.K', 0):.0f}")
    else:
        print("❌ TradingView failed")
    
    if not ADMIN_CHAT_ID:
        print("⚠️ ADMIN_CHAT_ID not set!")
    
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).post_init(post_init).build()
    
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("scan", scan_cmd))
    app.add_handler(CommandHandler("stats", stats_cmd))
    
    print("🚀 Running...")
    app.run_polling()
