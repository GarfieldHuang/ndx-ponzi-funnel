"""把 transcript.md 轉成 transcript.html（帶時間戳連結回 YouTube）。"""
import html, re
from pathlib import Path
root = Path(__file__).resolve().parent.parent
lines = (root / "transcript.md").read_text(encoding="utf-8").splitlines()
title = lines[0].lstrip("# ")
meta, paras = [], []
for ln in lines[1:]:
    if ln.startswith("- "):
        meta.append(re.sub(r"\[(.+?)\]\((.+?)\)", r'<a href="\2">\1</a>', html.escape(ln[2:], quote=False)))
    elif m := re.match(r"\*\*\[(\d+):(\d+)\]\*\* (.*)", ln):
        sec = int(m[1]) * 60 + int(m[2])
        paras.append(f'<p><a class="ts" href="https://www.youtube.com/watch?v=OGxUTvU_St0&t={sec}s" target="_blank" rel="noopener">{m[1]}:{m[2]}</a>{html.escape(m[3])}</p>')
page = f"""<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>龐氏漏斗逐字稿</title><style>
:root{{color-scheme:light;--bg:#f6f5f2;--text:#0b0b0b;--muted:#6f6e69;--accent:#2a78d6}}
@media (prefers-color-scheme:dark){{:root:not([data-theme="light"]){{color-scheme:dark;--bg:#121211;--text:#f0efec;--muted:#9c9b94;--accent:#3987e5}}}}
:root[data-theme="dark"]{{color-scheme:dark;--bg:#121211;--text:#f0efec;--muted:#9c9b94;--accent:#3987e5}}
body{{margin:0;background:var(--bg);color:var(--text);font:16px/1.85 -apple-system,"PingFang TC","Noto Sans TC",sans-serif}}
.wrap{{max-width:760px;margin:0 auto;padding:28px 16px 80px}} h1{{font-size:24px;line-height:1.3}} a{{color:var(--accent)}}
ul{{color:var(--muted);font-size:14px;padding-left:18px}} .ts{{font:600 12px ui-monospace,Menlo,monospace;margin-right:10px;text-decoration:none}}
</style></head><body><div class="wrap"><p><a href="./">← 回到模型重建</a></p><h1>{html.escape(title)}</h1>
<ul>{''.join(f'<li>{m}</li>' for m in meta)}</ul>{''.join(paras)}</div></body></html>"""
(root / "transcript.html").write_text(page, encoding="utf-8")
print("paragraphs:", len(paras))
