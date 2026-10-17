import os
import json
import time
import html
import logging
from datetime import datetime, timezone
from urllib.request import Request, urlopen
from urllib.parse import urlencode
import xml.etree.ElementTree as ET

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
CHANNEL = os.getenv("TELEGRAM_CHANNEL_ID", "").strip()
STATE = "state.json"
KRAKEN_API = "https://api.kraken.com/0/public/OHLC"
RSS = "https://www.coindesk.com/arc/outboundfeeds/rss/"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def fetch(url):
    req = Request(url, headers={"User-Agent": "CryptoWithShamikhBot/1.0"})
    with urlopen(req, timeout=20) as response:
        return response.read()


def get_json(url):
    return json.loads(fetch(url).decode())


def send(text):
    if not TOKEN or not CHANNEL:
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHANNEL_ID")

    referral = (
        "\n\n🔗 <b>ابدأ رحلتك في عالم الكريبتو</b>\n"
        "سجّل عبر Bybit من الرابط التالي:\n"
        '<a href="https://partner.bybit.com/b/153832">'
        "رابط التسجيل</a>\n"
        "⚠️ التداول ينطوي على مخاطر، ولا توجد أرباح مضمونة."
    )
    if "partner.bybit.com/b/153832" not in text:
        text += referral

    payload = urlencode({
        "chat_id": CHANNEL,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }).encode()

    req = Request(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urlopen(req, timeout=20) as response:
        result = json.loads(response.read().decode())
    if not result.get("ok"):
        raise RuntimeError(f"Telegram send failed: {result}")


def klines(symbol):
    pairs = {"BTCUSDT": "XBTUSD", "ETHUSDT": "ETHUSD"}
    if symbol not in pairs:
        raise ValueError(f"Unsupported symbol: {symbol}")

    params = urlencode({"pair": pairs[symbol], "interval": 60})
    data = get_json(f"{KRAKEN_API}?{params}")
    if data.get("error"):
        raise RuntimeError(f"Kraken API error: {data['error']}")

    result = data.get("result", {})
    pair_key = next((key for key in result if key != "last"), None)
    if not pair_key:
        raise RuntimeError(f"No candle data returned for {symbol}")

    # Keep close price at index 4.
    return [[c[0], c[1], c[2], c[3], c[4]] for c in result[pair_key]]


def ema(values, period):
    alpha = 2 / (period + 1)
    out = []
    for value in values:
        out.append(value if not out else alpha * value + (1 - alpha) * out[-1])
    return out


def rsi(values, period=14):
    changes = [values[i] - values[i - 1] for i in range(1, len(values))]
    gains = [max(value, 0) for value in changes]
    losses = [max(-value, 0) for value in changes]
    if len(changes) < period:
        return 50.0

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(changes)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0 if avg_gain else 50.0
    return 100 - 100 / (1 + avg_gain / avg_loss)


def analyze(symbol, name):
    # Use completed candles only; ignore the still-open candle.
    candles = klines(symbol)
    closes = [float(candle[4]) for candle in candles[:-1]]
    if len(closes) < 60:
        raise RuntimeError(f"Insufficient candles for {symbol}")

    fast_ema = ema(closes, 20)
    slow_ema = ema(closes, 50)
    rsi_value = rsi(closes)
    price = closes[-1]

    if fast_ema[-2] <= slow_ema[-2] and fast_ema[-1] > slow_ema[-1] and 45 <= rsi_value <= 68:
        signal, why = "تقاطع صاعد محتمل", "EMA20 تجاوز EMA50 صعودًا وRSI ضمن النطاق المحدد."
    elif fast_ema[-2] >= slow_ema[-2] and fast_ema[-1] < slow_ema[-1] and 32 <= rsi_value <= 55:
        signal, why = "تقاطع هابط محتمل", "EMA20 هبط أسفل EMA50 وRSI ضمن النطاق المحدد."
    elif fast_ema[-1] > slow_ema[-1]:
        signal, why = "ميل صاعد، دون تقاطع جديد", "EMA20 أعلى EMA50؛ هذه قراءة اتجاه وليست إشارة دخول."
    elif fast_ema[-1] < slow_ema[-1]:
        signal, why = "ميل هابط، دون تقاطع جديد", "EMA20 أسفل EMA50؛ هذه قراءة اتجاه وليست إشارة دخول."
    else:
        signal, why = "محايد", "لا يوجد اتجاه واضح حسب هذه القواعد."

    return name, symbol, price, rsi_value, fast_ema[-1], slow_ema[-1], signal, why


def pricefmt(value):
    return f"{value:,.2f}" if value >= 100 else f"{value:,.4f}"


def market_message(rows):
    lines = ["📊 <b>Crypto with Shamikh | تحديث السوق</b>", ""]
    for name, symbol, price, rsi_value, ema20, ema50, signal, why in rows:
        lines.extend([
            f"🪙 <b>{name} ({symbol})</b>",
            f"السعر التقريبي: <code>${pricefmt(price)}</code>",
            f"RSI(14): <code>{rsi_value:.1f}</code>",
            f"EMA20: <code>{pricefmt(ema20)}</code> | EMA50: <code>{pricefmt(ema50)}</code>",
            f"القراءة الفنية: <b>{html.escape(signal)}</b>",
            html.escape(why),
            "",
        ])
    lines.extend([
        "⏱️ الإطار الزمني: ساعة واحدة. المصدر: بيانات السوق العامة من Kraken.",
        "⚠️ تحليل آلي مبسط للتثقيف، وليس نصيحة مالية أو ضمانًا للربح. قد تكون الإشارات خاطئة.",
    ])
    return "\n".join(lines)


def read_state():
    try:
        with open(STATE, encoding="utf-8") as file:
            state = json.load(file)
            return state if isinstance(state, dict) else {"news_seen": []}
    except (FileNotFoundError, json.JSONDecodeError):
        return {"news_seen": []}


def write_state(state):
    with open(STATE, "w", encoding="utf-8") as file:
        json.dump(state, file, ensure_ascii=False, indent=2)


def news_items():
    try:
        root = ET.fromstring(fetch(RSS))
        items = []
        for item in root.findall(".//item")[:15]:
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            if title and link:
                items.append({"title": title, "link": link})
        return items
    except Exception as error:
        logging.warning("News RSS unavailable: %s", error)
        return []


def should_send_market(rows, state, now):
    # Ignore price/RSI fluctuations when technical signal labels stay the same.
    signature = "|".join(f"{row[1]}:{row[6]}" for row in rows)
    last_signature = state.get("market_last_signature")
    last_sent_at = state.get("market_last_sent_at")

    if last_signature == signature and last_sent_at:
        try:
            last_time = datetime.fromisoformat(last_sent_at)
            if last_time.tzinfo is None:
                last_time = last_time.replace(tzinfo=timezone.utc)
            elapsed = (now - last_time).total_seconds()
            if 0 <= elapsed < 5.5 * 60 * 60:
                logging.info(
                    "Skipping duplicate market update; %.1f minutes since last post",
                    elapsed / 60,
                )
                return False, signature
        except (ValueError, TypeError):
            logging.warning("Invalid previous market timestamp; sending update")

    return True, signature


def main():
    state = read_state()
    rows = [analyze("BTCUSDT", "Bitcoin"), analyze("ETHUSDT", "Ethereum")]

    now = datetime.now(timezone.utc)
    publish_market, signature = should_send_market(rows, state, now)
    if publish_market:
        send(market_message(rows))
        # Save the deduplication record only after successful Telegram delivery.
        state["market_last_signature"] = signature
        state["market_last_sent_at"] = now.isoformat()
        write_state(state)
        logging.info("Market update sent")
    else:
        logging.info("Market update skipped")

    # News processing is independent from market deduplication.
    seen = set(state.get("news_seen", []))
    news = news_items()

    # On first run, record a baseline without posting old headlines.
    if not state.get("initialized"):
        state["news_seen"] = [item["link"] for item in news[:15]]
        state["initialized"] = True
        write_state(state)
        logging.info("Initialized news baseline; no old headlines posted")
        return

    fresh = [item for item in news if item["link"] not in seen][:3]
    for item in reversed(fresh):
        title = html.escape(item["title"])
        link = html.escape(item["link"], quote=True)
        send(
            f"📰 <b>خبر كريبتو</b>\n\n{title}\n\n"
            f'المصدر: <a href="{link}">اقرأ الخبر الأصلي</a>\n\n'
            f"ℹ️ للمعلومات فقط، وليس توصية شراء أو بيع."
        )
        time.sleep(1)

    if fresh:
        state["news_seen"] = (
            state.get("news_seen", []) + [item["link"] for item in fresh]
        )[-100:]
        write_state(state)
        logging.info("Sent %d news items", len(fresh))


if __name__ == "__main__":
    main()
