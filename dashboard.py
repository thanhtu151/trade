import streamlit as st
import plotly.graph_objects as go
import finnhub
import yfinance as yf
import time
import requests
from datetime import datetime, timedelta
import pandas as pd

st.set_page_config(page_title="Stock Dashboard", layout="wide", page_icon="📈")

FINNHUB_KEY = "d8jrao1r01qh6g3s1pf0d8jrao1r01qh6g3s1pfg"
client = finnhub.Client(api_key=FINNHUB_KEY)

st.title("📈 Stock Dashboard")

symbol = st.sidebar.text_input("Mã cổ phiếu", value="AAPL").upper()
interval = st.sidebar.selectbox("Khung thời gian", ["1d", "5d", "1mo", "3mo"], index=2)
refresh_sec = st.sidebar.slider("Tự động refresh (giây)", 10, 120, 30)

# --- Giá realtime ---
quote = client.quote(symbol)
col1, col2, col3, col4 = st.columns(4)
col1.metric("Giá hiện tại", f"${quote['c']:.2f}", f"{quote['d']:+.2f} ({quote['dp']:+.2f}%)")
col2.metric("Cao nhất", f"${quote['h']:.2f}")
col3.metric("Thấp nhất", f"${quote['l']:.2f}")
col4.metric("Mở cửa", f"${quote['o']:.2f}")
st.caption(f"🕐 Cập nhật lúc: {datetime.now().strftime('%H:%M:%S')}")

st.divider()

# --- Chart ---
period_map = {"1d": "1d", "5d": "5d", "1mo": "1mo", "3mo": "3mo"}
intraday_map = {"1d": "5m", "5d": "15m", "1mo": "1d", "3mo": "1d"}
df = yf.download(symbol, period=period_map[interval], interval=intraday_map[interval], progress=False)

if not df.empty:
    fig = go.Figure(data=[go.Candlestick(
        x=df.index,
        open=df["Open"].squeeze(),
        high=df["High"].squeeze(),
        low=df["Low"].squeeze(),
        close=df["Close"].squeeze(),
        increasing_line_color="#00ff88",
        decreasing_line_color="#ff4444"
    )])
    fig.update_layout(
        title=f"{symbol}",
        template="plotly_dark",
        height=500,
        xaxis_rangeslider_visible=False,
        margin=dict(l=0, r=0, t=40, b=0)
    )
    st.plotly_chart(fig, use_container_width=True)

st.divider()

# --- AI Dự đoán ---
st.subheader("🤖 AI Dự đoán")

def get_technical_summary(df, quote):
    close = df["Close"].squeeze()
    sma20 = close.rolling(20).mean().iloc[-1]
    sma50 = close.rolling(50).mean().iloc[-1]
    rsi_delta = close.diff()
    gain = rsi_delta.clip(lower=0).rolling(14).mean().iloc[-1]
    loss = (-rsi_delta.clip(upper=0)).rolling(14).mean().iloc[-1]
    rsi = 100 - (100 / (1 + gain / loss)) if loss != 0 else 50
    return {
        "price": quote["c"],
        "change_pct": quote["dp"],
        "sma20": round(float(sma20), 2),
        "sma50": round(float(sma50), 2),
        "rsi": round(float(rsi), 2),
        "high": quote["h"],
        "low": quote["l"],
    }

def ask_ollama(prompt):
    try:
        resp = requests.post("http://localhost:11434/api/generate", json={
            "model": "qwen2.5:14b",
            "prompt": prompt,
            "stream": False
        }, timeout=120)
        return resp.json()["response"]
    except Exception as e:
        return f"Lỗi kết nối Ollama: {e}"

col_ai1, col_ai2 = st.columns([1, 2])

with col_ai1:
    if st.button("🔍 Phân tích ngay", use_container_width=True):
        with st.spinner("AI đang phân tích..."):
            df_analysis = yf.download(symbol, period="3mo", interval="1d", progress=False)
            tech = get_technical_summary(df_analysis, quote)

            news = client.company_news(
                symbol,
                _from=(datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d"),
                to=datetime.now().strftime("%Y-%m-%d")
            )
            news_text = "\n".join([f"- {a['headline']}" for a in news[:5]])

            prompt = f"""Bạn là chuyên gia phân tích chứng khoán. Phân tích cổ phiếu {symbol} và đưa ra khuyến nghị ngắn gọn bằng tiếng Việt.

Dữ liệu kỹ thuật:
- Giá hiện tại: ${tech['price']}
- Thay đổi hôm nay: {tech['change_pct']:+.2f}%
- SMA20: ${tech['sma20']}
- SMA50: ${tech['sma50']}
- RSI(14): {tech['rsi']}
- Cao nhất hôm nay: ${tech['high']}
- Thấp nhất hôm nay: ${tech['low']}

Tin tức gần đây:
{news_text}

Hãy trả lời theo format:
1. **Xu hướng**: (Tăng/Giảm/Sideway)
2. **Khuyến nghị**: (MUA / BÁN / GIỮ)
3. **Nên vào lệnh khi**: (điều kiện cụ thể)
4. **Mục tiêu chốt lời**: (giá mục tiêu)
5. **Cắt lỗ tại**: (giá cắt lỗ)
6. **Thời gian nắm giữ**: (bao lâu)
7. **Lý do**: (ngắn gọn 2-3 câu)"""

            result = ask_ollama(prompt)
            st.session_state["ai_result"] = result
            st.session_state["ai_time"] = datetime.now().strftime("%H:%M:%S")

with col_ai2:
    if "ai_result" in st.session_state:
        st.info(f"📊 Phân tích lúc {st.session_state['ai_time']}")
        st.markdown(st.session_state["ai_result"])

st.divider()

# --- News ---
st.subheader("📰 Tin tức mới nhất")
news = client.company_news(
    symbol,
    _from=(datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d"),
    to=datetime.now().strftime("%Y-%m-%d")
)
for article in news[:8]:
    with st.expander(f"🗞️ {article['headline']}"):
        st.write(f"**Nguồn:** {article['source']} | {datetime.fromtimestamp(article['datetime']).strftime('%d/%m/%Y %H:%M')}")
        st.write(article["summary"])
        st.markdown(f"[Đọc thêm]({article['url']})")

# --- Auto refresh ---
time.sleep(refresh_sec)
st.rerun()