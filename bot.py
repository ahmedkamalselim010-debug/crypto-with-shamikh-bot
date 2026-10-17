import os, json, time, html, logging
from urllib.request import Request, urlopen
from urllib.parse import urlencode
import xml.etree.ElementTree as ET

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
CHANNEL = os.getenv("TELEGRAM_CHANNEL_ID", "").strip()
STATE = "state.json"
API = "https://api.binance.com"
RSS = "https://www.coindesk.com/arc/outboundfeeds/rss/"
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

def fetch(url):
    req = Request(url, headers={"User-Agent": "CryptoWithShamikhBot/1.0"})
    with urlopen(req, timeout=20) as r:
        return r.read()

def get_json(url):
    return json.loads(fetch(url).decode())

def send(text):
    if not TOKEN or not CHANNEL:
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHANNEL_ID secret")
    from urllib.parse import urlencode
    data = urlencode({"chat_id": CHANNEL, "text": text, "parse_mode": "HTML",
                      "disable_web_page_preview": "true"}).encode()
    req = Request(f"https://api.telegram.org/bot{TOKEN}/sendMessage", data=data)
    result = json.loads(urlopen(req, timeout=20).read().decode())
    if not result.get("ok"):
        raise RuntimeError(f"Telegram API error: {result}")
    return result

def klines(symbol):
    url = API + "/api/v3/klines?" + urlencode({"symbol": symbol, "interval": "1h", "limit": 120})
    return get_json(url)

def ema(values, period):
    alpha = 2 / (period + 1)
    out = []
    for v in values:
        out.append(v if not out else alpha * v + (1-alpha) * out[-1])
    return out

def rsi(values, period=14):
    changes = [values[i] - values[i-1] for i in range(1, len(values))]
    gains = [max(x, 0) for x in changes]
    losses = [max(-x, 0) for x in changes]
    if len(changes) < period: return 50.0
    ag, al = sum(gains[:period])/period, sum(losses[:period])/period
    for i in range(period, len(changes)):
        ag = (ag*(period-1)+gains[i])/period
        al = (al*(period-1)+losses[i])/period
    if al == 0: return 100.0 if ag else 50.0
    return 100 - 100/(1+ag/al)

def analyze(symbol, name):
    # Public market data only. Ignore the still-open candle.
    candles = klines(symbol)
    closes = [float(c[4]) for c in candles[:-1]]
    if len(closes) < 60: raise RuntimeError(f"Insufficient candles for {symbol}")
    f, s = ema(closes, 20), ema(closes, 50)
    rv, price = rsi(closes), closes[-1]
    if f[-2] <= s[-2] and f[-1] > s[-1] and 45 <= rv <= 68:
        signal, why = "تقاطع صاعد محتمل", "EMA20 تجاوز EMA50 صعودًا وRSI ضمن النطاق المحدد."
    elif f[-2] >= s[-2] and f[-1] < s[-1] and 32 <= rv <= 55:
        signal, why = "تقاطع هابط محتمل", "EMA20 هبط أسفل EMA50 وRSI ضمن النطاق المحدد."
    elif f[-1] > s[-1]:
        signal, why = "ميل صاعد، دون تقاطع جديد", "EMA20 أعلى EMA50؛ هذه قراءة اتجاه وليست إشارة دخول."
    elif f[-1] < s[-1]:
        signal, why = "ميل هابط، دون تقاطع جديد", "EMA20 أسفل EMA50؛ هذه قراءة اتجاه وليست إشارة دخول."
    else:
        signal, why = "محايد", "لا يوجد اتجاه واضح حسب هذه القواعد."
    return name, symbol, price, rv, f[-1], s[-1], signal, why

def pricefmt(x):
    return f"{x:,.2f}" if x >= 100 else f"{x:,.4f}"

def market_message(rows):
    lines = ["📊 <b>Crypto with Shamikh | تحديث السوق</b>", ""]
    for name, sym, price, rv, e20, e50, signal, why in rows:
        lines.extend([f"🪙 <b>{name} ({sym})</b>",
            f"السعر التقريبي: <code>${pricefmt(price)}</code>",
            f"RSI(14): <code>{rv:.1f}</code>",
            f"EMA20: <code>{pricefmt(e20)}</code> | EMA50: <code>{pricefmt(e50)}</code>",
            f"القراءة الفنية: <b>{html.escape(signal)}</b>",
            html.escape(why), ""])
    lines.extend(["⏱️ الإطار الزمني: ساعة واحدة. المصدر: بيانات السوق العامة من Binance.",
        "⚠️ تحليل آلي مبسط للتثقيف، وليس نصيحة مالية أو ضمانًا للربح. قد تكون الإشارات خاطئة."])
    return "\n".join(lines)

def read_state():
    try:
        with open(STATE, encoding="utf-8") as f: return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError): return {"news_seen": []}

def write_state(state):
    with open(STATE, "w", encoding="utf-8") as f: json.dump(state, f, ensure_ascii=False, indent=2)

def news_items():
    try:
        root = ET.fromstring(fetch(RSS))
        out = []
        for item in root.findall(".//item")[:15]:
            title, link = (item.findtext("title") or "").strip(), (item.findtext("link") or "").strip()
            if title and link: out.append({"title": title, "link": link})
        return out
    except Exception as e:
        logging.warning("News RSS unavailable: %s", e)
        return []

def main():
    state = read_state()
    rows = [analyze("BTCUSDT", "Bitcoin"), analyze("ETHUSDT", "Ethereum")]
    send(market_message(rows))
    logging.info("Market update sent")
    seen = set(state.get("news_seen", []))
    news = news_items()
    # On first run, establish a baseline and avoid flooding the channel with old headlines.
    if not state.get("initialized"):
        state["news_seen"] = [n["link"] for n in news[:15]]
        state["initialized"] = True
        write_state(state)
        logging.info("Initialized news baseline; no old headlines posted")
        return
    fresh = [n for n in news if n["link"] not in seen][:3]
    for n in reversed(fresh):
        title = html.escape(n["title"])
        link = html.escape(n["link"], quote=True)
        send(f"📰 <b>خبر كريبتو</b>\n\n{title}\n\nالمصدر: <a href=\"{link}\">اقرأ الخبر الأصلي</a>\n\nℹ️ للمعلومات فقط، وليس توصية شراء أو بيع.")
        time.sleep(1)
    if fresh:
        state["news_seen"] = (state.get("news_seen", []) + [n["link"] for n in fresh])[-100:]
        write_state(state)
        logging.info("Sent %d news items", len(fresh))

if __name__ == "__main__":
    main()
