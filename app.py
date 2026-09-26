import hmac
import os

import pandas as pd
import requests
import streamlit as st
import yfinance as yf
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from io import StringIO

POSITIVE_NEWS_TERMS = {
    "beat", "beats", "bullish", "growth", "gain", "gains", "upgrade",
    "upgraded", "surge", "strong", "profit", "record", "outperform",
}
NEGATIVE_NEWS_TERMS = {
    "miss", "misses", "bearish", "decline", "declines", "downgrade",
    "downgraded", "slump", "weak", "loss", "lawsuit", "investigation",
    "warning", "cut", "cuts", "underperform",
}
MARKET_DATA_COLUMNS = [
    "Symbol", "Name", "Sector", "Current Price ($)", "50-Day MA ($)",
    "200-Day MA ($)", "News Sentiment", "Analyst Rating", "Analyst Opinions",
    "Analyst Score", "Average Analyst Target ($)", "Analyst Upside (%)",
    "Analyst Sentiment Summary", "Analyst Sentiment Sentence", "Market Cap ($B)",
    "P/E Ratio", "Net Margin (%)",
]

# 1. Page Configuration & Styling
st.set_page_config(page_title="AlphaScan | Custom Screener", layout="wide", page_icon="📊")
st.title("📊 AlphaScan Core Stock Screener")
st.caption("Real-time programmatic fundamental screening engine powered by Python & Streamlit")

try:
    app_password = st.secrets.get("APP_PASSWORD")
except Exception:
    app_password = None
app_password = app_password or os.getenv("APP_PASSWORD")
if app_password:
    entered_password = st.text_input("App password", type="password")
    if not hmac.compare_digest(entered_password, app_password):
        st.warning("Enter the app password to continue.")
        st.stop()

# 2. Sidebar Configuration for Filter Parameters
st.sidebar.header("🎯 Filter Settings")

def reset_filters_for_universe_change():
    if st.session_state.get("stock_universe") == "Custom tickers":
        st.session_state["max_pe"] = 150
        st.session_state["min_margin"] = 0
        st.session_state["min_market_cap"] = 0.5
    else:
        st.session_state["max_pe"] = 40
        st.session_state["min_margin"] = 15
        st.session_state["min_market_cap"] = 5.0
    st.session_state["selected_analyst_rating"] = "All ratings"
    st.session_state["selected_sector"] = "All"

stock_universe = st.sidebar.selectbox(
    "Stock Universe",
    ["S&P 500", "Nasdaq-100", "Custom tickers"],
    key="stock_universe",
    on_change=reset_filters_for_universe_change,
)
selected_symbols = st.sidebar.text_input(
    "Search stocks (optional)",
    placeholder="e.g. AAPL, Apple, MSFT",
    help="In Custom tickers, enter ticker symbols or company names separated by commas. In index universes, use this to narrow the results.",
)
analyst_rating_options = [
    "All ratings", "Strong Buy", "Buy", "Hold", "Underperform", "Sell", "Unavailable"
]
selected_analyst_rating = st.sidebar.selectbox(
    "Analyst Rating",
    analyst_rating_options,
    key="selected_analyst_rating",
    help="Filter stocks by the current Yahoo Finance analyst consensus rating.",
)

# Fundamental Value Sliders
max_pe = st.sidebar.slider("Maximum Price-to-Earnings (P/E) Ratio", min_value=5, max_value=150, value=40, key="max_pe")
min_margin = st.sidebar.slider("Minimum Net Profit Margin (%)", min_value=0, max_value=100, value=15, key="min_margin")
min_market_cap = st.sidebar.slider("Minimum Market Cap ($ Billions; $0.5B = $500M)", min_value=0.5, max_value=3000.0, value=5.0, step=0.5, key="min_market_cap")

# Sector Filtering dropdown
sectors_list = ["All", "Semiconductors and Semiconductor Equipment", "Software", "Retail", "Pharmaceuticals", "Hardware"]
selected_sector = st.sidebar.selectbox("Filter by Industry Sector", sectors_list, key="selected_sector")

# 3. S&P 500 Market Fetcher Engine
@st.cache_data(ttl=86400, show_spinner=False)
def resolve_custom_stock(search_term):
    search_term = search_term.strip()
    normalized_symbol = search_term.upper().replace(".", "-")
    try:
        quotes = yf.Search(search_term, max_results=10).quotes
    except Exception:
        quotes = []

    exact_match = next(
        (
            quote for quote in quotes
            if quote.get("symbol", "").upper().replace(".", "-") == normalized_symbol
        ),
        None,
    )
    if exact_match:
        return exact_match["symbol"], exact_match.get("shortname") or exact_match.get("longname") or search_term

    looks_like_symbol = len(search_term) <= 6 and (
        search_term.isupper() or "." in search_term or "-" in search_term
    )
    if looks_like_symbol or not quotes:
        return search_term.upper(), search_term

    best_match = quotes[0]
    return (
        best_match.get("symbol", search_term.upper()),
        best_match.get("shortname") or best_match.get("longname") or search_term,
    )


@st.cache_data(ttl=3600, show_spinner="Loading S&P 500 fundamentals...")
def load_market_universe(stock_universe, custom_symbols):
    if stock_universe == "S&P 500":
        url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
        ticker_column = "Symbol"
        security_column = "Security"
        sector_column = "GICS Sector"
    elif stock_universe == "Nasdaq-100":
        url = "https://en.wikipedia.org/wiki/List_of_NASDAQ-100_companies"
        ticker_column = "Ticker"
        security_column = "Company"
        sector_column = "ICB Industry[1]"
    else:
        custom_stocks = [
            resolve_custom_stock(search_term)
            for search_term in custom_symbols.split(",")
            if search_term.strip()
        ]
        constituents = pd.DataFrame([
            {"Symbol": symbol, "Security": name, "GICS Sector": "Custom"}
            for symbol, name in dict(custom_stocks).items()
        ], columns=["Symbol", "Security", "GICS Sector"])
        ticker_column = "Symbol"
        security_column = "Security"
        sector_column = "GICS Sector"
    if stock_universe != "Custom tickers":
        response = requests.get(
            url,
            headers={"User-Agent": "AlphaScan/1.0"},
            timeout=30,
        )
        response.raise_for_status()
        constituents = pd.read_html(StringIO(response.text))[0]
        constituents = constituents.rename(columns={ticker_column: "Symbol"})
        constituents["Security"] = constituents[security_column]
        constituents["GICS Sector"] = constituents.get(sector_column, "Unknown")

    def fetch_fundamentals(row):
        symbol = row["Symbol"].replace(".", "-")
        try:
            ticker = yf.Ticker(symbol)
            info = ticker.info
            market_cap = info.get("marketCap")
            pe_ratio = info.get("trailingPE")
            if pe_ratio is None or pe_ratio <= 0:
                forward_pe = info.get("forwardPE")
                pe_ratio = forward_pe if forward_pe and forward_pe > 0 else None
            net_margin = info.get("profitMargins")
            current_price = info.get("currentPrice") or info.get("regularMarketPrice")
            recommendation_mean = info.get("recommendationMean")
            analyst_count = info.get("numberOfAnalystOpinions") or 0
            analyst_target = info.get("targetMeanPrice")
            history = ticker.history(period="1y", auto_adjust=False)
            closing_prices = history["Close"].dropna()
            moving_average_50 = closing_prices.rolling(50).mean().iloc[-1]
            moving_average_200 = closing_prices.rolling(200).mean().iloc[-1]
            news_score = 0.0
            try:
                for article in ticker.news:
                    content = article.get("content", {}) or {}
                    title = (content.get("title") or "").lower()
                    words = set(title.replace("-", " ").split())
                    article_score = len(words & POSITIVE_NEWS_TERMS) - len(words & NEGATIVE_NEWS_TERMS)
                    published_at = content.get("pubDate")
                    weight = 1.0
                    if published_at:
                        published = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
                        age_days = max(0, (datetime.now(timezone.utc) - published).days)
                        weight = max(0.25, 1 - age_days / 30)
                    news_score += article_score * weight
            except Exception:
                news_score = 0.0
            news_score = max(-1.0, min(1.0, news_score / 3))
            analyst_score = 0.0
            if recommendation_mean is not None and analyst_count:
                analyst_score = max(-1.0, min(1.0, (3 - recommendation_mean) / 2))
            analyst_upside = None
            if analyst_target is not None and current_price:
                analyst_upside = (analyst_target / current_price - 1) * 100
            analyst_rating = (info.get("recommendationKey") or "Unavailable").replace("_", " ").title()
            if recommendation_mean is not None and analyst_count:
                analyst_summary = (
                    f"{analyst_rating} consensus ({recommendation_mean:.1f}/5, "
                    f"{analyst_count} opinions)"
                )
                if analyst_rating in {"Strong Buy", "Buy"}:
                    sentiment_word = "bullish"
                elif analyst_rating in {"Sell", "Underperform"}:
                    sentiment_word = "bearish"
                else:
                    sentiment_word = "neutral"
                analyst_sentence = (
                    f"Analysts are broadly {sentiment_word}, with a {analyst_rating} "
                    f"consensus from {analyst_count} opinions."
                )
            else:
                analyst_summary = "Unavailable"
                analyst_sentence = "Analyst sentiment is unavailable."
            if not all(value is not None for value in (market_cap, net_margin, current_price)):
                return None
            if pd.isna(moving_average_50) or pd.isna(moving_average_200):
                return None
            return (
                {
                    "Symbol": row["Symbol"],
                    "Name": info.get("longName", row[security_column]),
                    "Sector": row[sector_column],
                    "Current Price ($)": current_price,
                    "50-Day MA ($)": moving_average_50,
                    "200-Day MA ($)": moving_average_200,
                    "News Sentiment": news_score,
                    "Analyst Rating": analyst_rating,
                    "Analyst Opinions": analyst_count,
                    "Analyst Score": analyst_score,
                    "Average Analyst Target ($)": analyst_target,
                    "Analyst Upside (%)": analyst_upside,
                    "Analyst Sentiment Summary": analyst_summary,
                    "Analyst Sentiment Sentence": analyst_sentence,
                    "Market Cap ($B)": market_cap / 1_000_000_000,
                    "P/E Ratio": pe_ratio,
                    "Net Margin (%)": net_margin * 100,
                },
                history[["Close"]].rename(columns={"Close": row["Symbol"]}),
            )
        except Exception:
            return None

    records = []
    price_histories = {}
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(fetch_fundamentals, row) for _, row in constituents.iterrows()]
        for future in as_completed(futures):
            result = future.result()
            if result is not None:
                record, history = result
                records.append(record)
                price_histories[record["Symbol"]] = history

    return pd.DataFrame(records, columns=MARKET_DATA_COLUMNS), price_histories


@st.cache_data(ttl=900, show_spinner=False)
def load_intraday_price_history(symbol, period, interval):
    try:
        history = yf.Ticker(symbol.replace(".", "-")).history(
            period=period,
            interval=interval,
            auto_adjust=False,
        )
        if history.empty or "Close" not in history:
            return pd.DataFrame()
        return history[["Close"]].rename(columns={"Close": symbol})
    except Exception:
        return pd.DataFrame()


df, price_histories = load_market_universe(stock_universe, selected_symbols)

if df.empty:
    if stock_universe == "Custom tickers" and not selected_symbols.strip():
        st.warning("Enter one or more ticker symbols when using the Custom tickers universe.")
    else:
        st.warning("No usable market data was returned. Try refreshing or selecting another stock universe.")
    st.stop()

# 4. Processing the Reactive Filter Logic Engine
filtered_df = df[
    (df["P/E Ratio"].fillna(max_pe) <= max_pe) & 
    (df["Net Margin (%)"] >= min_margin) & 
    (df["Market Cap ($B)"] >= min_market_cap)
]

symbols = {
    symbol.strip().upper().replace(".", "-")
    for symbol in selected_symbols.split(",")
    if symbol.strip()
}
if symbols:
    if stock_universe != "Custom tickers":
        filtered_df = filtered_df[
            filtered_df["Symbol"].str.upper().str.replace(".", "-", regex=False).isin(symbols)
        ]

if selected_analyst_rating != "All ratings":
    filtered_df = filtered_df[filtered_df["Analyst Rating"] == selected_analyst_rating]

if selected_sector != "All":
    filtered_df = filtered_df[filtered_df["Sector"] == selected_sector]

if symbols and stock_universe != "Custom tickers":
    visible_symbols = set(filtered_df["Symbol"].str.upper().str.replace(".", "-", regex=False))
    explanations = []
    for symbol in sorted(symbols - visible_symbols):
        matching_rows = df[df["Symbol"].str.upper().str.replace(".", "-", regex=False) == symbol]
        if matching_rows.empty:
            explanations.append(f"{symbol}: market data was unavailable.")
            continue
        stock = matching_rows.iloc[0]
        reasons = []
        if stock["Net Margin (%)"] < min_margin:
            reasons.append(f"net margin {stock['Net Margin (%)']:.1f}% is below {min_margin}%")
        if stock["Market Cap ($B)"] < min_market_cap:
            reasons.append(f"market cap ${stock['Market Cap ($B)']:.1f}B is below ${min_market_cap:.1f}B")
        if pd.notna(stock["P/E Ratio"]) and stock["P/E Ratio"] > max_pe:
            reasons.append(f"P/E {stock['P/E Ratio']:.1f} is above {max_pe}")
        if selected_analyst_rating != "All ratings" and stock["Analyst Rating"] != selected_analyst_rating:
            reasons.append(f"analyst rating is {stock['Analyst Rating']}")
        if selected_sector != "All" and stock["Sector"] != selected_sector:
            reasons.append(f"sector is {stock['Sector']}")
        explanations.append(f"{symbol}: {', '.join(reasons) or 'excluded by the active filters.'}.")
    if explanations:
        st.info("Not shown: " + " ".join(explanations))

# Rank quality and valuation together: higher margins at a lower P/E imply more upside potential.
filtered_df = filtered_df.copy()
trend_factor = (
    (filtered_df["Current Price ($)"] / filtered_df["50-Day MA ($)"])
    + (filtered_df["Current Price ($)"] / filtered_df["200-Day MA ($)"])
) / 2
filtered_df["Upside Score"] = (
    filtered_df["Net Margin (%)"] / filtered_df["P/E Ratio"].fillna(max_pe) * 100 * trend_factor
    * (1 + 0.2 * filtered_df["News Sentiment"])
    * (1 + 0.15 * filtered_df["Analyst Score"])
    * (1 + 0.15 * filtered_df["Analyst Upside (%)"].fillna(0).clip(-100, 100) / 100)
).round(1)
priority_columns = [
    "Upside Score",
    "Symbol",
    "Name",
    "Analyst Rating",
    "Average Analyst Target ($)",
]
remaining_columns = [
    column for column in filtered_df.columns
    if column not in priority_columns and column != "Sector"
]
filtered_df = filtered_df[priority_columns + remaining_columns + ["Sector"]]
filtered_df = filtered_df.sort_values("Upside Score", ascending=False)
filtered_df = filtered_df.reset_index(drop=True)

page_sizes = [10, 20, 50]
if "results_page_size" not in st.session_state:
    st.session_state["results_page_size"] = 10
filter_signature = (
    tuple(filtered_df["Symbol"].tolist()),
    st.session_state["results_page_size"],
)
if st.session_state.get("results_filter_signature") != filter_signature:
    st.session_state["results_page"] = 0
    st.session_state["selected_stock_rows"] = []
    st.session_state["results_filter_signature"] = filter_signature

# 5. UI Layout Display
col1, col2 = st.columns([1, 3])

with col1:
    st.metric(label="Total Universe", value=len(df))
with col2:
    st.metric(label="Matches Found", value=len(filtered_df), delta=int(len(filtered_df) - len(df)))

st.subheader("🔍 Filtered Candidates Matrix")
if not filtered_df.empty:
    total_results = len(filtered_df)
    page_size = st.session_state["results_page_size"]
    page_count = (total_results + page_size - 1) // page_size
    current_page = min(st.session_state.get("results_page", 0), page_count - 1)
    start = current_page * page_size
    end = min(start + page_size, total_results)

    selected_rows = [
        row for row in st.session_state.get("selected_stock_rows", [])
        if 0 <= row < total_results
    ]
    if selected_rows != st.session_state.get("selected_stock_rows", []):
        st.session_state["selected_stock_rows"] = selected_rows
    selected_stock_index = selected_rows[0] if selected_rows else None
    visible_columns = list(
        filtered_df.columns[:filtered_df.columns.get_loc("Current Price ($)") + 1]
    )
    detail_columns = [column for column in filtered_df.columns if column not in visible_columns]

    def format_results(dataframe):
        return dataframe.style.format({
            "Current Price ($)": "${:,.2f}",
            "50-Day MA ($)": "${:,.2f}",
            "200-Day MA ($)": "${:,.2f}",
            "News Sentiment": "{:+.2f}",
            "Analyst Score": "{:+.2f}",
            "Average Analyst Target ($)": "${:,.2f}",
            "Analyst Upside (%)": "{:+.1f}%",
            "Market Cap ($B)": "${:,.1f}B",
            "P/E Ratio": "{:.1f}x",
            "Net Margin (%)": "{:.1f}%",
            "Upside Score": "{:.1f}"
        }, na_rep="Unavailable")

    if selected_stock_index is not None:
        selected_stock = filtered_df.iloc[[selected_stock_index]]
        st.dataframe(
            format_results(selected_stock[visible_columns]),
            key=f"selected_stock_{selected_stock.iloc[0]['Symbol']}",
            width="stretch",
            height=120,
        )
        st.markdown("**Selected Stock Details**")
        detail_rows = []
        for column in detail_columns:
            value = selected_stock.iloc[0][column]
            if pd.isna(value):
                value = "Unavailable"
            elif column == "Average Analyst Target ($)":
                value = f"${value:,.2f}"
            elif column == "Market Cap ($B)":
                value = f"${value:,.1f}B"
            elif column == "Analyst Upside (%)":
                value = f"{value:+.1f}%"
            elif column in {"50-Day MA ($)", "200-Day MA ($)"}:
                value = f"${value:,.2f}"
            elif column == "News Sentiment":
                value = f"{value:+.2f}"
            elif column == "Analyst Score":
                value = f"{value:+.2f}"
            elif column == "P/E Ratio":
                value = f"{value:.1f}x"
            elif column == "Net Margin (%)":
                value = f"{value:.1f}%"
            detail_rows.append({"Detail": column, "Value": value})
        st.dataframe(pd.DataFrame(detail_rows), hide_index=True, width="stretch")
        if st.button("Show all stocks", use_container_width=True):
            st.session_state["selected_stock_rows"] = []
            st.session_state["stock_table_revision"] = st.session_state.get("stock_table_revision", 0) + 1
            st.rerun()
    else:
        page_df = filtered_df.iloc[start:end]
        revision = st.session_state.get("stock_table_revision", 0)
        selection = st.dataframe(
            format_results(page_df[visible_columns]),
            key=f"stock_results_{current_page}_{page_size}_{revision}",
            on_select="rerun",
            selection_mode="single-row",
            width="stretch",
            height=min(640, 46 + page_size * 35),
        )
        current_page_selection = list(selection.selection.rows)
        next_selected_rows = [start + current_page_selection[0]] if current_page_selection else []
        if next_selected_rows != selected_rows:
            st.session_state["selected_stock_rows"] = next_selected_rows
            st.session_state["stock_table_revision"] = revision + 1
            st.rerun()

        previous_col, range_col, size_col, next_col = st.columns([1, 2, 1, 1])
        with previous_col:
            if st.button("← Previous", disabled=current_page == 0, use_container_width=True):
                st.session_state["results_page"] = current_page - 1
                st.rerun()
        with range_col:
            st.markdown(
                f"<div style='text-align:center;padding:.45rem 0'>{start + 1}–{end} of {total_results} · Page {current_page + 1} of {page_count}</div>",
                unsafe_allow_html=True,
            )
        with size_col:
            st.selectbox(
                "Rows per page",
                page_sizes,
                key="results_page_size",
                format_func=lambda size: f"{size} per page",
                label_visibility="collapsed",
            )
        with next_col:
            if st.button("Next →", disabled=current_page >= page_count - 1, use_container_width=True):
                st.session_state["results_page"] = current_page + 1
                st.rerun()
    
    if selected_rows:
        selected_stocks = filtered_df.iloc[[row for row in selected_rows if row < len(filtered_df)]]
        chart_range = st.selectbox(
            "Price chart range",
            ["1 day", "1 week", "1 month", "3 months", "6 months", "1 year"],
            index=5,
            key="price_chart_range",
        )
        intraday_ranges = {
            "1 day": ("1d", "5m"),
            "1 week": ("5d", "30m"),
        }
        daily_range_days = {
            "1 month": 30,
            "3 months": 90,
            "6 months": 180,
            "1 year": 365,
        }
        if chart_range in intraday_ranges:
            period, interval = intraday_ranges[chart_range]
            histories = [
                load_intraday_price_history(stock["Symbol"], period, interval)
                for _, stock in selected_stocks.iterrows()
            ]
        else:
            days = daily_range_days[chart_range]
            histories = []
            for _, stock in selected_stocks.iterrows():
                history = price_histories.get(stock["Symbol"], pd.DataFrame())
                if not history.empty:
                    cutoff = history.index.max() - pd.Timedelta(days=days)
                    history = history.loc[history.index >= cutoff]
                histories.append(history)
        price_history = pd.concat(
            histories,
            axis=1,
        ).dropna(how="all")
        st.subheader(f"📉 {chart_range.title()} Price History")
        if price_history.empty:
            st.warning("Price history is unavailable for the selected ticker(s).")
        else:
            st.line_chart(price_history)
else:
    st.warning("⚠️ No equities currently match your strict custom parameters. Try expanding your sidebar criteria thresholds.")
