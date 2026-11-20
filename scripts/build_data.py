"""抓取「龐氏漏斗模型」可回測指標的歷史資料，輸出 data/indicators.json。

資料來源（皆為免費公開）：
  - NDX 月收盤         : Yahoo Finance (^NDX)
  - 保證金借款 / 現金   : FINRA Margin Statistics (debit balances, free credit in cash accounts)
  - 巴菲特指標          : FRED NCBEILQ027S (非金融企業股權市值) / GDP
  - 企業淨發行股票     : FRED NCBCEBQ027S（內部人賣/買比的替代）
  - M2 年增率           : FRED M2SL
  - 集中度替代         : Yahoo Finance RSP / SPY（含息調整）
  - SPX 0DTE 佔比      : OCC 日報彙總（scripts/occ_0dte.py，OCC 只開放近 2 年，之後逐次累積）
  - Shiller CAPE        : multpl.com（作為「市盈率」指標的替代，影片用的是遠期 P/E，無免費歷史）

，只以影片中的現值顯示。
"""
from __future__ import annotations

import io
import json
import re
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yfinance as yf

UA = {"User-Agent": "Mozilla/5.0 (data-builder)"}
OUT = Path(__file__).resolve().parent.parent / "data" / "indicators.json"
FINRA_URL = "https://www.finra.org/sites/default/files/2021-03/margin-statistics.xlsx"


def get(url: str) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
        return r.read()


def fred(series: str) -> pd.Series:
    df = pd.read_csv(io.BytesIO(get(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}")),
                     index_col=0, parse_dates=True)
    return pd.to_numeric(df.iloc[:, 0], errors="coerce").dropna()


def to_month(s: pd.Series) -> pd.Series:
    s = s.copy()
    s.index = pd.to_datetime(s.index).to_period("M")
    return s[~s.index.duplicated(keep="last")]


def ndx_monthly() -> pd.DataFrame:
    """依序嘗試：Yahoo chart API → yfinance → Stooq。回傳月收盤 close 與月最低 low。"""
    try:
        j = json.loads(get("https://query2.finance.yahoo.com/v8/finance/chart/%5ENDX"
                           "?period1=473385600&period2=9999999999&interval=1mo"))["chart"]["result"][0]
        q = j["indicators"]["quote"][0]
        df = pd.DataFrame({"close": q["close"], "low": q["low"]},
                          index=pd.to_datetime(j["timestamp"], unit="s")).dropna()
        if len(df) > 100:
            return monthly_df(df)
    except Exception as e:
        print("Yahoo chart API failed:", e)
    try:
        d = yf.download("^NDX", start="1985-01-01", interval="1mo", progress=False, auto_adjust=False)
        df = pd.DataFrame({"close": d["Close"].squeeze(), "low": d["Low"].squeeze()}).dropna()
        if len(df) > 100:
            return monthly_df(df)
    except Exception as e:
        print("yfinance failed:", e)
    d = pd.read_csv(io.BytesIO(get("https://stooq.com/q/d/l/?s=%5Endx&i=m")), index_col=0, parse_dates=True)
    return monthly_df(d.rename(columns={"Close": "close", "Low": "low"})[["close", "low"]].dropna())


def monthly_df(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.index = pd.to_datetime(df.index).to_period("M")
    return df[~df.index.duplicated(keep="last")]


def finra_margin() -> pd.DataFrame:
    df = pd.read_excel(io.BytesIO(get(FINRA_URL)))
    df.columns = ["ym", "debit", "credit_cash", "credit_margin"]
    df["ym"] = pd.PeriodIndex(df["ym"].astype(str), freq="M")
    return df.set_index("ym").sort_index().astype(float)


def yahoo_adj_monthly(ticker: str) -> pd.Series:
    j = json.loads(get(f"https://query2.finance.yahoo.com/v8/finance/chart/{ticker}"
                       "?period1=946684800&period2=9999999999&interval=1mo"))["chart"]["result"][0]
    s = pd.Series(j["indicators"]["adjclose"][0]["adjclose"], index=pd.to_datetime(j["timestamp"], unit="s"))
    return to_month(s.dropna())


def concentration_proxy() -> tuple[pd.Series, pd.Series]:
    """等權重 ÷ 市值加權（RSP/SPY，含息）。比值下跌 = 大型股集中度上升。
    回傳（以起始月 = 100 的指數, 距 36 個月高點的回落 %）。"""
    r = (yahoo_adj_monthly("RSP") / yahoo_adj_monthly("SPY")).dropna()
    r = r / r.iloc[0] * 100
    dd = (r / r.rolling(36, min_periods=12).max() - 1) * 100
    return r, dd


def zero_dte_monthly() -> pd.Series:
    """讀取 occ_0dte.py 累積的每日快取，彙總成月度 SPX 0DTE 成交佔比（%）。"""
    f = OUT.parent / "occ_0dte_daily.csv"
    if not f.exists():
        return pd.Series(dtype=float)
    df = pd.read_csv(f, parse_dates=["date"]).query("total > 1_000_000")  # 排除休市/提前收盤等異常日
    g = df.groupby(df["date"].dt.to_period("M"))[["total", "zero_dte"]].sum()
    g = g[df.groupby(df["date"].dt.to_period("M")).size() >= 5]  # 月初只有零星幾天時先不畫
    return g["zero_dte"] / g["total"] * 100


def cape_monthly() -> pd.Series:
    html = get("https://www.multpl.com/shiller-pe/table/by-month").decode("utf-8", "ignore")
    rows = re.findall(r"<td>([A-Z][a-z]{2} \d{1,2}, \d{4})</td>\s*<td>.*?([\d.]+)\s*</td>", html, re.S)
    s = pd.Series({pd.to_datetime(d): float(v) for d, v in rows}).sort_index()
    return to_month(s)


VIDEO_DATE = "2026-05-08"  # 影片中的基準日（NDX 收 29,333 附近）


def post_video_check() -> dict | None:
    """影片基準日之後 NDX 的實際日線高點，用來對照模型預測。"""
    try:
        j = json.loads(get("https://query2.finance.yahoo.com/v8/finance/chart/%5ENDX"
                           "?range=2y&interval=1d"))["chart"]["result"][0]
        q = j["indicators"]["quote"][0]
        df = pd.DataFrame({"high": q["high"], "close": q["close"]},
                          index=pd.to_datetime(j["timestamp"], unit="s").normalize()).dropna()
        after = df.loc[VIDEO_DATE:]
        peak_day = after["high"].idxmax()
        return {
            "base_date": VIDEO_DATE,
            "base_close": round(float(df.loc[:VIDEO_DATE, "close"].iloc[-1]), 1),
            "peak_date": peak_day.strftime("%Y-%m-%d"),
            "peak_high": round(float(after.loc[peak_day, "high"]), 1),
            "last_date": after.index[-1].strftime("%Y-%m-%d"),
            "last_close": round(float(after["close"].iloc[-1]), 1),
        }
    except Exception as e:
        print("post-video check failed:", e)
        return None


def main() -> None:
    ndx_df = ndx_monthly()
    ndx = ndx_df["close"]
    m = finra_margin()
    debit = m["debit"] / 1000  # 十億美元
    margin_mom = debit.pct_change() * 100
    margin_accel = margin_mom.diff()  # 二階導：月增率的變化（百分點）
    margin_cash = m["debit"] / m["credit_cash"]

    equity = to_month(fred("NCBEILQ027S")) / 1000  # 百萬 → 十億
    gdp = to_month(fred("GDP"))
    buffett = (equity / gdp * 100).dropna()

    # 內部人賣/買比的替代：非金融企業股票淨發行（季資料、年化，十億美元）。
    # > 0 代表企業整體從「回購」轉為「賣股給市場」（IPO、增發、內部人套現）。
    issuance = to_month(fred("NCBCEBQ027S")) / 1000

    m2 = to_month(fred("M2SL"))
    m2_yoy = m2.pct_change(12) * 100
    ndx_yoy = ndx.pct_change(12) * 100
    gap = (m2_yoy - ndx_yoy).dropna()

    try:
        cape = cape_monthly()
    except Exception as e:  # multpl 偶爾擋爬蟲；失敗就略過，不影響其他指標
        print("CAPE fetch failed:", e)
        cape = pd.Series(dtype=float)

    try:
        conc, conc_dd = concentration_proxy()
    except Exception as e:
        print("RSP/SPY fetch failed:", e)
        conc = conc_dd = pd.Series(dtype=float)

    idx = pd.period_range("1990-01", ndx.index.max(), freq="M")
    frame = pd.DataFrame({
        "ndx": ndx,
        "ndx_low": ndx_df["low"],
        "margin_debt": debit,
        "margin_mom": margin_mom,
        "margin_accel": margin_accel,
        "margin_cash": margin_cash,
        "buffett": buffett.reindex(idx).ffill(limit=8),  # 季資料 → 往後填到下一季
        "m2_ndx_gap": gap,
        "cape": cape,
        "issuance": issuance.reindex(idx).ffill(limit=8),
        "zero_dte": zero_dte_monthly(),
        "rsp_spy": conc,
        "rsp_spy_dd": conc_dd,
    }).reindex(idx)

    out = {
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "dates": [str(p) for p in frame.index],
        "series": {c: [None if pd.isna(v) else round(float(v), 3) for v in frame[c]] for c in frame.columns},
        "buffett_quarter_end": str(buffett.index.max()),
        "issuance_quarter_end": str(issuance.index.max()),
        "post_video": post_video_check(),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")))
    last = frame.dropna(how="all").tail(1).T
    print(f"wrote {OUT} ({len(frame)} months)\n{last}")


if __name__ == "__main__":
    main()
