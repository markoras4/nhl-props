"""Blog: History (nostalgia, rivalries, legendary nights) and Betting Lab (how to think about betting).

Posts live in blog/posts/<slug>.json (metadata) + <slug>.html (body). Photos from Wikimedia Commons are downloaded at build
time into output/blog/img (self-hosted, credited under each image and at the end); if a download fails the original URL is used.
Writes output/blog/index.html and output/blog/<slug>.html (served as /blog/<slug>).
"""
from __future__ import annotations

import html
import json
import re
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "blog" / "posts"
OUT = ROOT / "output" / "blog"
UA = {"User-Agent": "NHLPropsLab-blog/1.0 (article images, credited)"}
SECTIONS = {"history": "History", "betting": "Betting Lab"}

CSS = r"""
:root{--bg:#0a0e13;--bg2:#0d131a;--panel:#111821;--panel2:#17202b;--line:#22303f;--text:#e7eef6;--mute:#8796a8;--accent:#3ddc97;--gold:#e9c46a;--glow:61,220,151;color-scheme:dark;
--f-ui:"Exo 2",-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;--f-disp:"Oxanium","Exo 2",sans-serif;--f-read:"Source Serif 4",Georgia,"Times New Roman",serif}
@media (prefers-color-scheme: light){:root:not([data-theme="dark"]){--bg:#f3f5f8;--bg2:#eaeef3;--panel:#fff;--panel2:#eef2f6;--line:#dbe2ea;--text:#111821;--mute:#5a697b;--accent:#0d9a60;--gold:#b8860b;--glow:13,154,96;color-scheme:light}}
:root[data-theme="light"]{--bg:#f3f5f8;--bg2:#eaeef3;--panel:#fff;--panel2:#eef2f6;--line:#dbe2ea;--text:#111821;--mute:#5a697b;--accent:#0d9a60;--gold:#b8860b;--glow:13,154,96;color-scheme:light}
*{box-sizing:border-box}html{-webkit-text-size-adjust:100%;scroll-behavior:smooth}body{margin:0;background:var(--bg);color:var(--text);font:16px/1.6 var(--f-ui)}
a{color:inherit;text-decoration:none}img{max-width:100%;display:block}
.wrap{max-width:1180px;margin:0 auto;padding:0 max(16px,env(safe-area-inset-left))}
header{position:sticky;top:0;z-index:20;background:color-mix(in srgb,var(--bg) 86%,transparent);backdrop-filter:blur(12px);-webkit-backdrop-filter:blur(12px);border-bottom:1px solid var(--line)}
.top{display:flex;align-items:center;gap:18px;height:60px}.brand{font:800 18px var(--f-disp);letter-spacing:.04em;text-transform:uppercase}.brand b{color:var(--accent)}
.top nav{display:flex;gap:4px;margin-left:auto}.top nav a{padding:7px 12px;border-radius:9px;font-weight:600;font-size:14px;color:var(--mute)}.top nav a:hover,.top nav a.on{color:var(--text);background:var(--panel2)}
.cta{background:var(--accent);color:var(--accent-ink,#06210f)!important;font-weight:700}
/* index */
.bh{padding:46px 0 18px}.kick{font:700 12px var(--f-disp);letter-spacing:.18em;text-transform:uppercase;color:var(--accent)}
.bh h1{font:800 clamp(34px,5vw,56px)/1.05 var(--f-disp);margin:8px 0 10px;letter-spacing:.01em}.bh p{color:var(--mute);max-width:640px;margin:0;font-size:17px}
.tabs{display:inline-flex;gap:4px;padding:4px;border:1px solid var(--line);border-radius:12px;margin:22px 0 22px;background:var(--panel)}
.tabs button{all:unset;cursor:pointer;padding:8px 14px;border-radius:9px;font-weight:700;font-size:14px;color:var(--mute);transition:color .2s,box-shadow .2s}
.tabs button.on{color:var(--accent);box-shadow:inset 0 0 0 1.5px var(--accent)}
.feat{display:grid;grid-template-columns:1.25fr 1fr;border-radius:20px;overflow:hidden;border:1px solid var(--line);background:var(--panel);margin-bottom:22px;transition:transform .35s cubic-bezier(.2,.8,.2,1),box-shadow .35s}
.feat:hover{transform:translateY(-3px);box-shadow:0 24px 60px rgba(0,0,0,.35),0 0 50px rgba(var(--glow),.08)}
.feat .ph{position:relative;min-height:360px;background:var(--panel2)}.feat .ph img{position:absolute;inset:0;width:100%;height:100%;object-fit:cover;transition:transform 1.2s cubic-bezier(.2,.8,.2,1)}
.feat:hover .ph img{transform:scale(1.04)}.feat .tx{padding:34px;display:flex;flex-direction:column;justify-content:center;gap:12px}
.feat h2{font:800 32px/1.1 var(--f-disp);margin:0}.feat p{color:var(--mute);margin:0;font-size:16px}
.meta{color:var(--mute);font-size:13px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(330px,1fr));gap:18px;margin-bottom:60px}
.card{display:flex;flex-direction:column;border-radius:16px;overflow:hidden;border:1px solid var(--line);background:var(--panel);transition:transform .3s cubic-bezier(.2,.8,.2,1),box-shadow .3s,border-color .3s;animation:up .6s cubic-bezier(.2,.8,.2,1) both}
.card:hover{transform:translateY(-4px);border-color:color-mix(in srgb,var(--accent) 50%,var(--line));box-shadow:0 18px 44px rgba(0,0,0,.3)}
.card .ph{aspect-ratio:16/9;background:var(--panel2);overflow:hidden}.card .ph img{width:100%;height:100%;object-fit:cover;transition:transform .9s cubic-bezier(.2,.8,.2,1)}.card:hover .ph img{transform:scale(1.05)}
.card .tx{padding:18px 18px 20px;display:flex;flex-direction:column;gap:8px}.card h3{font:800 21px/1.15 var(--f-disp);margin:0}.card p{margin:0;color:var(--mute);font-size:15px}
.tag{display:inline-block;align-self:flex-start;font:700 11px var(--f-disp);letter-spacing:.14em;text-transform:uppercase;padding:3px 9px;border-radius:999px}
.tag.history{color:var(--gold);box-shadow:inset 0 0 0 1.5px color-mix(in srgb,var(--gold) 70%,transparent)}.tag.betting{color:var(--accent);box-shadow:inset 0 0 0 1.5px color-mix(in srgb,var(--accent) 70%,transparent)}
@keyframes up{from{opacity:0;transform:translateY(14px)}}
/* article */
.hero{position:relative;min-height:min(78vh,720px);display:flex;align-items:flex-end;overflow:hidden;background:#05080b}
.hero img{position:absolute;inset:0;width:100%;height:100%;object-fit:cover;opacity:.92;animation:kb 18s ease-out both}
@keyframes kb{from{transform:scale(1.12)}to{transform:scale(1)}}
.hero:after{content:"";position:absolute;inset:0;background:linear-gradient(180deg,rgba(5,8,11,.15) 0%,rgba(5,8,11,.35) 45%,rgba(5,8,11,.94) 100%)}
.hero .in{position:relative;z-index:1;padding:0 0 46px;color:#f2f6fa;animation:up 1s .2s cubic-bezier(.2,.8,.2,1) both}
.hero h1{font:800 clamp(34px,6vw,68px)/1.02 var(--f-disp);margin:12px 0 14px;max-width:900px;text-shadow:0 4px 30px rgba(0,0,0,.5)}
.hero .deck{font:400 clamp(17px,2vw,21px)/1.5 var(--f-read);max-width:760px;margin:0 0 16px;color:#d9e2ea}
.hero .meta{color:#b5c2cf}.hero .cr{position:absolute;right:14px;bottom:10px;z-index:1;font-size:11px;color:#9fb0bf}
.art{max-width:720px;margin:0 auto;padding:46px max(16px,env(safe-area-inset-left)) 30px;font:400 19px/1.75 var(--f-read)}
.art p{margin:0 0 1.3em}.art>p:first-of-type:first-letter{float:left;font:800 74px/0.8 var(--f-disp);margin:8px 12px 0 0;color:var(--accent)}
.art h2{font:800 28px/1.2 var(--f-disp);margin:2em 0 .7em;letter-spacing:.01em}
.art blockquote{margin:1.8em 0;padding:4px 0 4px 22px;border-left:3px solid var(--accent);font:italic 500 24px/1.45 var(--f-read);color:var(--text)}
.art blockquote small{display:block;font:500 14px var(--f-ui);font-style:normal;color:var(--mute);margin-top:8px}
.art figure{margin:2em -40px}.art figure img{width:100%;border-radius:14px}.art figcaption{font:500 13px/1.45 var(--f-ui);color:var(--mute);margin-top:8px;padding:0 40px}
.art .viz{margin:2.2em -40px;padding:22px;border:1px solid var(--line);border-radius:16px;background:var(--panel);font-family:var(--f-ui)}
.art .viz h4{margin:0 0 4px;font:800 13px var(--f-disp);letter-spacing:.14em;text-transform:uppercase;color:var(--accent)}.art .viz p{font:400 14px/1.5 var(--f-ui);color:var(--mute);margin:0 0 12px}
.art .viz svg{width:100%;height:auto;display:block;color:var(--text)}
.art .stat{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin:2em -40px;font-family:var(--f-ui)}.art .stat div{padding:18px;border-radius:14px;background:var(--panel);border:1px solid var(--line);text-align:center}
.art .stat b{display:block;font:800 34px var(--f-disp);color:var(--accent)}.art .stat span{font-size:13px;color:var(--mute)}
.art ul{padding-left:1.1em}.art li{margin:.3em 0}
.note{font:500 14px/1.55 var(--f-ui);color:var(--mute);border-top:1px solid var(--line);padding-top:18px;margin-top:2.5em}
.rg{font:500 14px/1.55 var(--f-ui);border:1px solid var(--line);border-left:3px solid var(--gold);border-radius:10px;padding:12px 16px;background:var(--panel);margin:2em 0}
.credits{font:500 12.5px/1.6 var(--f-ui);color:var(--mute)}.credits a{text-decoration:underline}
.more{max-width:1180px;margin:0 auto 60px;padding:0 16px}.more h3{font:800 20px var(--f-disp);margin:0 0 14px}
.reveal{opacity:0;transform:translateY(18px);transition:opacity .8s cubic-bezier(.2,.8,.2,1),transform .8s cubic-bezier(.2,.8,.2,1)}.reveal.in{opacity:1;transform:none}
.bar{transform-origin:bottom;animation:grow 1s cubic-bezier(.2,.8,.2,1) both}@keyframes grow{from{transform:scaleY(0)}}
footer{border-top:1px solid var(--line);padding:26px 0 40px;color:var(--mute);font-size:13px}
@media (max-width:800px){.feat{grid-template-columns:1fr}.feat .ph{min-height:220px}.art figure,.art .viz,.art .stat{margin-left:0;margin-right:0}.art figcaption{padding:0}.top nav a:not(.cta):not(.on){display:none}.art{font-size:18px}}
@media (prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}.reveal{opacity:1;transform:none}}
"""

THEME_JS = (Path(__file__).parent / "theme_head.html").read_text(encoding="utf-8")

HEAD = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>{title}</title><meta name="description" content="{desc}"><meta name="theme-color" content="#0a0e13">
<meta property="og:title" content="{title}"><meta property="og:description" content="{desc}"><meta property="og:type" content="{ogtype}">{ogimg}
<meta name="twitter:card" content="summary_large_image">
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Exo+2:wght@400;500;600;700&family=Oxanium:wght@500;600;700;800&family=Source+Serif+4:ital,wght@0,400;0,500;1,400;1,500&display=swap">
<style>{css}</style>{themejs}</head><body>
<header><div class="wrap top"><a class="brand" href="/">NHL Props <b>Lab</b></a><nav><a href="/blog" class="{on_blog}">Blog</a><a href="/app">Tonight's games</a><a class="cta" href="/app">Open the app</a></nav></div></header>"""

FOOT = """<footer><div class="wrap">18+ · Gambling can be addictive. Play responsibly. Need help? Sweden: <a href="https://www.stodlinjen.se" rel="noopener">Stödlinjen</a> 020-81 91 00 · <a href="https://www.spelpaus.se" rel="noopener">Spelpaus.se</a> · Elsewhere: <a href="https://www.begambleaware.org" rel="noopener">BeGambleAware</a>, <a href="https://www.ncpgambling.org" rel="noopener">1-800-GAMBLER</a>. NHL Props Lab is an analysis tool and does not take bets. · <a href="/legal.html#terms">Terms</a> · <a href="/legal.html#privacy">Privacy</a></div></footer>
<script>document.querySelectorAll('.reveal').forEach(el=>new IntersectionObserver((es,o)=>es.forEach(e=>{if(e.isIntersecting){e.target.classList.add('in');o.unobserve(e.target)}}),{threshold:.12}).observe(el));
const tb=document.querySelector('.tabs');if(tb)tb.onclick=e=>{const b=e.target.closest('button');if(!b)return;tb.querySelectorAll('button').forEach(x=>x.classList.toggle('on',x===b));
document.querySelectorAll('[data-sec]').forEach(c=>{c.style.display=(b.dataset.k==='all'||c.dataset.sec===b.dataset.k)?'':'none';c.style.animation='none';c.offsetHeight;c.style.animation='';});};</script></body></html>"""


def fetch_img(url: str, name: str) -> str:
    """download a Commons image into output/blog/img; returns the path to use in the page"""
    if not url or not url.startswith("http"):
        return url
    ext = ".jpg" if ".jp" in url.lower() else (".png" if ".png" in url.lower() else ".jpg")
    import hashlib
    name = f"{name}-{hashlib.md5(url.encode()).hexdigest()[:6]}"   # a new photo URL never reuses an old cached file
    dst = OUT / "img" / (name + ext)
    cache = ROOT / "data" / "cache" / "blog_img" / (name + ext)   # kept between builds (actions cache), so Commons is asked once
    try:
        if not cache.exists():
            import time
            time.sleep(2.0)   # be gentle with Commons (it rate-limits bursts)
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=40) as r:
                data = r.read()
            if data[:3] != b"\xff\xd8\xff" and data[:4] != b"\x89PNG":
                raise ValueError("not an image")
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_bytes(data)
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(cache.read_bytes())
        return f"/blog/img/{dst.name}"
    except Exception as e:  # noqa: BLE001
        print(f"  blog image not downloaded ({e}); using the original URL")
        return url


def posts() -> list[dict]:
    out = []
    for f in sorted(SRC.glob("*.json")):
        m = json.loads(f.read_text(encoding="utf-8"))
        if m.get("draft"):
            continue
        m["body"] = (SRC / (f.stem + ".html")).read_text(encoding="utf-8")
        out.append(m)
    return sorted(out, key=lambda m: m["date"], reverse=True)


def card(m, big=False):
    tag = f'<span class="tag {m["section"]}">{SECTIONS[m["section"]]}</span>'
    meta = f'<span class="meta">{m["date_label"]} · {m["read_min"]} min read</span>'
    if big:
        return (f'<a class="feat" href="/blog/{m["slug"]}" data-sec="{m["section"]}"><div class="ph"><img src="{m["hero_src"]}" alt="{html.escape(m["hero"]["alt"])}"></div>'
                f'<div class="tx">{tag}<h2>{html.escape(m["title"])}</h2><p>{html.escape(m["deck"])}</p>{meta}</div></a>')
    return (f'<a class="card" href="/blog/{m["slug"]}" data-sec="{m["section"]}"><div class="ph"><img loading="lazy" src="{m["hero_src"]}" alt="{html.escape(m["hero"]["alt"])}"></div>'
            f'<div class="tx">{tag}<h3>{html.escape(m["title"])}</h3><p>{html.escape(m["deck"])}</p>{meta}</div></a>')


def build(log=print) -> int:
    ps = posts()
    OUT.mkdir(parents=True, exist_ok=True)
    for m in ps:
        m["hero_src"] = fetch_img(m["hero"]["src"], m["slug"] + "-hero")
        # inline photos: <img data-commons="URL" ...> become self-hosted copies
        n = [0]
        def sub(mt):
            n[0] += 1
            return f'src="{fetch_img(mt.group(1), m["slug"] + "-" + str(n[0]))}"'
        m["body_out"] = re.sub(r'data-commons="([^"]+)"', sub, m["body"])
    for m in ps:
        credits = "".join(f'<li>{c}</li>' for c in m.get("credits", []))
        rg = ('<div class="rg"><b>18+.</b> Betting involves risk and losing runs are normal, even with an edge. Only bet what you can afford to lose. '
              'Need help? <a href="https://www.stodlinjen.se" rel="noopener">Stödlinjen</a> 020-81 91 00 · <a href="https://www.spelpaus.se" rel="noopener">Spelpaus.se</a>.</div>') if m["section"] == "betting" else ""
        others = [o for o in ps if o["slug"] != m["slug"]][:3]
        page = HEAD.format(themejs=THEME_JS, title=html.escape(m["title"]) + " · NHL Props Lab", desc=html.escape(m["deck"]), css=CSS, on_blog="on", ogtype="article",
                           ogimg=f'<meta property="og:image" content="{m["hero_src"]}">')
        page += (f'<main><section class="hero"><img src="{m["hero_src"]}" alt="{html.escape(m["hero"]["alt"])}"><div class="wrap in">'
                 f'<span class="tag {m["section"]}">{SECTIONS[m["section"]]}</span><h1>{html.escape(m["title"])}</h1><p class="deck">{html.escape(m["deck"])}</p>'
                 f'<span class="meta">{m["date_label"]} · {m["read_min"]} min read</span></div><span class="cr">{m["hero"]["credit"]}</span></section>'
                 f'<article class="art">{m["body_out"]}{rg}</article>'
                 + (f'<section class="more"><h3>More from the blog</h3><div class="grid">{"".join(card(o) for o in others)}</div></section>' if others else "")
                 + '</main>' + FOOT)
        (OUT / f'{m["slug"]}.html').write_text(page, encoding="utf-8")
    idx = HEAD.format(themejs=THEME_JS, title="Blog · NHL Props Lab", desc="Legendary nights, rivalries and characters from NHL history, and the Betting Lab: how to think about betting.",
                      css=CSS, on_blog="on", ogtype="website", ogimg=(f'<meta property="og:image" content="{ps[0]["hero_src"]}">' if ps else ""))
    idx += ('<main class="wrap"><section class="bh"><span class="kick">The blog</span><h1>Stories from the ice</h1>'
            '<p>Legendary nights, bitter rivalries, records and characters from hockey\'s past, and the Betting Lab, where we take apart how to think about a bet.</p>'
            '<div class="tabs"><button class="on" data-k="all">All</button><button data-k="history">History</button><button data-k="betting">Betting Lab</button></div></section>')
    if ps:
        idx += card(ps[0], big=True) + '<div class="grid">' + "".join(card(m) for m in ps[1:]) + '</div>'
    idx += '</main>' + FOOT
    (OUT / "index.html").write_text(idx, encoding="utf-8")
    log(f"Blog: {len(ps)} posts -> {OUT}")
    return len(ps)


if __name__ == "__main__":
    build()
