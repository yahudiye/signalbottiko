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
from tradingview_ta import TA_Handler, Interval, Exchange

# Logging
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")

# EdgeX Pro Exchange Tokens
COINS = [
    "BTC", "ETH", "SOL", "XRP", "BNB", "LTC", "BCH", "DOT", "LINK", "SUI",
    "AVAX", "APT", "NEAR", "TRX", "DOGE", "ATOM", "ETC", "XLM", "XMR", "ZEC",
    "AAVE", "UNI", "CRV", "LDO", "PENDLE", "JUP", "OP", "ARB", "ICP", "FIL",
    "PEPE", "BONK", "SHIB", "WIF", "TRUMP", "HYPE",
    "TON", "TAO", "SEI", "CAKE", "CFX", "CRO", "ONDO", "ENS", "ENA", "HBAR",
    "PYTH", "RAY", "ORDI", "WLD", "ALGO", "OKB", "MNT", "ZK", "GRASS"
]

# Configuration
SCAN_INTERVAL = 180  # 3 minutes
SIGNALS_TODAY = 0
SIGNAL_CACHE = {}
CACHE_DURATION = 1800  # 30 min

# ============================================
# TRADINGVIEW ANALYSIS
# ============================================

def get_tv_analysis(symbol, timeframe=Interval.INTERVAL_15_MINUTES):
    """Get TradingView Technical Analysis for a symbol"""
    try:
        # Try different exchanges
        exchanges = ["BINANCE", "BYBIT", "OKX", "COINBASE"]
        
        for exchange in exchanges:
            try:
                handler = TA_Handler(
                    symbol=f"{symbol}USDT",
                    screener="crypto",
                    exchange=exchange,
                    interval=timeframe
                )
                analysis = handler.get_analysis()
                return analysis
            except:
                continue
        
        return None
    except Exception as e:
        logger.error(f"TV Analysis error {symbol}: {e}")
        return None

def analyze_coin_tv(symbol):
    """Analyze coin using TradingView Technical Analysis"""
    
    # Get analysis for multiple timeframes
    tf_15m = get_tv_analysis(symbol, Interval.INTERVAL_15_MINUTES)
    tf_1h = get_tv_analysis(symbol, Interval.INTERVAL_1_HOUR)
    tf_4h = get_tv_analysis(symbol, Interval.INTERVAL_4_HOURS)
    
    if not tf_15m or not tf_1h:
        return None
    
    # Get recommendations
    rec_15m = tf_15m.summary.get('RECOMMENDATION', 'NEUTRAL')
    rec_1h = tf_1h.summary.get('RECOMMENDATION', 'NEUTRAL')
    rec_4h = tf_4h.summary.get('RECOMMENDATION', 'NEUTRAL') if tf_4h else 'NEUTRAL'
    
    # Get scores
    buy_15m = tf_15m.summary.get('BUY', 0)
    sell_15m = tf_15m.summary.get('SELL', 0)
    neutral_15m = tf_15m.summary.get('NEUTRAL', 0)
    
    buy_1h = tf_1h.summary.get('BUY', 0)
    sell_1h = tf_1h.summary.get('SELL', 0)
    
    # Get oscillators and moving averages
    osc_rec = tf_15m.oscillators.get('RECOMMENDATION', 'NEUTRAL')
    ma_rec = tf_15m.moving_averages.get('RECOMMENDATION', 'NEUTRAL')
    
    # Get key indicators
    indicators = tf_15m.indicators
    rsi = indicators.get('RSI', 50)
    macd = indicators.get('MACD.macd', 0)
    macd_signal = indicators.get('MACD.signal', 0)
    stoch_k = indicators.get('Stoch.K', 50)
    adx = indicators.get('ADX', 0)
    ema_20 = indicators.get('EMA20', 0)
    ema_50 = indicators.get('EMA50', 0)
    close = indicators.get('close', 0)
    
    # ============================================
    # SIGNAL LOGIC
    # ============================================
    
    signals = []
    direction = None
    score = 0
    
    # Check for STRONG signals
    strong_buy = rec_15m in ['STRONG_BUY', 'BUY'] and rec_1h in ['STRONG_BUY', 'BUY']
    strong_sell = rec_15m in ['STRONG_SELL', 'SELL'] and rec_1h in ['STRONG_SELL', 'SELL']
    
    # 4H confirmation (bonus)
    htf_bull = rec_4h in ['STRONG_BUY', 'BUY']
    htf_bear = rec_4h in ['STRONG_SELL', 'SELL']
    
    if strong_buy:
        direction = "LONG"
        signals.append(f"📈 15m: {rec_15m}")
        signals.append(f"� 1H: {rec_1h}")
        if htf_bull:
            signals.append(f"📈 4H: {rec_4h} ✓")
        score = 70 + buy_15m * 2
    elif strong_sell:
        direction = "SHORT"
        signals.append(f"� 15m: {rec_15m}")
        signals.append(f"� 1H: {rec_1h}")
        if htf_bear:
            signals.append(f"📉 4H: {rec_4h} ✓")
        score = 70 + sell_15m * 2
    else:
        return None
    
    # RSI filter
    if direction == "LONG" and rsi > 70:
        return None  # Overbought
    if direction == "SHORT" and rsi < 30:
        return None  # Oversold
    
    # Stochastic filter
    if direction == "LONG" and stoch_k > 80:
        return None
    if direction == "SHORT" and stoch_k < 20:
        return None
    
    # ADX bonus
    if adx and adx > 25:
        score += 10
        signals.append(f"� ADX: {adx:.0f} (Strong)")
    
    # Oscillator/MA alignment
    if direction == "LONG" and osc_rec in ['BUY', 'STRONG_BUY']:
        score += 5
        signals.append(f"📊 Oscillators: {osc_rec}")
    elif direction == "SHORT" and osc_rec in ['SELL', 'STRONG_SELL']:
        score += 5
        signals.append(f"📊 Oscillators: {osc_rec}")
    
    if direction == "LONG" and ma_rec in ['BUY', 'STRONG_BUY']:
        score += 5
        signals.append(f"📊 Moving Avg: {ma_rec}")
    elif direction == "SHORT" and ma_rec in ['SELL', 'STRONG_SELL']:
        score += 5
        signals.append(f"📊 Moving Avg: {ma_rec}")
    
    # 4H alignment bonus
    if (direction == "LONG" and htf_bull) or (direction == "SHORT" and htf_bear):
        score += 10
    
    score = min(score, 100)
    
    if score < 75:
        return None
    
    # Add indicator info
    signals.append(f"📈 RSI: {rsi:.0f} | Stoch: {stoch_k:.0f}")
    
    # Calculate trade levels using ATR-like estimate
    atr_estimate = close * 0.02  # 2% as rough ATR
    
    if direction == "LONG":
        entry = close
        sl = close * 0.97  # 3% SL
        tp1 = close * 1.02  # 2% TP1
        tp2 = close * 1.04  # 4% TP2
        tp3 = close * 1.06  # 6% TP3
    else:
        entry = close
        sl = close * 1.03  # 3% SL
        tp1 = close * 0.98  # 2% TP1
        tp2 = close * 0.96  # 4% TP2
        tp3 = close * 0.94  # 6% TP3
    
    risk = abs(entry - sl)
    rr = abs(tp1 - entry) / risk if risk > 0 else 1
    
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
        'adx': adx if adx else 0,
        'stoch': stoch_k,
        'rec_15m': rec_15m,
        'rec_1h': rec_1h,
        'rec_4h': rec_4h,
        'signals': signals
    }

def format_signal(s):
    emoji = "🟢" if s['direction'] == "LONG" else "🔴"
    
    if s['score'] >= 90:
        grade = "💎 PREMIUM SIGNAL"
    elif s['score'] >= 85:
        grade = "⭐⭐⭐ A+ QUALITY"
    else:
        grade = "⭐⭐ A QUALITY"
    
    sl_pct = abs(s['entry'] - s['sl']) / s['entry'] * 100
    tp1_pct = abs(s['tp1'] - s['entry']) / s['entry'] * 100
    
    msg = f"""{emoji} **{s['symbol']}** | {s['direction']}
━━━━━━━━━━━━━━━━━━━━
{grade}
📊 TradingView Score: **{s['score']}/100**

🕐 **TIMEFRAME ANALYSIS:**
• 15m: {s['rec_15m']}
• 1H: {s['rec_1h']}
• 4H: {s['rec_4h']}

💰 **TRADE LEVELS:**
• Entry: ${s['entry']:.4f}
• SL: ${s['sl']:.4f} (-{sl_pct:.1f}%)
• TP1: ${s['tp1']:.4f} (+{tp1_pct:.1f}%)
• TP2: ${s['tp2']:.4f}
• TP3: ${s['tp3']:.4f}

📈 RSI: {s['rsi']:.0f} | ADX: {s['adx']:.0f} | Stoch: {s['stoch']:.0f}

📋 **CONFIRMATIONS:**
"""
    for sig in s['signals'][:7]:
        msg += f"{sig}\n"
    
    msg += """━━━━━━━━━━━━━━━━━━━━
⚠️ Risk: 1% max
🎯 Move SL to BE at TP1
📊 Source: TradingView TA"""
    return msg

def run_scan():
    global SIGNAL_CACHE
    signals = []
    current_time = time.time()
    
    logger.info(f"📡 TradingView Scan: {len(COINS)} coins...")
    
    for coin in COINS:
        try:
            result = analyze_coin_tv(coin)
            if result:
                signals.append(result)
                SIGNAL_CACHE[coin] = {'signal': result, 'time': current_time}
                logger.info(f"✅ TV SIGNAL: {coin} {result['direction']} Score:{result['score']}")
            else:
                # Check cache
                if coin in SIGNAL_CACHE:
                    cached = SIGNAL_CACHE[coin]
                    age = current_time - cached['time']
                    if age < CACHE_DURATION:
                        signals.append(cached['signal'])
                        logger.info(f"📦 CACHED: {coin} ({int(age/60)}min)")
                    else:
                        del SIGNAL_CACHE[coin]
            
            time.sleep(0.3)  # Rate limiting
        except Exception as e:
            logger.error(f"Error {coin}: {e}")
    
    # Clean expired cache
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
    
    logger.info("🚀 TradingView Scanner starting...")
    await asyncio.sleep(10)
    
    if ADMIN_CHAT_ID:
        try:
            await app.bot.send_message(
                chat_id=int(ADMIN_CHAT_ID),
                text="🤖 TradingView Bot started!\nAuto scanning enabled."
            )
        except:
            pass
    
    while True:
        try:
            logger.info("🔄 AUTO SCAN cycle...")
            
            loop = asyncio.get_event_loop()
            signals = await loop.run_in_executor(None, run_scan)
            
            if signals and ADMIN_CHAT_ID:
                logger.info(f"📤 Sending {len(signals)} signals")
                try:
                    for sig in signals[:2]:
                        SIGNALS_TODAY += 1
                        await app.bot.send_message(chat_id=int(ADMIN_CHAT_ID), text=format_signal(sig))
                        await asyncio.sleep(2)
                except Exception as e:
                    logger.error(f"Send error: {e}")
            
            logger.info(f"⏰ Next scan in {SCAN_INTERVAL}s...")
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
        f"🏆 **TRADINGVIEW SIGNAL BOT**\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 Coins: {len(COINS)}\n"
        f"⏱ Scan: Every {SCAN_INTERVAL//60} min\n\n"
        f"**SIGNAL CRITERIA:**\n"
        f"• 15m + 1H must align\n"
        f"• BUY or STRONG_BUY\n"
        f"• RSI not extreme\n"
        f"• Score ≥ 75/100\n\n"
        f"Your Chat ID: `{chat_id}`\n"
        f"Add as ADMIN_CHAT_ID in Railway!"
    )

async def scan_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 TradingView scan starting...")
    
    loop = asyncio.get_event_loop()
    signals = await loop.run_in_executor(None, run_scan)
    
    if signals:
        summary = f"📊 **TRADINGVIEW SIGNALS**\nFound: {len(signals)}\n\n"
        for s in signals[:5]:
            emoji = "🟢" if s['direction'] == "LONG" else "🔴"
            summary += f"{emoji} {s['symbol']}: {s['direction']} ({s['score']})\n"
        await update.message.reply_text(summary)
        
        for sig in signals[:2]:
            await update.message.reply_text(format_signal(sig))
    else:
        await update.message.reply_text(
            "❌ No signals found.\n\n"
            "TradingView requires:\n"
            "• 15m + 1H both BUY/SELL\n"
            "• RSI not extreme\n"
            "• Score ≥ 75"
        )

async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"📊 **STATUS**\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Source: TradingView TA\n"
        f"Coins: {len(COINS)}\n"
        f"Interval: {SCAN_INTERVAL//60}min\n"
        f"Signals Today: {SIGNALS_TODAY}\n"
        f"Cached: {len(SIGNAL_CACHE)}"
    )

async def post_init(app):
    asyncio.create_task(background_scanner(app))

if __name__ == '__main__':
    print("🏆 TradingView Signal Bot")
    print("Testing TradingView API...")
    
    test = get_tv_analysis("BTC", Interval.INTERVAL_15_MINUTES)
    if test:
        print(f"✅ TradingView OK - BTC: {test.summary.get('RECOMMENDATION')}")
    else:
        print("❌ TradingView connection failed")
    
    if not ADMIN_CHAT_ID:
        print("⚠️ ADMIN_CHAT_ID not set!")
    
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).post_init(post_init).build()
    
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("scan", scan_cmd))
    app.add_handler(CommandHandler("status", status_cmd))
    
    print("🚀 Bot running...")
    app.run_polling()
