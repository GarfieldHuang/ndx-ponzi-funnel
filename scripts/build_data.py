"""抓取「龐氏漏斗模型」各指標的歷史資料，輸出 data/indicators.json。

原則：每一筆數字都必須是這次執行時從線上抓到的。每個序列有多個線上來源依序嘗試，
全部失敗就讓整支腳本失敗（workflow 也會失敗、不部署），絕不沿用上次的舊值。

資料來源（皆為免費公開；→ 後為備援）：
  - NDX 月收盤 / 月最低 : Yahoo chart API → yfinance → Stooq
  - 保證金借款 / 現金   : FINRA Margin Statistics
  - 巴菲特指標          : 非金融企業股權市值 ÷ GDP（DBnomics 聯準會 Z.1 / BEA → FRED）
  - 企業淨發行股票      : 內部人賣/買比的替代（DBnomics Z.1 → FRED）
  - M2 年增率           : DBnomics 聯準會 H.6 → FRED M2SL
  - 集中度替代          : Yahoo RSP / SPY（含息調整）
  - SPX 0DTE 佔比       : OCC 日報彙總（scripts/occ_0dte.py；OCC 只開放近 2 年，之後逐日累積）
  - Shiller CAPE        : multpl.com → Shiller 官方 ie_data.xls（shillerdata.com）（遠期 P/E 的替代）
"""
from __future__ import annotations

import io
import json
import re
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yfinance as yf

UA = {"User-Agent": "Mozilla/5.0 (data-builder)"}
OUT = Path(__file__).resolve().parent.parent / "data" / "indicators.json"
FINRA_URL = "https://www.finra.org/sites/default/files/2021-03/margin-statistics.xlsx"
VIDEO_DATE = "2026-05-08"  # 影片中的基準日
USED: dict[str, str] = {}  # 每個序列實際用到的來源，寫進 JSON 供頁面顯示


# ───────── 共用工具 ─────────
def get(url: str, tries: int = 3, timeout: int = 40) -> bytes:
    for k in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
                return r.read()
        except Exception as e:
            if k == tries - 1:
                raise
            print(f"  retry {k + 1}/{tries - 1} {url[:70]}… ({e})", flush=True)
            time.sleep(4 * (k + 1))
    raise RuntimeError("unreachable")


def first_ok(name: str, *sources):
    """依序嘗試多個線上來源，回傳第一個成功的結果；全部失敗就丟出錯誤。"""
    errors = []
    for label, fn in sources:
        try:
            out = fn()
            if out is None or (hasattr(out, "__len__") and len(out) == 0):
                raise ValueError("empty result")
            USED[name] = label
            print(f"  ✓ {name}: {label}", flush=True)
            return out
        except Exception as e:
            errors.append(f"{label}: {e}")
            print(f"::warning::{name} via {label} failed: {e}", flush=True)
    raise RuntimeError(f"{name}: all sources failed → " + " | ".join(errors))


def to_month(s: pd.Series) -> pd.Series:
    s = s.copy()
    s.index = pd.to_datetime(s.index).to_period("M")
    return s[~s.index.duplicated(keep="last")]


def monthly_df(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.index = pd.to_datetime(df.index).to_period("M")
    return df[~df.index.duplicated(keep="last")]


# ───────── 總經序列：DBnomics → FRED ─────────
def fred_csv(series: str) -> pd.Series:
    raw = get(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}", tries=2, timeout=30)
    df = pd.read_csv(io.BytesIO(raw), index_col=0, parse_dates=True)
    return to_month(pd.to_numeric(df.iloc[:, 0], errors="coerce").dropna())


def dbnomics(series_id: str) -> pd.Series:
    """DBnomics 鏡像（聯準會 Z.1 / H.6、BEA）。季資料一律以季初月份標記，與 FRED 對齊。"""
    j = json.loads(get(f"https://api.db.nomics.world/v22/series/{series_id}?observations=1&format=json"))
    doc = j["series"]["docs"][0]
    idx = []
    for p in doc["period"]:
        if "-Q" in p:  # BEA 格式：2026-Q3
            y, q = p.split("-Q")
            idx.append(pd.Period(f"{y}-{(int(q) - 1) * 3 + 1:02d}", "M"))
        else:
            per = pd.Period(p[:7], "M")
            idx.append(per - 2 if series_id.endswith(".Q") else per)  # Z.1 以季末日標記 → 季初月
    v = pd.to_numeric(pd.Series(doc["value"], index=idx), errors="coerce").dropna()
    return v[~v.index.duplicated(keep="last")]


# 名稱: (FRED 代碼, DBnomics 代碼, DBnomics → FRED 單位換算)
MACRO = {
    "equity":   ("NCBEILQ027S", "FED/Z1/LM103164105.Q", 1),         # 百萬美元
    "issuance": ("NCBCEBQ027S", "FED/Z1/FA103164105.Q", 1),         # 百萬美元，年化
    "gdp":      ("GDP",         "BEA/NIPA-T10105/A191RC-Q", 1e-3),  # 十億美元
    "m2":       ("M2SL",        "FED/H6_H6_M2/M2.M", 1),            # 十億美元
}


def macro(key: str) -> pd.Series:
    fid, did, k = MACRO[key]
    # DBnomics 優先：FRED 對雲端機器（GitHub Actions）常逾時；兩者數值逐月一致
    return first_ok(key, (f"DBnomics {did}", lambda: dbnomics(did) * k),
                         (f"FRED {fid}", lambda: fred_csv(fid)))


# ───────── 市場資料 ─────────
def ndx_yahoo() -> pd.DataFrame:
    j = json.loads(get("https://query2.finance.yahoo.com/v8/finance/chart/%5ENDX"
                       "?period1=473385600&period2=9999999999&interval=1mo"))["chart"]["result"][0]
    q = j["indicators"]["quote"][0]
    return monthly_df(pd.DataFrame({"close": q["close"], "low": q["low"]},
                                   index=pd.to_datetime(j["timestamp"], unit="s")).dropna())


def ndx_yfinance() -> pd.DataFrame:
    d = yf.download("^NDX", start="1985-01-01", interval="1mo", progress=False, auto_adjust=False)
    return monthly_df(pd.DataFrame({"close": d["Close"].squeeze(), "low": d["Low"].squeeze()}).dropna())


def ndx_stooq() -> pd.DataFrame:
    d = pd.read_csv(io.BytesIO(get("https://stooq.com/q/d/l/?s=%5Endx&i=m")), index_col=0, parse_dates=True)
    return monthly_df(d.rename(columns={"Close": "close", "Low": "low"})[["close", "low"]].dropna())


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


def yfinance_adj_monthly(ticker: str) -> pd.Series:
    d = yf.download(ticker, start="2003-01-01", interval="1mo", progress=False, auto_adjust=True)
    return to_month(d["Close"].squeeze().dropna())


def concentration_proxy(fetch) -> tuple[pd.Series, pd.Series]:
    """等權重 ÷ 市值加權（RSP/SPY，含息）。比值下跌 = 大型股集中度上升。
    回傳（以起始月 = 100 的指數, 距 36 個月高點的回落 %）。"""
    r = (fetch("RSP") / fetch("SPY")).dropna()
    r = r / r.iloc[0] * 100
    dd = (r / r.rolling(36, min_periods=12).max() - 1) * 100
    return r, dd


def zero_dte_monthly() -> pd.Series:
    """讀取 occ_0dte.py 這次執行時從 OCC 抓取並累積的每日資料，彙總成月度 SPX 0DTE 佔比（%）。"""
    df = pd.read_csv(OUT.parent / "occ_0dte_daily.csv", parse_dates=["date"]).query("total > 1_000_000")
    g = df.groupby(df["date"].dt.to_period("M"))[["total", "zero_dte"]].sum()
    g = g[df.groupby(df["date"].dt.to_period("M")).size() >= 5]  # 月初只有零星幾天時先不畫
    return g["zero_dte"] / g["total"] * 100


def cape_monthly() -> pd.Series:
    html = get("https://www.multpl.com/shiller-pe/table/by-month").decode("utf-8", "ignore")
    rows = re.findall(r"<td>([A-Z][a-z]{2} \d{1,2}, \d{4})</td>\s*<td>.*?([\d.]+)\s*</td>", html, re.S)
    return to_month(pd.Series({pd.to_datetime(d): float(v) for d, v in rows}).sort_index())


def cape_shiller() -> pd.Series:
    """Shiller 教授官網的 ie_data.xls（Data 分頁，P/E10 = CAPE）。下載連結帶版本號，每次先從首頁找。"""
    page = get("https://shillerdata.com/").decode("utf-8", "ignore")
    m = re.search(r'href="((?:https?:)?//[^"]+/ie_data\.xls[^"]*)"', page)
    if not m:
        raise ValueError("ie_data.xls link not found on shillerdata.com")
    url = m.group(1) if m.group(1).startswith("http") else "https:" + m.group(1)
    df = pd.read_excel(io.BytesIO(get(url)), sheet_name="Data", header=None, skiprows=8, engine="xlrd")
    df = df[[0, 12]].apply(pd.to_numeric, errors="coerce").dropna()
    years = df[0].astype(int)
    months = ((df[0] - years) * 100).round().astype(int)  # 2026.1 = 10 月、2026.01 = 1 月
    idx = pd.PeriodIndex.from_fields(year=years, month=months, freq="M")
    s = pd.Series(df[12].astype(float).values, index=idx)
    return s[~s.index.duplicated(keep="last")].sort_index()


def post_video_check() -> dict:
    """影片基準日之後 NDX 的實際日線高點，用來對照模型預測。"""
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


# ───────── 主程式 ─────────
def main() -> None:
    ndx_df = first_ok("NDX", ("Yahoo chart API", ndx_yahoo), ("yfinance", ndx_yfinance), ("Stooq", ndx_stooq))
    ndx = ndx_df["close"]

    m = first_ok("FINRA margin", ("FINRA xlsx", finra_margin))
    debit = m["debit"] / 1000  # 十億美元
    margin_mom = debit.pct_change() * 100
    margin_accel = margin_mom.diff()  # 二階導：月增率的變化（百分點）
    margin_cash = m["debit"] / m["credit_cash"]

    equity = macro("equity") / 1000  # 百萬 → 十億
    buffett = (equity / macro("gdp") * 100).dropna()
    # 內部人賣/買比的替代：> 0 代表企業整體從「回購」轉為「賣股給市場」（IPO、增發、內部人套現）
    issuance = macro("issuance") / 1000
    gap = (macro("m2").pct_change(12) * 100 - ndx.pct_change(12) * 100).dropna()

    cape = first_ok("Shiller CAPE", ("multpl.com", cape_monthly), ("shillerdata.com ie_data.xls", cape_shiller))
    conc, conc_dd = first_ok("RSP/SPY", ("Yahoo chart API", lambda: concentration_proxy(yahoo_adj_monthly)),
                                        ("yfinance", lambda: concentration_proxy(yfinance_adj_monthly)))
    zero_dte = first_ok("SPX 0DTE", ("OCC daily volume", zero_dte_monthly))
    post_video = first_ok("post-video NDX", ("Yahoo chart API", post_video_check))

    idx = pd.period_range("1990-01", ndx.index.max(), freq="M")
    frame = pd.DataFrame({
        "ndx": ndx,
        "ndx_low": ndx_df["low"],
        "margin_debt": debit,
        "margin_mom": margin_mom,
        "margin_accel": margin_accel,
        "margin_cash": margin_cash,
        "buffett": buffett.reindex(idx).ffill(limit=8),  # 季資料 → 往後延伸到下一季公布前
        "m2_ndx_gap": gap,
        "cape": cape,
        "issuance": issuance.reindex(idx).ffill(limit=8),
        "zero_dte": zero_dte,
        "rsp_spy": conc,
        "rsp_spy_dd": conc_dd,
    }).reindex(idx)

    out = {
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "dates": [str(p) for p in frame.index],
        "series": {c: [None if pd.isna(v) else round(float(v), 3) for v in frame[c]] for c in frame.columns},
        "buffett_quarter_end": str(buffett.index.max()),
        "issuance_quarter_end": str(issuance.index.max()),
        "post_video": post_video,
        "sources": USED,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")))
    print(f"wrote {OUT} ({len(frame)} months)\n{frame.dropna(how='all').tail(1).T}")


if __name__ == "__main__":
    main()
