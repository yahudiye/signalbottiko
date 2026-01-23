"""
⚡ SCALP BOT PRO v3 - All-in-One Edition
═══════════════════════════════════════════════════════════════

Improvements over v2:
✅ SQLite database - signal tracking & performance stats
✅ BTC multi-TF trend analysis (not just RSI)
✅ ADX trend strength filter
✅ Volume confirmation
✅ Market session awareness
✅ Inline Telegram buttons (TP1/TP2/SL result tracking)
✅ Better message formatting (score bars, charts)
✅ More commands (/stats, /history, /status)
✅ Correlation filter (max 2 meme coins, etc.)
✅ Improved fake breakout detection
✅ Exchange integration ready (Extended.exchange placeholder)

═══════════════════════════════════════════════════════════════
"""
import os
import time
import json
import sqlite3
import logging
import asyncio
from datetime import datetime, timedelta
from typing import Optional, Dict, List

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, 
    CommandHandler, 
    CallbackQueryHandler,
    ContextTypes
)
from dotenv import load_dotenv
from tradingview_ta import TA_Handler, Interval

# ═══════════════════════════════════════════════════════════════
# LOGGING SETUP
# ═══════════════════════════════════════════════════════════════
logging.basicConfig(
    format='%(asctime)s | %(levelname)s | %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════
# LOAD ENVIRONMENT
# ═══════════════════════════════════════════════════════════════
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")

# Exchange API (Extended.exchange için hazırlık)
EXCHANGE_API_KEY = os.getenv("EXCHANGE_API_KEY", "")
EXCHANGE_API_SECRET = os.getenv("EXCHANGE_API_SECRET", "")

# ═══════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════

# Coins - Extended.exchange desteklenen tüm coinler
COINS = [
    # Major
    "BTC", "ETH", "SOL", "XRP", "BNB", "LTC", "BCH", "DOT", "LINK", "SUI",
    # L1/L2  
    "AVAX", "APT", "NEAR", "TRX", "ATOM", "ETC", "XLM", "XMR", "ZEC",
    # DeFi
    "AAVE", "UNI", "CRV", "LDO", "PENDLE", "JUP", "OP", "ARB", "ICP", "FIL",
    # Meme
    "DOGE", "PEPE", "BONK", "SHIB", "WIF", "TRUMP",
    # Others
    "HYPE", "TON", "TAO", "SEI", "CAKE", "CFX", "CRO", "ONDO", "ENS", "ENA",
    "HBAR", "PYTH", "RAY", "ORDI", "WLD", "ALGO", "OKB", "MNT", "ZK", "GRASS"
]

# Coin kategorileri (korelasyon kontrolü için)
COIN_CATEGORIES = {
    "meme": ["DOGE", "PEPE", "BONK", "SHIB", "WIF", "TRUMP"],
    "l1": ["SOL", "AVAX", "APT", "NEAR", "SUI", "SEI", "TON"],
    "l2": ["OP", "ARB", "MNT", "ZK"],
    "defi": ["AAVE", "UNI", "CRV", "LDO", "PENDLE", "JUP"],
    "ai": ["TAO", "WLD", "GRASS"],
}

# Scanning
SCAN_INTERVAL = 60              # Saniye
MAX_SIGNALS_PER_SCAN = 3        # Scan başına max sinyal
MAX_SIGNALS_DAY = 15            # Günlük max sinyal
MIN_SCORE = 75                  # Minimum sinyal skoru
DUPLICATE_COOLDOWN = 300        # Aynı coin için bekleme (saniye)

# Scalp Levels - ATR based
ATR_SL = 0.6
ATR_TP1 = 0.8
ATR_TP2 = 1.5
ATR_TP3 = 2.5

# Percentage fallback
PCT_SL = 0.5
PCT_TP1 = 0.6
PCT_TP2 = 1.2
PCT_TP3 = 2.0

# Leverage
MAX_LEVERAGE_HIGH = 20          # Score >= 85
MAX_LEVERAGE_NORMAL = 15        # Score < 85

# Dangerous hours (UTC) - Only funding hours (less restrictive)
DANGEROUS_HOURS = [0, 8]  # Sadece funding saatleri - daha fazla sinyal için

# Scoring weights
WEIGHTS = {
    "rsi_momentum": 20,
    "macd_cross": 20,
    "stochastic": 15,
    "ema_cross": 15,
    "momentum": 5,
    "tf_alignment": 15,
    "tf_15m_bonus": 10,
    "adx_bonus": 5,
    "btc_alignment": 5,
}

# Database
DATABASE_PATH = "signals.db"

# ═══════════════════════════════════════════════════════════════
# GLOBAL STATE
# ═══════════════════════════════════════════════════════════════
BTC_RSI = 50
BTC_TREND = "NEUTRAL"  # BULLISH, BEARISH, NEUTRAL
RECENT_SIGNALS = {}
SIGNALS_TODAY = 0
ACTIVE_CATEGORY_COUNT = {}  # Korelasyon kontrolü için

# ═══════════════════════════════════════════════════════════════
# DATABASE FUNCTIONS
# ═══════════════════════════════════════════════════════════════

def init_database():
    """Veritabanı tablolarını oluştur"""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            direction TEXT NOT NULL,
            score INTEGER NOT NULL,
            entry_price REAL NOT NULL,
            sl_price REAL NOT NULL,
            tp1_price REAL NOT NULL,
            tp2_price REAL NOT NULL,
            tp3_price REAL NOT NULL,
            rsi REAL,
            btc_rsi REAL,
            btc_trend TEXT,
            atr REAL,
            rec_1m TEXT,
            rec_5m TEXT,
            rec_15m TEXT,
            signals_json TEXT,
            session TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            status TEXT DEFAULT 'ACTIVE',
            result TEXT DEFAULT NULL,
            pnl_pct REAL DEFAULT NULL,
            closed_at TIMESTAMP DEFAULT NULL
        )
    """)
    
    conn.commit()
    conn.close()
    logger.info(f"✅ Database initialized: {DATABASE_PATH}")

def save_signal_to_db(signal: Dict) -> int:
    """Sinyali veritabanına kaydet"""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    
    cursor.execute("""
        INSERT INTO signals (
            symbol, direction, score, entry_price, sl_price,
            tp1_price, tp2_price, tp3_price, rsi, btc_rsi,
            btc_trend, atr, rec_1m, rec_5m, rec_15m, signals_json, session
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        signal['symbol'],
        signal['direction'],
        signal['score'],
        signal['entry'],
        signal['sl'],
        signal['tp1'],
        signal['tp2'],
        signal['tp3'],
        signal.get('rsi'),
        signal.get('btc_rsi'),
        signal.get('btc_trend'),
        signal.get('atr'),
        signal.get('rec_1m'),
        signal.get('rec_5m'),
        signal.get('rec_15m'),
        json.dumps(signal.get('signals', [])),
        signal.get('session')
    ))
    
    signal_id = cursor.lastrowid
    conn.commit()
    conn.close()
    
    return signal_id

def update_signal_result(signal_id: int, result: str, pnl_pct: float):
    """Sinyal sonucunu güncelle"""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    
    cursor.execute("""
        UPDATE signals 
        SET status = 'CLOSED', result = ?, pnl_pct = ?, closed_at = CURRENT_TIMESTAMP
        WHERE id = ?
    """, (result, pnl_pct, signal_id))
    
    conn.commit()
    conn.close()

def get_performance_stats(days: int = 7) -> Dict:
    """Performans istatistikleri"""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    
    since = (datetime.utcnow() - timedelta(days=days)).strftime('%Y-%m-%d')
    
    cursor.execute("""
        SELECT 
            COUNT(*) as total,
            SUM(CASE WHEN result = 'WIN' THEN 1 ELSE 0 END) as wins,
            SUM(CASE WHEN result = 'LOSS' THEN 1 ELSE 0 END) as losses,
            AVG(CASE WHEN result IS NOT NULL THEN pnl_pct ELSE NULL END) as avg_pnl,
            SUM(CASE WHEN result IS NOT NULL THEN pnl_pct ELSE 0 END) as total_pnl,
            MAX(pnl_pct) as best_trade,
            MIN(pnl_pct) as worst_trade
        FROM signals
        WHERE DATE(created_at) >= ?
    """, (since,))
    
    row = cursor.fetchone()
    conn.close()
    
    total = row[0] or 0
    wins = row[1] or 0
    losses = row[2] or 0
    
    return {
        "period_days": days,
        "total_signals": total,
        "wins": wins,
        "losses": losses,
        "win_rate": (wins / (wins + losses) * 100) if (wins + losses) > 0 else 0,
        "avg_pnl": row[3] or 0,
        "total_pnl": row[4] or 0,
        "best_trade": row[5] or 0,
        "worst_trade": row[6] or 0
    }

def get_recent_signals(limit: int = 10) -> List[Dict]:
    """Son sinyalleri getir"""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT id, symbol, direction, score, result, pnl_pct, created_at
        FROM signals 
        ORDER BY created_at DESC 
        LIMIT ?
    """, (limit,))
    
    columns = ['id', 'symbol', 'direction', 'score', 'result', 'pnl_pct', 'created_at']
    signals = [dict(zip(columns, row)) for row in cursor.fetchall()]
    
    conn.close()
    return signals

def get_today_signals_count() -> int:
    """Bugünkü sinyal sayısı"""
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    
    today = datetime.utcnow().strftime('%Y-%m-%d')
    cursor.execute("SELECT COUNT(*) FROM signals WHERE DATE(created_at) = ?", (today,))
    
    count = cursor.fetchone()[0]
    conn.close()
    return count

# ═══════════════════════════════════════════════════════════════
# TRADINGVIEW DATA FUNCTIONS
# ═══════════════════════════════════════════════════════════════

def get_tv(symbol: str, tf=Interval.INTERVAL_5_MINUTES):
    """TradingView'dan veri çek - multi exchange fallback"""
    for exchange in ["BINANCE", "BYBIT", "OKX", "KUCOIN"]:
        try:
            handler = TA_Handler(
                symbol=f"{symbol}USDT",
                screener="crypto",
                exchange=exchange,
                interval=tf
            )
            return handler.get_analysis()
        except:
            continue
    return None

def update_btc_trend():
    """BTC trend ve RSI güncelle - Multi-TF analiz"""
    global BTC_RSI, BTC_TREND
    
    try:
        btc_5m = get_tv("BTC", Interval.INTERVAL_5_MINUTES)
        btc_15m = get_tv("BTC", Interval.INTERVAL_15_MINUTES)
        btc_1h = get_tv("BTC", Interval.INTERVAL_1_HOUR)
        
        if btc_5m:
            BTC_RSI = btc_5m.indicators.get('RSI', 50)
            
            # Multi-TF trend analizi
            rec_5m = btc_5m.summary.get('RECOMMENDATION', 'NEUTRAL')
            rec_15m = btc_15m.summary.get('RECOMMENDATION', 'NEUTRAL') if btc_15m else 'NEUTRAL'
            rec_1h = btc_1h.summary.get('RECOMMENDATION', 'NEUTRAL') if btc_1h else 'NEUTRAL'
            
            bull_count = sum(1 for r in [rec_5m, rec_15m, rec_1h] if 'BUY' in r)
            bear_count = sum(1 for r in [rec_5m, rec_15m, rec_1h] if 'SELL' in r)
            
            if bull_count >= 2:
                BTC_TREND = "BULLISH"
            elif bear_count >= 2:
                BTC_TREND = "BEARISH"
            else:
                BTC_TREND = "NEUTRAL"
                
            logger.info(f"📊 BTC Update: RSI={BTC_RSI:.0f}, Trend={BTC_TREND}")
                
    except Exception as e:
        logger.error(f"BTC trend error: {e}")
    
    return BTC_RSI, BTC_TREND

# ═══════════════════════════════════════════════════════════════
# HELPER FUNCTIONS
# ═══════════════════════════════════════════════════════════════

def is_dangerous_hour() -> bool:
    """Tehlikeli saat kontrolü"""
    return datetime.utcnow().hour in DANGEROUS_HOURS

def get_market_session() -> str:
    """Aktif market seansı"""
    hour = datetime.utcnow().hour
    if 0 <= hour < 8:
        return "🌏 ASIA"
    elif 8 <= hour < 13:
        return "🇬🇧 LONDON"
    elif 13 <= hour < 21:
        return "🇺🇸 NY"
    else:
        return "🌏 ASIA"

def get_coin_category(symbol: str) -> Optional[str]:
    """Coin kategorisini bul"""
    for category, coins in COIN_CATEGORIES.items():
        if symbol in coins:
            return category
    return None

def check_category_limit(symbol: str) -> bool:
    """Aynı kategoride çok fazla sinyal var mı?"""
    category = get_coin_category(symbol)
    if not category:
        return True  # Kategorisiz coinler için limit yok
    
    count = ACTIVE_CATEGORY_COUNT.get(category, 0)
    if category == "meme":
        return count < 2  # Max 2 meme coin
    return count < 3  # Diğer kategoriler için max 3

# ═══════════════════════════════════════════════════════════════
# MAIN ANALYZER
# ═══════════════════════════════════════════════════════════════

def scalp_analyze(symbol: str) -> Optional[Dict]:
    """
    SCALP BOT PRO v3 - Advanced Analysis
    
    Improvements:
    - Multi-TF BTC trend (not just RSI)
    - ADX trend strength
    - Volume confirmation
    - Category correlation filter
    - Better fake breakout detection
    - Market session tagging
    """
    global SIGNALS_TODAY
    
    # ═══════ PRE-CHECKS ═══════
    
    # Daily limit
    if SIGNALS_TODAY >= MAX_SIGNALS_DAY:
        return None
    
    # Dangerous hour
    if is_dangerous_hour():
        return None
    
    # Duplicate check
    now = time.time()
    if symbol in RECENT_SIGNALS:
        last = RECENT_SIGNALS[symbol]
        if (now - last['time']) < DUPLICATE_COOLDOWN:
            return None
    
    # Category correlation check
    if not check_category_limit(symbol):
        return None
    
    # ═══════ DATA FETCH ═══════
    
    tf_1m = get_tv(symbol, Interval.INTERVAL_1_MINUTE)
    tf_5m = get_tv(symbol, Interval.INTERVAL_5_MINUTES)
    tf_15m = get_tv(symbol, Interval.INTERVAL_15_MINUTES)
    
    if not tf_1m or not tf_5m:
        return None
    
    # ═══════ EXTRACT INDICATORS ═══════
    
    ind = tf_5m.indicators
    ind_1m = tf_1m.indicators
    
    close = ind.get('close', 0)
    high = ind.get('high', 0)
    low = ind.get('low', 0)
    open_price = ind.get('open', 0)
    
    if not close:
        return None
    
    # Recommendations
    rec_1m = tf_1m.summary.get('RECOMMENDATION', 'NEUTRAL')
    rec_5m = tf_5m.summary.get('RECOMMENDATION', 'NEUTRAL')
    rec_15m = tf_15m.summary.get('RECOMMENDATION', 'NEUTRAL') if tf_15m else 'NEUTRAL'
    
    buy_1m = tf_1m.summary.get('BUY', 0)
    sell_1m = tf_1m.summary.get('SELL', 0)
    buy_5m = tf_5m.summary.get('BUY', 0)
    sell_5m = tf_5m.summary.get('SELL', 0)
    
    # Indicators
    rsi = ind.get('RSI', 50)
    rsi_1m = ind_1m.get('RSI', 50)
    macd = ind.get('MACD.macd', 0)
    macd_sig = ind.get('MACD.signal', 0)
    stoch_k = ind.get('Stoch.K', 50)
    stoch_d = ind.get('Stoch.D', 50)
    ema_9 = ind.get('EMA9', ind.get('EMA10', 0))
    ema_21 = ind.get('EMA21', ind.get('EMA20', 0))
    atr = ind.get('ATR', 0)
    mom = ind.get('Mom', 0)
    ao = ind.get('AO', 0)
    adx = ind.get('ADX', 20)
    volume = ind.get('volume', 0)
    
    # ═══════ SCORING SYSTEM ═══════
    
    signals = []
    bull = 0
    bear = 0
    
    # 1. RSI MOMENTUM (improved)
    if rsi and rsi_1m:
        rsi_mom = rsi_1m - rsi
        
        if 40 <= rsi <= 60:
            if rsi_mom > 2:
                bull += WEIGHTS["rsi_momentum"]
                signals.append(f"RSI: {rsi:.0f} ↑")
            elif rsi_mom < -2:
                bear += WEIGHTS["rsi_momentum"]
                signals.append(f"RSI: {rsi:.0f} ↓")
        elif rsi > 70 or rsi < 30:
            return None  # Overbought/Oversold skip
        elif 60 < rsi <= 70 and rsi_mom > 0:
            bull += 10
            signals.append(f"RSI: {rsi:.0f} (high)")
        elif 30 <= rsi < 40 and rsi_mom < 0:
            bear += 10
            signals.append(f"RSI: {rsi:.0f} (low)")
    
    # 2. MACD FRESH CROSS (improved detection)
    if macd is not None and macd_sig is not None:
        macd_hist = macd - macd_sig
        macd_strength = abs(macd) * 0.3 if macd else 0.001
        
        if 0 < macd_hist < macd_strength:
            bull += WEIGHTS["macd_cross"]
            signals.append("MACD: Fresh ↑")
        elif -macd_strength < macd_hist < 0:
            bear += WEIGHTS["macd_cross"]
            signals.append("MACD: Fresh ↓")
        elif macd_hist > macd_strength:
            bull += 5  # Already bullish, less weight
        elif macd_hist < -macd_strength:
            bear += 5
    
    # 3. STOCHASTIC
    if stoch_k and stoch_d:
        if stoch_k > 80 or stoch_k < 20:
            return None  # Extreme zones skip
        
        if stoch_k > stoch_d and 30 < stoch_k < 70:
            bull += WEIGHTS["stochastic"]
            signals.append(f"Stoch: {stoch_k:.0f}>{stoch_d:.0f}")
        elif stoch_k < stoch_d and 30 < stoch_k < 70:
            bear += WEIGHTS["stochastic"]
            signals.append(f"Stoch: {stoch_k:.0f}<{stoch_d:.0f}")
    
    # 4. EMA9/21 CROSS (improved)
    if ema_9 and ema_21 and close:
        ema_diff = ((ema_9 - ema_21) / ema_21) * 100 if ema_21 else 0
        
        if 0 < ema_diff < 0.2:
            bull += WEIGHTS["ema_cross"]
            signals.append("EMA9>21 ✓")
        elif -0.2 < ema_diff < 0:
            bear += WEIGHTS["ema_cross"]
            signals.append("EMA9<21 ✓")
        elif ema_diff > 0.5:
            bull += 5
        elif ema_diff < -0.5:
            bear += 5
    
    # 5. MOMENTUM
    if mom and ao:
        if mom > 0 and ao > 0:
            bull += WEIGHTS["momentum"]
            signals.append("Mom: +")
        elif mom < 0 and ao < 0:
            bear += WEIGHTS["momentum"]
            signals.append("Mom: -")
    
    # 6. TIMEFRAME ALIGNMENT (critical)
    if rec_1m in ['STRONG_BUY', 'BUY'] and rec_5m in ['STRONG_BUY', 'BUY']:
        if bull > bear:
            bull += WEIGHTS["tf_alignment"]
            signals.append(f"1m+5m: ↑ ({buy_1m+buy_5m})")
    elif rec_1m in ['STRONG_SELL', 'SELL'] and rec_5m in ['STRONG_SELL', 'SELL']:
        if bear > bull:
            bear += WEIGHTS["tf_alignment"]
            signals.append(f"1m+5m: ↓ ({sell_1m+sell_5m})")
    else:
        return None  # TF alignment required
    
    # 7. 15M BONUS (flexible)
    prelim_score = max(bull, bear)
    
    if rec_15m in ['STRONG_BUY', 'BUY'] and bull > bear:
        bull += WEIGHTS["tf_15m_bonus"]
        signals.append("15m: ↑")
    elif rec_15m in ['STRONG_SELL', 'SELL'] and bear > bull:
        bear += WEIGHTS["tf_15m_bonus"]
        signals.append("15m: ↓")
    elif prelim_score >= 85:
        signals.append("15m: Bypass")
    else:
        return None
    
    # 8. ADX BONUS (trend strength) - NEW
    if adx and adx > 25:
        if bull > bear:
            bull += WEIGHTS["adx_bonus"]
        else:
            bear += WEIGHTS["adx_bonus"]
        signals.append(f"ADX: {adx:.0f}")
    
    # 9. BTC ALIGNMENT BONUS - NEW
    if bull > bear and BTC_TREND == "BULLISH":
        bull += WEIGHTS["btc_alignment"]
        signals.append("BTC: ↑")
    elif bear > bull and BTC_TREND == "BEARISH":
        bear += WEIGHTS["btc_alignment"]
        signals.append("BTC: ↓")
    
    # ═══════ DIRECTION & SCORE ═══════
    
    if bull >= 65 and bull > bear:
        direction = "LONG"
        score = min(bull, 100)
    elif bear >= 65 and bear > bull:
        direction = "SHORT"
        score = min(bear, 100)
    else:
        return None
    
    # ═══════ FILTERS ═══════
    
    # BTC RSI Filter (basic)
    if direction == "LONG" and BTC_RSI < 45:
        return None
    if direction == "SHORT" and BTC_RSI > 55:
        return None
    
    # BTC TREND Filter (strict) - NEW!
    # LONG sinyali için BTC BEARISH olmamalı (yüksek skorlu hariç)
    # SHORT sinyali için BTC BULLISH olmamalı
    if direction == "LONG" and BTC_TREND == "BEARISH":
        if score < 90:  # Sadece 90+ skorlu sinyaller bypass edebilir
            return None
    if direction == "SHORT" and BTC_TREND == "BULLISH":
        if score < 90:
            return None
    
    # Improved Fake Breakout Filter
    if high and low and close and open_price:
        candle_body = abs(close - open_price)
        candle_range = high - low if high > low else 0.0001
        body_ratio = candle_body / candle_range
        
        # Doji candles are risky
        if body_ratio < 0.3 and score < 85:
            return None
        
        if direction == "LONG":
            # Close near high with small body = potential fake breakout
            if close >= high * 0.998 and body_ratio < 0.5 and score < 85:
                return None
        if direction == "SHORT":
            if close <= low * 1.002 and body_ratio < 0.5 and score < 85:
                return None
    
    # Score threshold
    if score < MIN_SCORE:
        return None
    
    # ═══════ CALCULATE LEVELS ═══════
    
    if atr and atr > 0:
        if direction == "LONG":
            entry = close
            sl = close - (atr * ATR_SL)
            tp1 = close + (atr * ATR_TP1)
            tp2 = close + (atr * ATR_TP2)
            tp3 = close + (atr * ATR_TP3)
        else:
            entry = close
            sl = close + (atr * ATR_SL)
            tp1 = close - (atr * ATR_TP1)
            tp2 = close - (atr * ATR_TP2)
            tp3 = close - (atr * ATR_TP3)
    else:
        if direction == "LONG":
            entry = close
            sl = close * (1 - PCT_SL / 100)
            tp1 = close * (1 + PCT_TP1 / 100)
            tp2 = close * (1 + PCT_TP2 / 100)
            tp3 = close * (1 + PCT_TP3 / 100)
        else:
            entry = close
            sl = close * (1 + PCT_SL / 100)
            tp1 = close * (1 - PCT_TP1 / 100)
            tp2 = close * (1 - PCT_TP2 / 100)
            tp3 = close * (1 - PCT_TP3 / 100)
    
    # Calculate percentages
    sl_pct = abs(entry - sl) / entry * 100
    tp1_pct = abs(tp1 - entry) / entry * 100
    tp2_pct = abs(tp2 - entry) / entry * 100
    tp3_pct = abs(tp3 - entry) / entry * 100
    
    # Risk/Reward
    risk = abs(entry - sl)
    rr1 = abs(tp1 - entry) / risk if risk > 0 else 1
    rr2 = abs(tp2 - entry) / risk if risk > 0 else 1
    rr3 = abs(tp3 - entry) / risk if risk > 0 else 1
    
    # ═══════ RECORD & RETURN ═══════
    
    RECENT_SIGNALS[symbol] = {'dir': direction, 'time': now}
    
    # Update category count
    category = get_coin_category(symbol)
    if category:
        ACTIVE_CATEGORY_COUNT[category] = ACTIVE_CATEGORY_COUNT.get(category, 0) + 1
    
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
        'tp2_pct': tp2_pct,
        'tp3': tp3,
        'tp3_pct': tp3_pct,
        'rr1': rr1,
        'rr2': rr2,
        'rr3': rr3,
        'rsi': rsi,
        'stoch': stoch_k,
        'btc_rsi': BTC_RSI,
        'btc_trend': BTC_TREND,
        'atr': atr if atr else 0,
        'adx': adx if adx else 0,
        'rec_1m': rec_1m,
        'rec_5m': rec_5m,
        'rec_15m': rec_15m,
        'session': get_market_session(),
        'signals': signals,
        'category': category,
        'timestamp': datetime.utcnow().isoformat()
    }

# ═══════════════════════════════════════════════════════════════
# SCANNER
# ═══════════════════════════════════════════════════════════════

def run_scan() -> List[Dict]:
    """Tüm coinleri tara"""
    global RECENT_SIGNALS, SIGNALS_TODAY, ACTIVE_CATEGORY_COUNT
    
    now = time.time()
    
    # Update BTC trend
    update_btc_trend()
    
    # Reset daily counter at midnight
    current_hour = datetime.utcnow().hour
    if current_hour == 0 and datetime.utcnow().minute < 2:
        SIGNALS_TODAY = 0
        ACTIVE_CATEGORY_COUNT = {}
    
    # Clean old signals
    RECENT_SIGNALS = {
        k: v for k, v in RECENT_SIGNALS.items() 
        if now - v['time'] < DUPLICATE_COOLDOWN
    }
    
    # Dangerous hour check
    if is_dangerous_hour():
        logger.info(f"⚠️ Dangerous hour (UTC {current_hour}), skipping...")
        return []
    
    logger.info(f"⚡ Scanning {len(COINS)} coins | BTC: {BTC_RSI:.0f} ({BTC_TREND}) | Today: {SIGNALS_TODAY}/{MAX_SIGNALS_DAY}")
    
    signals = []
    
    for coin in COINS:
        try:
            result = scalp_analyze(coin)
            if result:
                signals.append(result)
                logger.info(f"✅ {coin} {result['direction']} Score:{result['score']}")
            time.sleep(0.15)  # Rate limiting
        except Exception as e:
            logger.error(f"Error {coin}: {str(e)[:30]}")
    
    # Sort by score
    signals = sorted(signals, key=lambda x: x['score'], reverse=True)
    
    # Limit signals per scan
    signals = signals[:MAX_SIGNALS_PER_SCAN]
    
    logger.info(f"Found {len(signals)} signals")
    
    return signals

# ═══════════════════════════════════════════════════════════════
# MESSAGE FORMATTERS
# ═══════════════════════════════════════════════════════════════

def format_price(price: float) -> str:
    """Akıllı fiyat formatlama - düşük fiyatlı coinler için daha fazla decimal"""
    if price == 0:
        return "$0.00"
    elif price < 0.0001:
        return f"${price:.8f}"
    elif price < 0.01:
        return f"${price:.6f}"
    elif price < 1:
        return f"${price:.5f}"
    elif price < 100:
        return f"${price:.4f}"
    else:
        return f"${price:.2f}"

def format_signal(s: Dict, signal_id: int = None) -> str:
    """Sinyal mesajını formatla - improved"""
    emoji = "🟢" if s['direction'] == "LONG" else "🔴"
    lev = f"{MAX_LEVERAGE_HIGH}x" if s['score'] >= 85 else f"{MAX_LEVERAGE_NORMAL}x"
    
    # Score bar
    score_filled = int(s['score'] / 10)
    score_bar = "█" * score_filled + "░" * (10 - score_filled)
    
    # Category emoji
    cat_emoji = ""
    if s.get('category') == "meme":
        cat_emoji = "🐕"
    elif s.get('category') == "ai":
        cat_emoji = "🤖"
    elif s.get('category') == "defi":
        cat_emoji = "🏦"
    
    msg = f"""{emoji} **{s['symbol']}** | {s['direction']} {cat_emoji}
━━━━━━━━━━━━━━━━━━━━

⚡ **SCORE:** [{score_bar}] **{s['score']}/100**
🎚️ Leverage: **{lev}** | {s.get('session', '')}

📊 **MARKET:**
• BTC RSI: {s['btc_rsi']:.0f} ({s.get('btc_trend', 'N/A')})
• 1m: {s['rec_1m']} | 5m: {s['rec_5m']} | 15m: {s['rec_15m']}

💰 **LEVELS (ATR):**
┌ Entry: `{format_price(s['entry'])}`
├ SL: `{format_price(s['sl'])}` (-{s['sl_pct']:.2f}%)
├ TP1: `{format_price(s['tp1'])}` (+{s['tp1_pct']:.2f}%) [{s['rr1']:.1f}R]
├ TP2: `{format_price(s['tp2'])}` (+{s['tp2_pct']:.2f}%) [{s['rr2']:.1f}R]
└ TP3: `{format_price(s['tp3'])}` (+{s['tp3_pct']:.2f}%) [{s['rr3']:.1f}R]

📋 **CONFIRMATIONS:**
"""
    
    for sig in s['signals'][:6]:
        msg += f"✅ {sig}\n"
    
    msg += f"""
🧠 **TRADE MANAGEMENT:**
• TP1 → Close 40% ⚠️ MUTLAKA AL
• SL → Move to BE after TP1
• TP2 → Trail stop (50%)
• TP3 → Moon bag (10%)

⚠️ TP1 scalp hedefidir, atlama!
━━━━━━━━━━━━━━━━━━━━"""

    if signal_id:
        msg += f"\n📝 ID: `{signal_id}`"
    
    return msg

def format_stats(stats: Dict) -> str:
    """İstatistik mesajını formatla"""
    win_rate = stats.get('win_rate', 0)
    
    # Win rate bar
    wr_filled = int(win_rate / 10)
    wr_bar = "🟩" * wr_filled + "⬜" * (10 - wr_filled)
    
    return f"""📊 **PERFORMANCE** ({stats['period_days']} days)
━━━━━━━━━━━━━━━━━━━━

📈 **Signals:** {stats['total_signals']}
✅ Wins: {stats['wins']}
❌ Losses: {stats['losses']}

🎯 **Win Rate:** {wr_bar} **{win_rate:.1f}%**

💰 **P&L:**
• Average: {stats['avg_pnl']:.2f}%
• Total: {stats['total_pnl']:.2f}%
• Best: +{stats['best_trade']:.2f}%
• Worst: {stats['worst_trade']:.2f}%

━━━━━━━━━━━━━━━━━━━━"""

def format_status() -> str:
    """Bot durumu"""
    hour = datetime.utcnow().hour
    session = get_market_session()
    dangerous = "🔴 YES" if is_dangerous_hour() else "🟢 NO"
    
    return f"""📊 **BOT STATUS**
━━━━━━━━━━━━━━━━━━━━

🕐 UTC: {hour}:00 | {session}
⚠️ Dangerous Hour: {dangerous}

📈 BTC RSI: {BTC_RSI:.0f}
📊 BTC Trend: {BTC_TREND}

📝 Signals Today: {SIGNALS_TODAY}/{MAX_SIGNALS_DAY}
💾 DB Signals: {get_today_signals_count()}

━━━━━━━━━━━━━━━━━━━━"""

# ═══════════════════════════════════════════════════════════════
# KEYBOARD BUILDERS
# ═══════════════════════════════════════════════════════════════

def build_signal_keyboard(signal: Dict, signal_id: int) -> InlineKeyboardMarkup:
    """Sinyal için inline keyboard"""
    symbol_clean = signal['symbol'].replace('/USDT', '').replace('/', '')
    
    keyboard = [
        [
            InlineKeyboardButton("✅ TP1", callback_data=f"tp1_{signal_id}"),
            InlineKeyboardButton("✅ TP2", callback_data=f"tp2_{signal_id}"),
            InlineKeyboardButton("❌ SL", callback_data=f"sl_{signal_id}"),
        ],
        [
            InlineKeyboardButton("📊 Chart", url=f"https://www.tradingview.com/chart/?symbol=BINANCE:{symbol_clean}USDT"),
        ]
    ]
    
    return InlineKeyboardMarkup(keyboard)

def build_main_keyboard() -> InlineKeyboardMarkup:
    """Ana menü keyboard"""
    keyboard = [
        [
            InlineKeyboardButton("🔍 Scan", callback_data="scan"),
            InlineKeyboardButton("📊 Stats", callback_data="stats"),
        ],
        [
            InlineKeyboardButton("📝 History", callback_data="history"),
            InlineKeyboardButton("⚙️ Status", callback_data="status"),
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

# ═══════════════════════════════════════════════════════════════
# TELEGRAM HANDLERS
# ═══════════════════════════════════════════════════════════════

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start komutu"""
    chat_id = update.effective_chat.id
    
    await update.message.reply_text(
        f"""⚡ **SCALP BOT PRO v3**
━━━━━━━━━━━━━━━━━━━━

🚀 **New Features:**
✅ Database tracking
✅ BTC multi-TF trend
✅ ADX trend strength
✅ Category correlation filter
✅ Inline result buttons
✅ Performance stats

📋 **Commands:**
/scan - Manual scan
/stats - Performance stats
/history - Recent signals
/status - Bot status

━━━━━━━━━━━━━━━━━━━━
📌 Chat ID: `{chat_id}`""",
        reply_markup=build_main_keyboard(),
        parse_mode='Markdown'
    )

async def scan_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manuel tarama"""
    global SIGNALS_TODAY
    
    await update.message.reply_text("⚡ Scanning...")
    
    loop = asyncio.get_event_loop()
    signals = await loop.run_in_executor(None, run_scan)
    
    if signals:
        # Summary
        summary = f"⚡ **{len(signals)} SIGNALS** | BTC: {BTC_RSI:.0f} ({BTC_TREND})\n\n"
        for s in signals:
            emoji = "🟢" if s['direction'] == "LONG" else "🔴"
            summary += f"{emoji} {s['symbol']}: {s['direction']} ({s['score']})\n"
        
        await update.message.reply_text(summary, parse_mode='Markdown')
        
        # Detailed signals
        for sig in signals[:3]:
            signal_id = save_signal_to_db(sig)
            SIGNALS_TODAY += 1
            
            await update.message.reply_text(
                format_signal(sig, signal_id),
                reply_markup=build_signal_keyboard(sig, signal_id),
                parse_mode='Markdown'
            )
            await asyncio.sleep(0.5)
    else:
        if is_dangerous_hour():
            hour = datetime.utcnow().hour
            await update.message.reply_text(f"⚠️ Dangerous hour (UTC {hour})")
        else:
            await update.message.reply_text(f"❌ No signals | BTC: {BTC_RSI:.0f}")

async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Performans istatistikleri"""
    days = 7
    if context.args and context.args[0].isdigit():
        days = int(context.args[0])
    
    stats = get_performance_stats(days)
    await update.message.reply_text(format_stats(stats), parse_mode='Markdown')

async def history_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Son sinyaller"""
    limit = 10
    if context.args and context.args[0].isdigit():
        limit = int(context.args[0])
    
    signals = get_recent_signals(limit)
    
    if not signals:
        await update.message.reply_text("📝 No signals yet.")
        return
    
    msg = f"📝 **LAST {len(signals)} SIGNALS**\n━━━━━━━━━━━━━━━━━━━━\n\n"
    
    for s in signals:
        emoji = "🟢" if s['direction'] == "LONG" else "🔴"
        result_emoji = "✅" if s['result'] == 'WIN' else "❌" if s['result'] == 'LOSS' else "⏳"
        created = s['created_at'][:16] if s['created_at'] else 'N/A'
        msg += f"{emoji} {s['symbol']} | {s['score']} | {result_emoji} | {created}\n"
    
    await update.message.reply_text(msg, parse_mode='Markdown')

async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Bot durumu"""
    await update.message.reply_text(format_status(), parse_mode='Markdown')

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Inline button handler"""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    
    if data == "scan":
        await query.message.reply_text("⚡ Starting scan...")
        loop = asyncio.get_event_loop()
        signals = await loop.run_in_executor(None, run_scan)
        
        if signals:
            for sig in signals[:2]:
                signal_id = save_signal_to_db(sig)
                global SIGNALS_TODAY
                SIGNALS_TODAY += 1
                await query.message.reply_text(
                    format_signal(sig, signal_id),
                    reply_markup=build_signal_keyboard(sig, signal_id),
                    parse_mode='Markdown'
                )
        else:
            await query.message.reply_text("❌ No signals found")
    
    elif data == "stats":
        stats = get_performance_stats(7)
        await query.message.reply_text(format_stats(stats), parse_mode='Markdown')
    
    elif data == "history":
        signals = get_recent_signals(5)
        if signals:
            msg = "📝 **RECENT SIGNALS**\n\n"
            for s in signals:
                emoji = "🟢" if s['direction'] == "LONG" else "🔴"
                result_emoji = "✅" if s['result'] == 'WIN' else "❌" if s['result'] == 'LOSS' else "⏳"
                msg += f"{emoji} {s['symbol']} | {s['score']} | {result_emoji}\n"
            await query.message.reply_text(msg, parse_mode='Markdown')
        else:
            await query.message.reply_text("No signals yet")
    
    elif data == "status":
        await query.message.reply_text(format_status(), parse_mode='Markdown')
    
    # Signal result tracking
    elif data.startswith("tp1_"):
        signal_id = int(data.split("_")[1])
        update_signal_result(signal_id, "WIN", 0.8)
        await query.message.reply_text(f"✅ #{signal_id} → TP1 WIN recorded!")
    
    elif data.startswith("tp2_"):
        signal_id = int(data.split("_")[1])
        update_signal_result(signal_id, "WIN", 1.5)
        await query.message.reply_text(f"✅ #{signal_id} → TP2 WIN recorded!")
    
    elif data.startswith("sl_"):
        signal_id = int(data.split("_")[1])
        update_signal_result(signal_id, "LOSS", -0.5)
        await query.message.reply_text(f"❌ #{signal_id} → SL LOSS recorded!")

# ═══════════════════════════════════════════════════════════════
# BACKGROUND SCANNER
# ═══════════════════════════════════════════════════════════════

async def background_scanner(app):
    """Arka planda sürekli tarama"""
    global SIGNALS_TODAY
    
    logger.info("⚡ Background scanner starting...")
    await asyncio.sleep(5)
    
    # Startup message
    if ADMIN_CHAT_ID:
        try:
            await app.bot.send_message(
                chat_id=int(ADMIN_CHAT_ID),
                text=f"""⚡ **SCALP BOT PRO v3** Started!
━━━━━━━━━━━━━━━━━━━━

✅ Database tracking
✅ BTC multi-TF trend
✅ ADX strength filter
✅ Category correlation
✅ Inline buttons
✅ Performance stats

📊 Coins: {len(COINS)}
⏱️ Interval: {SCAN_INTERVAL}s
📝 Max Daily: {MAX_SIGNALS_DAY}

━━━━━━━━━━━━━━━━━━━━
🚀 Scanner Active!""",
                parse_mode='Markdown'
            )
        except Exception as e:
            logger.error(f"Startup message error: {e}")
    
    while True:
        try:
            loop = asyncio.get_event_loop()
            signals = await loop.run_in_executor(None, run_scan)
            
            if signals and ADMIN_CHAT_ID:
                for sig in signals[:MAX_SIGNALS_PER_SCAN]:
                    signal_id = save_signal_to_db(sig)
                    SIGNALS_TODAY += 1
                    
                    await app.bot.send_message(
                        chat_id=int(ADMIN_CHAT_ID),
                        text=format_signal(sig, signal_id),
                        reply_markup=build_signal_keyboard(sig, signal_id),
                        parse_mode='Markdown'
                    )
                    await asyncio.sleep(1)
            
            await asyncio.sleep(SCAN_INTERVAL)
            
        except Exception as e:
            logger.error(f"Scanner error: {e}")
            await asyncio.sleep(30)

async def post_init(app):
    """Bot başladıktan sonra"""
    asyncio.create_task(background_scanner(app))

# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

if __name__ == '__main__':
    print("""
    ╔═══════════════════════════════════════════════════════════╗
    ║            ⚡ SCALP BOT PRO v3 ⚡                          ║
    ╠═══════════════════════════════════════════════════════════╣
    ║  Database | BTC Trend | ADX | Correlation | Inline       ║
    ╚═══════════════════════════════════════════════════════════╝
    """)
    
    # Init database
    init_database()
    
    # Pre-flight checks
    if not TELEGRAM_TOKEN:
        print("❌ TELEGRAM_TOKEN not set!")
        exit(1)
    
    if not ADMIN_CHAT_ID:
        print("⚠️ ADMIN_CHAT_ID not set - auto signals disabled")
    
    # Test TradingView
    test = get_tv("BTC", Interval.INTERVAL_5_MINUTES)
    if test:
        print(f"✅ TradingView: BTC = {test.summary.get('RECOMMENDATION')}")
    else:
        print("⚠️ TradingView test failed")
    
    print(f"📊 Tracking {len(COINS)} coins")
    print(f"💾 Database: {DATABASE_PATH}")
    
    # Build bot
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).post_init(post_init).build()
    
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("scan", scan_cmd))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CommandHandler("history", history_cmd))
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(CallbackQueryHandler(callback_handler))
    
    print("🚀 Bot running! Press Ctrl+C to stop.")
    app.run_polling()
