# 龐氏漏斗模型重建

重建 YouTube 影片〈「龐氏漏斗模型」曾預測1999納指頂部誤差僅1週…〉（一个狠人，2026-05-15）中的分析方法：

- `transcript.md` / `transcript.html`：Qwen3-ASR 轉錄、簡轉繁並校正的逐字稿
- `index.html`：模型說明、二階導 7 指標中 全部 7 個的歷史（0DTE 為 OCC 實際彙總、集中度用 RSP/SPY、內部人用企業淨發行、本益比用 CAPE 替代） × NDX 同步圖、7 指標現況、頂部三角推算計算機、影片後實際走勢對照
- `scripts/occ_0dte.py`：彙總 OCC 每日 SPX 期權成交，算出 0DTE 佔比並累積到 `data/occ_0dte_daily.csv`
- `scripts/build_data.py`：從 Yahoo Finance、FINRA、FRED、multpl 抓資料輸出 `data/indicators.json`
- `.github/workflows/pages.yml`：每個交易日自動更新資料並部署 GitHub Pages

觸發門檻集中在 `index.html` 的 `RULES` 物件，可自行調整。

本專案僅供教育研究，不構成投資建議。
