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
SCAN_INTERVAL = 120  # 2 minutes
SIGNALS_TODAY = 0
MIN_SCORE = 82  # Higher threshold
MIN_ADX = 25  # Must have strong trend

# Duplicate filter
RECENT_SIGNALS = {}  # coin -> {direction, timestamp}
DUPLICATE_COOLDOWN = 900  # 15 min - same direction same coin

# Signal history for stats
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
    """Determine market regime: TRENDING, RANGING, VOLATILE"""
    if adx >= 25:
        if atr_pct >= 3:
            return "VOLATILE_TREND"
        return "STRONG_TREND"
    elif adx >= 20:
        return "WEAK_TREND"
    else:
        return "RANGING"

def analyze_coin_tv(symbol):
    """Pro analysis with market regime and dynamic levels"""
    
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
    
    # Key indicators
    ind = tf_15m.indicators
    rsi = ind.get('RSI', 50)
    macd = ind.get('MACD.macd', 0)
    macd_signal = ind.get('MACD.signal', 0)
    stoch_k = ind.get('Stoch.K', 50)
    adx = ind.get('ADX', 0)
    atr = ind.get('ATR', 0)
    ema_20 = ind.get('EMA20', 0)
    ema_50 = ind.get('EMA50', 0)
    close = ind.get('close', 0)
    high = ind.get('high', 0)
    low = ind.get('low', 0)
    
    if not close or close == 0:
        return None
    
    # Calculate ATR percentage
    atr_pct = (atr / close * 100) if atr and close else 2.0
    
    # ============================================
    # MARKET REGIME FILTER
    # ============================================
    regime = check_market_regime(adx if adx else 0, atr_pct)
    
    # Skip ranging markets
    if regime == "RANGING":
        return None
    
    # ============================================
    # EXTENSION FILTER - Don't buy at local tops!
    # ============================================
    
    # Check if price is extended from EMA20
    if ema_20 and close:
        distance_from_ema = ((close - ema_20) / ema_20) * 100
        
        # LONG: Don't buy if price > 2% above EMA20 (extended)
        # SHORT: Don't sell if price > 2% below EMA20
        if distance_from_ema > 2.0:  # Too extended for LONG
            return None
        if distance_from_ema < -2.0:  # Too extended for SHORT
            return None
    
    # RSI extension check
    if rsi and rsi > 65:  # Already overbought area
        return None
    if rsi and rsi < 35:  # Already oversold area
        return None
    
    # Stochastic extension check  
    if stoch_k and stoch_k > 75:  # Overbought
        return None
    if stoch_k and stoch_k < 25:  # Oversold
        return None
    
    # ============================================
    # SIGNAL DETECTION - STRICTER RULES
    # ============================================
    
    signals = []
    direction = None
    score = 0
    
    # STRICT: Require STRONG signals, not just BUY/SELL
    strong_buy = rec_15m == 'STRONG_BUY' and rec_1h in ['STRONG_BUY', 'BUY']
    strong_sell = rec_15m == 'STRONG_SELL' and rec_1h in ['STRONG_SELL', 'SELL']
    
    # 4H REQUIRED for entry
    htf_bull = rec_4h in ['STRONG_BUY', 'BUY']
    htf_bear = rec_4h in ['STRONG_SELL', 'SELL']
    
    # REQUIRE 4H alignment
    if strong_buy and htf_bull and not (rsi and rsi > 70):
        direction = "LONG"
        signals.append(f"📈 15m: {rec_15m} ({buy_15m} votes)")
        signals.append(f"📈 1H: {rec_1h} ({buy_1h} votes)")
        signals.append(f"📈 4H: {rec_4h} ✓")
        score = 75 + buy_15m * 2
        
    elif strong_sell and htf_bear and not (rsi and rsi < 30):
        direction = "SHORT"
        signals.append(f"📉 15m: {rec_15m} ({sell_15m} votes)")
        signals.append(f"📉 1H: {rec_1h} ({sell_1h} votes)")
        signals.append(f"📉 4H: {rec_4h} ✓")
        score = 75 + sell_15m * 2
    else:
        return None
    
    # ============================================
    # DUPLICATE FILTER
    # ============================================
    current_time = time.time()
    if symbol in RECENT_SIGNALS:
        last = RECENT_SIGNALS[symbol]
        if last['direction'] == direction and (current_time - last['time']) < DUPLICATE_COOLDOWN:
            return None  # Same signal recently
    
    # ============================================
    # SCORE BONUSES
    # ============================================
    
    # ADX bonus (strong trend)
    if adx and adx >= 25:
        score += 10
        signals.append(f"💪 ADX: {adx:.0f} (Strong)")
    elif adx and adx >= MIN_ADX:
        score += 5
        signals.append(f"📊 ADX: {adx:.0f}")
    
    # Oscillator alignment
    if direction == "LONG" and osc_rec in ['BUY', 'STRONG_BUY']:
        score += 5
        signals.append(f"✅ Oscillators: {osc_rec}")
    elif direction == "SHORT" and osc_rec in ['SELL', 'STRONG_SELL']:
        score += 5
        signals.append(f"✅ Oscillators: {osc_rec}")
    
    # MA alignment
    if direction == "LONG" and ma_rec in ['BUY', 'STRONG_BUY']:
        score += 5
        signals.append(f"✅ Moving Avg: {ma_rec}")
    elif direction == "SHORT" and ma_rec in ['SELL', 'STRONG_SELL']:
        score += 5
        signals.append(f"✅ Moving Avg: {ma_rec}")
    
    # 4H alignment bonus
    if (direction == "LONG" and htf_bull) or (direction == "SHORT" and htf_bear):
        score += 10
    
    # Regime bonus
    if regime == "STRONG_TREND":
        score += 5
        signals.append(f"🔥 Regime: {regime}")
    
    score = min(score, 100)
    
    if score < MIN_SCORE:
        return None
    
    # ============================================
    # WIDER SL / TP (to avoid stop hunting)
    # ============================================
    
    # WIDER ATR multipliers
    if regime == "VOLATILE_TREND":
        sl_mult = 4.0  # Very wide for volatile
        tp_mult = 5.0
    elif regime == "STRONG_TREND":
        sl_mult = 3.5  # Wide SL
        tp_mult = 4.5
    else:
        sl_mult = 3.0
        tp_mult = 2.0
    
    # Calculate SL/TP using ATR
    if atr and atr > 0:
        atr_val = atr
    else:
        atr_val = close * 0.02  # Fallback 2%
    
    if direction == "LONG":
        entry = close
        sl = close - (atr_val * sl_mult)
        tp1 = close + (atr_val * tp_mult)
        tp2 = close + (atr_val * (tp_mult + 1.5))
        tp3 = close + (atr_val * (tp_mult + 3))
    else:
        entry = close
        sl = close + (atr_val * sl_mult)
        tp1 = close - (atr_val * tp_mult)
        tp2 = close - (atr_val * (tp_mult + 1.5))
        tp3 = close - (atr_val * (tp_mult + 3))
    
    risk = abs(entry - sl)
    rr = abs(tp1 - entry) / risk if risk > 0 else 1
    sl_pct = (risk / close) * 100
    
    # Add indicator info
    signals.append(f"📈 RSI: {rsi:.0f} | Stoch: {stoch_k:.0f}")
    
    # Record for duplicate filter
    RECENT_SIGNALS[symbol] = {'direction': direction, 'time': current_time}
    
    # Record for history
    SIGNAL_HISTORY.append({
        'symbol': symbol,
        'direction': direction,
        'entry': entry,
        'sl': sl,
        'tp1': tp1,
        'time': datetime.now().isoformat()
    })
    
    return {
        'symbol': f"{symbol}/USDT",
        'direction': direction,
        'score': score,
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
        'atr': atr_val,
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
    elif s['score'] >= 85:
        grade = "⭐⭐⭐ A+"
    elif s['score'] >= 80:
        grade = "⭐⭐ A"
    else:
        grade = "⭐ B+"
    
    # Dynamic leverage based on SL% and score
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
🌊 Regime: **{s['regime']}**

🕐 **TIMEFRAMES:**
• 15m: {s['rec_15m']}
• 1H: {s['rec_1h']}
• 4H: {s['rec_4h']}

💰 **LEVELS (ATR-based):**
• Entry: ${s['entry']:.4f}
• SL: ${s['sl']:.4f} (-{s['sl_pct']:.1f}%)
• TP1: ${s['tp1']:.4f} (+{tp1_pct:.1f}%) [{s['rr']:.1f}R]
• TP2: ${s['tp2']:.4f}
• TP3: ${s['tp3']:.4f}

� RSI: {s['rsi']:.0f} | ADX: {s['adx']:.0f} | Stoch: {s['stoch']:.0f}

📋 **ANALYSIS:**
"""
    for sig in s['signals'][:6]:
        msg += f"{sig}\n"
    
    msg += """━━━━━━━━━━━━━━━━━━━━
⚠️ Risk: 1% max
🎯 Move SL to BE at TP1"""
    return msg

def run_scan():
    global RECENT_SIGNALS
    signals = []
    current_time = time.time()
    
    # Clean old duplicate entries
    RECENT_SIGNALS = {k: v for k, v in RECENT_SIGNALS.items() 
                      if current_time - v['time'] < DUPLICATE_COOLDOWN}
    
    logger.info(f"📡 Scanning {len(COINS)} coins...")
    
    for coin in COINS:
        try:
            result = analyze_coin_tv(coin)
            if result and result['score'] >= MIN_SCORE:
                signals.append(result)
                logger.info(f"✅ {coin} {result['direction']} Score:{result['score']} Regime:{result['regime']}")
            time.sleep(0.15)  # Fast but safe
        except Exception as e:
            logger.error(f"Error {coin}: {str(e)[:50]}")
    
    logger.info(f"📊 Found {len(signals)} signals")
    return sorted(signals, key=lambda x: x['score'], reverse=True)

# ============================================
# BACKGROUND SCANNER
# ============================================

async def background_scanner(app):
    global SIGNALS_TODAY
    
    logger.info("🚀 Pro Scanner v6 starting...")
    await asyncio.sleep(5)
    
    if ADMIN_CHAT_ID:
        try:
            await app.bot.send_message(
                chat_id=int(ADMIN_CHAT_ID),
                text="🤖 **Pro Scanner v6**\n"
                     "━━━━━━━━━━━━━━━━━━━━\n"
                     "📊 Market Regime Filter\n"
                     "📈 Dynamic ATR-based SL/TP\n"
                     "🔄 2 min scan interval\n"
                     "🚫 15min duplicate filter\n"
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
        f"🏆 **PRO SCANNER v6**\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 Coins: {len(COINS)}\n"
        f"⏱ Scan: {SCAN_INTERVAL}s\n\n"
        f"**FEATURES:**\n"
        f"• Market Regime Filter\n"
        f"• Dynamic ATR SL/TP\n"
        f"• 15m Duplicate Filter\n"
        f"• Multi-TF Analysis\n\n"
        f"Chat ID: `{chat_id}`"
    )

async def scan_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 Pro scan starting...")
    
    loop = asyncio.get_event_loop()
    signals = await loop.run_in_executor(None, run_scan)
    
    if signals:
        summary = f"📊 **FOUND {len(signals)} SIGNALS**\n\n"
        for s in signals[:5]:
            emoji = "🟢" if s['direction'] == "LONG" else "🔴"
            summary += f"{emoji} {s['symbol']}: {s['direction']} ({s['score']}) {s['regime']}\n"
        await update.message.reply_text(summary)
        
        for sig in signals[:3]:
            await update.message.reply_text(format_signal(sig))
    else:
        await update.message.reply_text("❌ No signals found")

async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show signal statistics"""
    total = len(SIGNAL_HISTORY)
    longs = len([s for s in SIGNAL_HISTORY if s['direction'] == 'LONG'])
    shorts = total - longs
    
    await update.message.reply_text(
        f"📊 **STATISTICS**\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Signals Today: {SIGNALS_TODAY}\n"
        f"Total History: {total}\n"
        f"Longs: {longs} | Shorts: {shorts}\n"
        f"Active Duplicates: {len(RECENT_SIGNALS)}"
    )

async def post_init(app):
    asyncio.create_task(background_scanner(app))

if __name__ == '__main__':
    print("🏆 Pro Scanner v6")
    print("Testing TradingView...")
    
    test = get_tv_analysis("BTC", Interval.INTERVAL_15_MINUTES)
    if test:
        rec = test.summary.get('RECOMMENDATION', 'N/A')
        adx = test.indicators.get('ADX', 0)
        print(f"✅ BTC: {rec} | ADX: {adx:.0f}")
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
