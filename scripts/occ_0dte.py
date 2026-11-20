"""從 OCC 彙總 SPX 期權每日 0DTE 成交佔比，增量寫入 data/occ_0dte_daily.csv。

做法：對每個交易日 D 查兩次 OCC volume-query（日報、標的 SPX）：
  1. 不限到期日        → 當天 SPX 期權總成交量
  2. contractDt = D    → 當天到期（0DTE）的成交量
OCC 每筆成交對買賣雙方各記一次（C/F/M 帳戶類型），分子分母都重複計算，比值不受影響。

限制：OCC 只開放最近 2 年的日報，所以第一次執行只能補到兩年前；
之後每次執行都會把新的交易日追加進 CSV，序列會隨時間累積變長。
"""
from __future__ import annotations

import csv
import io
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path

CACHE = Path(__file__).resolve().parent.parent / "data" / "occ_0dte_daily.csv"
BASE = ("https://marketdata.theocc.com/volume-query?format=csv&volumeQueryType=O&symbolType=U"
        "&accountType=ALL&productKind=ALL&porc=BOTH&reportType=D&symbol=SPX")
UA = {"User-Agent": "Mozilla/5.0 (occ-0dte-aggregator)"}
LOOKBACK_DAYS = 728  # OCC：「Report date cannot be prior to 2 years」，留兩天緩衝


def volume(d: date, expiry: date | None) -> int | None:
    """回傳成交量合計；OCC 回錯誤訊息（例如超出 2 年）時回傳 None。"""
    url = f"{BASE}&reportDate={d:%Y%m%d}&contractDt={'' if expiry is None else f'{expiry:%Y%m%d}'}"
    for attempt in range(4):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
                text = r.read().decode("utf-8", "ignore")
            break
        except Exception:
            time.sleep(2 * (attempt + 1))
    else:
        raise RuntimeError(f"OCC request failed: {url}")
    if not text.startswith("quantity"):
        return None
    return sum(int(row["quantity"]) for row in csv.DictReader(io.StringIO(text)) if row.get("quantity"))


def fetch_day(d: date) -> tuple[date, int, int] | None:
    total = volume(d, None)
    if total is None:
        return None
    zero = volume(d, d) if total else 0  # 假日 total = 0，不必再查
    return d, total, zero or 0


def load_cache() -> dict[str, tuple[int, int]]:
    if not CACHE.exists():
        return {}
    with CACHE.open() as f:
        return {r["date"]: (int(r["total"]), int(r["zero_dte"])) for r in csv.DictReader(f)}


def save_cache(rows: dict[str, tuple[int, int]]) -> None:
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    with CACHE.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "total", "zero_dte"])
        for k in sorted(rows):
            w.writerow([k, *rows[k]])


def main(workers: int = 4) -> None:
    cache = load_cache()
    today = date.today()
    start = today - timedelta(days=LOOKBACK_DAYS)
    days = [start + timedelta(n) for n in range((today - start).days)]
    todo = [d for d in days if d.weekday() < 5 and d.isoformat() not in cache]
    print(f"cached {len(cache)} days, fetching {len(todo)} …", flush=True)

    done = 0
    with ThreadPoolExecutor(workers) as ex:  # 少量並行，對 OCC 保持禮貌
        for res in ex.map(fetch_day, todo):
            done += 1
            if res:
                d, total, zero = res
                cache[d.isoformat()] = (total, zero)
            if done % 50 == 0:
                save_cache(cache)  # 中途存檔，被中斷也不會白跑
                print(f"  {done}/{len(todo)}", flush=True)
    save_cache(cache)
    traded = {k: v for k, v in cache.items() if v[0] > 0}
    print(f"saved {len(traded)} trading days → {CACHE}")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 4)
