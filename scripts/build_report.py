#!/usr/bin/env python3
"""Build the design-critic HTML report.

Reads <dir>/capture.json (from capture.py) and <dir>/findings.json (written by the model after
looking at the screenshots) and writes <dir>/report.html.

The report is meant to be read in layers: at the top the score, the verdict and "Start here";
then the problems, collapsed (one line each, click to expand); the technical detail (code,
file:line, metrics) only shows up when asked for.

The report UI is translated (scripts/i18n.py) into the language given by "lang" in
findings.json or --lang; the findings themselves are written by the model in that language.

Usage:
  python build_report.py .design-critic/example-20261007-101500
  python build_report.py <dir> --findings other.json --out report.html
  python build_report.py <dir> --after <dir>-after      (after compare.py: adds "Before and after")

findings.json schema (write the "plain" fields without jargon, in the user's language):
{
  "lang": "en",                                 ISO 639-1 code of the user's language (default en)
  "score": 4,                                   overall score 0-10 (optional; else mean of areas)
  "verdict": "One sentence, plain",
  "summary": "2-3 sentences, plain: what works, what fails, where to start",
  "areas": {"Easy to understand at a glance": 6, "Easy to read": 3, ...},   0-10, shown as traffic lights
  "strengths": ["What already works and should be kept (plain)", ...],
  "start_here": ["F3", "F1", "F5"],             the 3 fixes with the most impact per effort
  "findings": [{
    "id": "F1",
    "severity": "critical" | "high" | "medium" | "low",
    "category": "Responsive",
    "title": "Short, plain title (\"On mobile the page spills off the screen\")",
    "tldr": "One plain sentence, max ~20 words: what the visitor will notice",
    "devices": ["mobile", "tablet", "desktop"],   (optional; otherwise inferred from the screenshots)
    "effort": "5 min" | "30 min" | "2 h",         (optional)
    "problem": "What happens (plain, concrete)",
    "why": "Why it matters to visitors",
    "fix": "How to fix it, in words",
    "code": "CSS/HTML/JSX snippet (technical)",
    "where": "src/components/Hero.tsx:42 (technical)",
    "evidence": [{"shot": "shots/desktop-light-fold.png", "box": [x, y, w, h], "label": "optional"}],
    "status": "pending" | "fixed",
    "after": {"shot": "after/shots/...", "box": [x, y, w, h]}   (with --after, paths under after/)
  }]
}
"box" coordinates are in pixels of the image itself (see SKILL.md).
"""
import argparse
import html
import json
import re
import shutil
import struct
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from i18n import RTL, strings

SEV_ORDER = ["critical", "high", "medium", "low"]
SEV_ALIAS = {"critico": "critical", "crítico": "critical", "alto": "high", "medio": "medium", "bajo": "low"}
DEVICES = ["mobile", "tablet", "desktop"]
DEV_ALIAS = {"movil": "mobile", "móvil": "mobile", "escritorio": "desktop"}
DEV_ICON = {"mobile": "📱 ", "tablet": "", "desktop": "💻 "}
STATUS_ALIAS = {"arreglado": "fixed", "pendiente": "pending"}
CMP_STATE = {"better": ("ok", "✓"), "same": ("same", "="), "worse": ("bad", "✗")}
YES_NO = {"horizontal_scroll", "shrunk"}


def png_size(path):
    try:
        with open(path, "rb") as f:
            head = f.read(24)
        return struct.unpack(">II", head[16:24])
    except Exception:  # noqa: BLE001
        return None


def esc(s):
    return html.escape(str(s if s is not None else ""))


def first_sentence(text, limit=140):
    s = re.split(r"(?<=[.!?。])\s?", (text or "").strip(), maxsplit=1)[0]
    return s if len(s) <= limit else s[:limit].rsplit(" ", 1)[0] + "…"


def normalize(fnd):
    """Accept the older Spanish values (critico, movil, arreglado...) as aliases."""
    for f in fnd.get("findings", []):
        f["severity"] = SEV_ALIAS.get(f.get("severity"), f.get("severity"))
        f["status"] = STATUS_ALIAS.get(f.get("status"), f.get("status"))
        if f.get("devices"):
            f["devices"] = [DEV_ALIAS.get(d, d) for d in f["devices"]]
    return fnd


def devices_of(f):
    if f.get("devices"):
        return [d for d in f["devices"] if d in DEVICES]
    found = []
    for e in f.get("evidence", []):
        name = Path(e.get("shot", "")).name
        for d in DEVICES:
            if name.startswith(d) and d not in found:
                found.append(d)
        if e.get("shot", "").startswith(("states/", "motion/")) and "desktop" not in found:
            found.append("desktop")
    return found


def dev_name(d, T):
    return DEV_ICON[d] + T["dev_" + d]


def scope_name(scope, T):
    if scope == "global":
        return T["whole_page"]
    vp, _, scheme = scope.partition("-")
    dev = dev_name(vp, T) if vp in DEVICES else vp
    return f"{dev} · {T['dark' if scheme == 'dark' else 'light']}"


def plain_flag(flag, T):
    return re.sub(r"^((?:mobile|tablet|desktop)-(?:light|dark)):", lambda m: scope_name(m[1], T) + ":", flag)


def window(size, box, thumb=False):
    """Crop window (cx0, cy0, cw, ch) around the box, in image pixels."""
    iw, ih = size
    x, y, w, h = [float(v) for v in (box or [0, 0, iw, min(ih, iw * 0.7)])]
    mx, my = max(100, w * 0.5), max(70, h * 0.7)
    cx0, cy0 = max(0, x - mx), max(0, y - my)
    cx1, cy1 = min(iw, x + w + mx), min(ih, y + h + my)
    want = min(iw, 420)
    if cx1 - cx0 < want:
        extra = (want - (cx1 - cx0)) / 2
        cx0, cx1 = max(0, cx0 - extra), min(iw, cx1 + extra)
    cw, ch = cx1 - cx0, cy1 - cy0
    if ch > cw * (0.75 if thumb else 1.2):
        ch = cw * (0.75 if thumb else 1.2)
        cy0 = max(0, min(y + h / 2 - ch / 2, ih - ch))
    if thumb and ch < cw * 0.5:
        ch = min(ih, cw * 0.5)
        cy0 = max(0, min(y + h / 2 - ch / 2, ih - ch))
    return cx0, cy0, cw, ch


def crop_inner(shot, size, box, win):
    """Image offset inside the window + highlighted box."""
    iw, ih = size
    cx0, cy0, cw, ch = win
    x, y, w, h = [float(v) for v in (box or [0, 0, iw, min(ih, iw * 0.7)])]
    p = lambda v, t: f"{100 * v / t:.3f}%"  # noqa: E731
    return (f'<img src="{esc(shot)}" alt="" loading="lazy" style="width:{p(iw, cw)};left:-{p(cx0, cw)};top:-{p(cy0, ch)}">'
            f'<span class="mark" style="left:{p(x - cx0, cw)};top:{p(y - cy0, ch)};width:{p(w, cw)};height:{p(h, ch)}"></span>')


def zoom_data(shot, size, box):
    iw, ih = size
    x, y, w, h = [float(v) for v in (box or [0, 0, iw, min(ih, iw * 0.7)])]
    return f'data-full="{esc(shot)}" data-box="{x:.0f},{y:.0f},{w:.0f},{h:.0f}" data-size="{iw},{ih}"'


def crop(base, shot, box, T, label="", kind="before", thumb=False):
    """Crop of the screenshot around the box, with the box highlighted.

    Outside the summary it is a button that opens the full screenshot in the lightbox."""
    size = png_size(base / shot)
    if not size:
        return f'<div class="missing">{esc(T["missing_shot"].format(shot=shot))}</div>'
    win = window(size, box, thumb)
    inner = crop_inner(shot, size, box, win)
    ratio = f"aspect-ratio:{win[2]:.0f}/{win[3]:.0f}"
    if thumb:
        return f'<span class="crop thumb" style="{ratio}">{inner}</span>'
    tag = T["after"] if kind == "after" else T["before"]
    cap = f"{tag} · {label}" if label else tag
    return (f'<figure class="shot {kind}" dir="ltr"><button type="button" class="crop zoom" style="{ratio}" {zoom_data(shot, size, box)} '
            f'aria-label="{esc(T["zoom_shot"])}">{inner}<span class="zoom-hint">{esc(T["zoom"])}</span></button>'
            f'<figcaption dir="auto">{esc(cap)}</figcaption></figure>')


def before_after(base, f, ev, T):
    """Before/after of a fixed problem: a slider if both screenshots are the same size,
    otherwise side by side. Returns (html, evidence item already used)."""
    after = f["after"]
    name = Path(after["shot"]).name
    before = next((e for e in ev if Path(e["shot"]).name == name), ev[0] if ev else None)
    label = after.get("label") or (before or {}).get("label", "")
    after_html = crop(base, after["shot"], after.get("box"), T, label, kind="after")
    if not before:
        return after_html, None
    sa, sb = png_size(base / before["shot"]), png_size(base / after["shot"])
    if not (sa and sb and sa == sb):
        return (f'<div class="pair">{crop(base, before["shot"], before.get("box"), T, before.get("label", ""))}{after_html}</div>',
                before)
    win = window(sa, before.get("box"))
    ratio = f"aspect-ratio:{win[2]:.0f}/{win[3]:.0f}"
    cap = f"{T['before_after']} · {label}" if label else T["before_after"]
    zoom = lambda e, k: (f'<button type="button" class="zoom linky" {zoom_data(e["shot"], sa, e.get("box"))} '  # noqa: E731
                         f'data-title="{esc(T[k])}">{esc(T["zoom_" + k])}</button>')
    html_ = (f'<figure class="shot ba"><div class="ba-wrap" dir="ltr" style="{ratio}">'
             f'<div class="crop ba-layer">{crop_inner(before["shot"], sa, before.get("box"), win)}</div>'
             f'<div class="crop ba-layer ba-after" style="clip-path:inset(0 0 0 50%)">{crop_inner(after["shot"], sb, after.get("box"), win)}</div>'
             f'<span class="ba-line" style="left:50%"></span><span class="ba-tag l">{esc(T["before"])}</span><span class="ba-tag r">{esc(T["after"])}</span>'
             f'<input type="range" class="ba-range" min="0" max="100" value="50" aria-label="{esc(T["slider"])}"></div>'
             f'<figcaption>{esc(cap)} {zoom(before, "before")} {zoom(after, "after")}</figcaption></figure>')
    return html_, before


def compare_section(cmp, base, T):
    """"Before and after" section from after/comparison.json (written by compare.py)."""
    rows = cmp.get("metrics", [])
    val = lambda r, k: (T["yes"] if r[k] else T["no"]) if r["metric"] in YES_NO else r[k]  # noqa: E731

    def row(r):
        cls, icon = CMP_STATE.get(r["status"], CMP_STATE["same"])
        new = (f'<span class="c-new">{esc(T["new_el"])} <code>{esc(", ".join(r["new"]))}</code></span>' if r.get("new") else "")
        return (f'<li class="{cls}"><span class="c-icon" aria-label="{esc(T[r["status"]])}">{icon}</span>'
                f'<span class="c-main"><span class="c-label">{esc(T.get("m_" + r["metric"], r["label"]))}</span>'
                f'<span class="c-scope">{esc(scope_name(r["scope"], T))}</span>{new}</span>'
                f'<span class="c-val" dir="ltr">{esc(val(r, "before"))} → <b>{esc(val(r, "after"))}</b></span></li>')

    changed = sorted((r for r in rows if r["status"] != "same"), key=lambda r: r["status"] != "worse")
    same = [r for r in rows if r["status"] == "same"]
    better = sum(1 for r in rows if r["status"] == "better")
    worse = sum(1 for r in rows if r["status"] == "worse")
    new_flags = [plain_flag(a, T) for a in cmp.get("new_flags", [])]
    solved = [plain_flag(a, T) for a in cmp.get("resolved_flags", [])]
    bad = worse or new_flags
    head = (f'<p class="c-head bad">{esc(T["cmp_bad"].format(worse=worse, new=len(new_flags)))}</p>' if bad
            else f'<p class="c-head ok">{esc(T["cmp_ok"].format(n=better))}</p>')
    metrics = f'<ul class="cmp">{"".join(row(r) for r in changed)}</ul>' if changed else f'<p class="muted">{esc(T["none_changed"])}</p>'
    if same:
        metrics += (f'<details class="c-same"><summary>{esc(T["unchanged"].format(n=len(same)))}</summary>'
                    f'<ul class="cmp">{"".join(row(r) for r in same)}</ul></details>')
    alerts = ""
    if solved:
        alerts += f'<div class="card"><h2>{esc(T["resolved"])}</h2><ul class="good">{"".join(f"<li>{esc(a)}</li>" for a in solved)}</ul></div>'
    if new_flags:
        alerts += f'<div class="card"><h2>{esc(T["new_alerts"])}</h2><ul class="bad-list">{"".join(f"<li>{esc(a)}</li>" for a in new_flags)}</ul></div>'
    diffs = []
    for d in cmp.get("diffs", []):
        path = f'after/{d["diff"]}'
        size = png_size(base / path)
        if not size:
            continue
        pct = d.get("changed_pct", 0)
        note = T["diff_pct"].format(pct=pct) if pct else T["diff_none"]
        if d.get("size_changed"):
            note += " · " + T["size_changed"]
        diffs.append(f'<figure><button type="button" class="zoom g-item" data-full="{esc(path)}" data-size="{size[0]},{size[1]}">'
                     f'<img src="{esc(path)}" alt="" loading="lazy"></button><figcaption>{esc(scope_name(d["scope"], T))} · {esc(note)}</figcaption></figure>')
    diff_html = (f'<h3>{esc(T["diff_title"])}</h3><p class="muted">{esc(T["diff_sub"])}</p>'
                 f'<div class="gallery">{"".join(diffs)}</div>') if diffs else ""
    return f"""
<section id="before-after">
  <div class="sec-head"><h2>{esc(T["cmp_title"])}</h2><p>{esc(T["cmp_sub"])}</p></div>
  {head}
  <div class="card">{metrics}</div>
  {f'<div class="duo">{alerts}</div>' if alerts else ''}
  {diff_html}
</section>
"""


def area_state(v, T):
    v = float(v)
    if v >= 7:
        return "ok", T["area_ok"]
    if v >= 4:
        return "warn", T["area_warn"]
    return "bad", T["area_bad"]


def score_label(v, T):
    if v >= 8:
        return T["score_great"]
    if v >= 6:
        return T["score_ok"]
    if v >= 4:
        return T["score_work"]
    return T["score_lot"]


def render(cap, fnd, base, T, lang, cmp=None):
    findings = sorted(fnd.get("findings", []), key=lambda f: SEV_ORDER.index(f["severity"]) if f.get("severity") in SEV_ORDER else 2)
    num = {f.get("id"): i + 1 for i, f in enumerate(findings)}
    areas = fnd.get("areas") or fnd.get("scores") or {}
    score = fnd.get("score")
    if score is None and areas:
        score = round(sum(float(v) for v in areas.values()) / len(areas), 1)

    # ---- Header
    meta = next((e["metrics"]["meta"] for e in cap.get("captures", {}).values() if e.get("metrics")), {})
    url = cap.get("url", "")
    u = urlparse(url)
    site = u.netloc or Path(u.path).parent.name + "/" + Path(u.path).name
    try:
        date = datetime.fromisoformat(cap.get("date", "")).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        date = cap.get("date", "")
    page_title = meta.get("title") or site

    counts = [(k, sum(1 for f in findings if f.get("severity") == k)) for k in SEV_ORDER]
    counts_txt = " · ".join(f"{T['sev_' + k]} {n}" for k, n in counts if n)
    fixed = sum(1 for f in findings if f.get("status") == "fixed")

    score_html = ""
    if score is not None:
        st, _ = area_state(score, T)
        score_html = (f'<div class="score {st}"><span class="score-n">{esc(score)}</span><span class="score-of">/10</span>'
                      f'<span class="score-l">{esc(score_label(float(score), T))}</span></div>')

    start_ids = fnd.get("start_here") or [f.get("id") for f in findings[:3]]
    start_items = ""
    for fid in start_ids[:3]:
        f = next((x for x in findings if x.get("id") == fid), None)
        if not f:
            continue
        meta_bits = [dev_name(d, T) for d in devices_of(f)] + ([f"⏱ {f['effort']}"] if f.get("effort") else [])
        start_items += (f'<li><a href="#{esc(fid)}" data-open="{esc(fid)}"><span class="num sev-{esc(f.get("severity"))}">{num[fid]}</span>'
                        f'<span class="st-t">{esc(f.get("title"))}</span><span class="st-m">{esc(" · ".join(meta_bits))}</span></a></li>')

    area_html = "".join(
        f'<li class="{area_state(v, T)[0]}"><span class="light"></span><span class="a-name">{esc(k)}</span><span class="a-state">{esc(area_state(v, T)[1])}</span></li>'
        for k, v in areas.items())
    strengths = "".join(f"<li>{esc(s)}</li>" for s in fnd.get("strengths", []))

    # ---- Problems
    cards = []
    for f in findings:
        sev = f["severity"] if f.get("severity") in SEV_ORDER else "medium"
        devs = devices_of(f)
        dev_html = "".join(f'<span class="tag">{esc(dev_name(d, T))}</span>' for d in devs)
        effort = f'<span class="tag">⏱ {esc(f["effort"])}</span>' if f.get("effort") else ""
        done = f'<span class="tag done">{esc(T["fixed_tag"])}</span>' if f.get("status") == "fixed" else ""
        ev = [e for e in f.get("evidence", []) if e.get("shot")]
        thumb = crop(base, ev[0]["shot"], ev[0].get("box"), T, thumb=True) if ev else ""
        ba, used = before_after(base, f, ev, T) if f.get("after", {}).get("shot") else ("", None)
        shots = ba + "".join(crop(base, e["shot"], e.get("box"), T, e.get("label", "")) for e in ev if e is not used)
        tech = ""
        if f.get("where") or f.get("code"):
            where = f'<p class="where">{esc(T["where"])} <code>{esc(f["where"])}</code></p>' if f.get("where") else ""
            code = (f'<div class="code" dir="ltr"><button type="button" class="copy">{esc(T["copy"])}</button><pre><code>{esc(f["code"])}</code></pre></div>'
                    if f.get("code") else "")
            tech = f'<details class="tech"><summary>{esc(T["tech_details"])}</summary>{where}{code}</details>'
        tldr = f.get("tldr") or first_sentence(f.get("problem"))
        cards.append(f"""
<details class="f sev-{sev}{' is-done' if done else ''}" id="{esc(f.get('id'))}" data-sev="{sev}" data-dev="{' '.join(devs)}">
  <summary>
    <span class="num sev-{sev}">{num[f.get('id')]}</span>
    <span class="s-main">
      <span class="s-meta"><span class="sev-label">{esc(T['sev_' + sev])}</span> · {esc(f.get('category'))}</span>
      <span class="s-title">{esc(f.get('title'))}</span>
      <span class="s-tldr">{esc(tldr)}</span>
      <span class="tags">{done}{dev_html}{effort}</span>
    </span>
    {thumb}
    <span class="chev" aria-hidden="true"></span>
  </summary>
  <div class="f-body">
    <div class="shots">{shots}</div>
    <div class="explain">
      <h4>{esc(T['what'])}</h4><p>{esc(f.get('problem'))}</p>
      {f'<h4>{esc(T["why"])}</h4><p>{esc(f.get("why"))}</p>' if f.get('why') else ''}
      <h4>{esc(T['how'])}</h4><p>{esc(f.get('fix'))}</p>
      {tech}
    </div>
  </div>
</details>""")

    sev_filters = "".join(f'<button type="button" data-f-sev="{k}">{esc(T["sev_" + k])} <b>{n}</b></button>' for k, n in counts if n)
    used_devs = [d for d in DEVICES if any(d in devices_of(f) for f in findings)]
    dev_filters = "".join(f'<button type="button" data-f-dev="{d}">{esc(dev_name(d, T))}</button>' for d in used_devs) if len(used_devs) > 1 else ""

    # ---- Screenshots
    gallery = []
    for key, e in cap.get("captures", {}).items():
        if not e.get("full"):
            continue
        size = png_size(base / e["full"]) or (1, 1)
        gallery.append(f'<figure><button type="button" class="zoom g-item" data-full="{esc(e["full"])}" data-size="{size[0]},{size[1]}">'
                       f'<img src="{esc(e.get("fold") or e["full"])}" alt="" loading="lazy"></button><figcaption>{esc(scope_name(key, T))}</figcaption></figure>')
    extras = []
    for path, label in ((cap.get("states_sheet"), T["states_sheet"]), ((cap.get("motion") or {}).get("sheet"), T["motion_sheet"])):
        if path:
            size = png_size(base / path) or (1, 1)
            extras.append(f'<figure><button type="button" class="zoom g-item" data-full="{esc(path)}" data-size="{size[0]},{size[1]}">'
                          f'<img src="{esc(path)}" alt="" loading="lazy"></button><figcaption>{esc(label)}</figcaption></figure>')
    video = (cap.get("motion") or {}).get("video")
    if video:
        extras.append(f'<figure><video src="{esc(video)}" controls muted preload="metadata"></video><figcaption>{esc(T["video"])}</figcaption></figure>')

    # ---- Technical data
    flags = "".join(f'<li dir="auto">{esc(plain_flag(s, T))}</li>' for s in cap.get("flags", []))
    dl = cap.get("captures", {}).get("desktop-light") or next(iter(cap.get("captures", {}).values()), {})
    rows = "".join(
        f'<tr><td dir="ltr"><span class="sw" style="background:{esc(c["fg"])}"></span>{esc(c["fg"])} / <span class="sw" style="background:{esc(c["bg"])}"></span>{esc(c["bg"])}</td>'
        f'<td><b>{c["ratio"]}:1</b> ({esc(T["min"])} {c["needed"]}:1)</td><td>{esc(c["font_size"])}px</td><td dir="auto">{esc(c["text"])}</td></tr>'
        for c in dl.get("metrics", {}).get("contrast", {}).get("failures", [])[:15])
    contrast = (f'<h3>{esc(T["contrast_title"])}</h3><div class="table-wrap"><table><thead><tr><th>{esc(T["th_pair"])}</th><th>{esc(T["th_contrast"])}</th>'
                f'<th>{esc(T["th_size"])}</th><th>{esc(T["th_text"])}</th></tr></thead><tbody>{rows}</tbody></table></div>') if rows else ""

    count_line = T["problems_n"].format(n=len(findings)) + (f" · {counts_txt}" if counts_txt else "") + (" · " + T["fixed_n"].format(n=fixed) if fixed else "")
    body = f"""
<header class="top">
  <nav class="bar"><span class="brand">design-critic</span>
    <a href="#summary">{esc(T['nav_summary'])}</a>{f'<a href="#before-after">{esc(T["nav_before_after"])}</a>' if cmp else ''}<a href="#problems">{esc(T['nav_problems'])} <b>{len(findings)}</b></a><a href="#screenshots">{esc(T['nav_screens'])}</a><a href="#technical">{esc(T['nav_tech'])}</a></nav>
</header>
<main>
<section id="summary" class="hero">
  <p class="site"><bdi>{esc(site)}</bdi> · {esc(date)}</p>
  <h1 dir="auto">{esc(page_title)}</h1>
  <div class="hero-grid">
    {score_html}
    <div class="verdict">
      <p class="v">{esc(fnd.get('verdict'))}</p>
      <p class="sum">{esc(fnd.get('summary'))}</p>
      <p class="count">{esc(count_line)}</p>
    </div>
  </div>
  {f'<div class="start"><h2>{esc(T["start_here"])}</h2><ol>{start_items}</ol></div>' if start_items else ''}
  <div class="duo">
    {f'<div class="card"><h2>{esc(T["areas_title"])}</h2><ul class="areas">{area_html}</ul></div>' if area_html else ''}
    {f'<div class="card"><h2>{esc(T["strengths_title"])}</h2><ul class="good">{strengths}</ul></div>' if strengths else ''}
  </div>
</section>
{compare_section(cmp, base, T) if cmp else ''}
<section id="problems">
  <div class="sec-head"><h2>{esc(T['all_problems'])}</h2><p>{esc(T['all_problems_sub'])}</p></div>
  <div class="filters" role="toolbar" aria-label="{esc(T['filter_label'])}">
    <button type="button" data-f-sev="all" class="on">{esc(T['all'])} <b>{len(findings)}</b></button>{sev_filters}
    {f'<span class="sep"></span>{dev_filters}' if dev_filters else ''}
    <button type="button" class="expand">{esc(T['expand'])}</button>
  </div>
  <div class="list">{''.join(cards) or f'<p>{esc(T["no_problems"])}</p>'}</div>
  <p class="empty" hidden>{esc(T['empty_filter'])}</p>
</section>

<section id="screenshots">
  <div class="sec-head"><h2>{esc(T['screens_title'])}</h2><p>{esc(T['screens_sub'])}</p></div>
  <div class="gallery">{''.join(gallery)}</div>
  {f'<div class="extras">{"".join(extras)}</div>' if extras else ''}
</section>

<section id="technical">
  <details class="appendix"><summary><h2>{esc(T['tech_title'])}</h2><span>{esc(T['tech_sub'])}</span></summary>
    {f'<h3>{esc(T["auto_alerts"])}</h3><ul class="flags">{flags}</ul>' if flags else ''}
    {contrast}
    <p class="src">{esc(T['analyzed'])} <code>{esc(url)}</code></p>
  </details>
</section>
</main>

<dialog id="lb"><div class="lb-bar"><span class="lb-t"></span><button type="button" class="lb-x" aria-label="✕">✕</button></div>
  <div class="lb-scroll" dir="ltr"><div class="lb-stage"><img alt=""><span class="mark"></span></div></div></dialog>"""
    js_strings = json.dumps({k: T[k] for k in ("expand", "collapse", "copy", "copied")}, ensure_ascii=False)
    return (TEMPLATE.replace("__LANG__", lang).replace("__DIR__", "rtl" if lang in RTL else "ltr")
            .replace("__TITLE__", esc(T["page_title"].format(title=page_title)))
            .replace("__I18N__", js_strings.replace("</", "<\\/")).replace("__BODY__", body))


TEMPLATE = """<!doctype html>
<html lang="__LANG__" dir="__DIR__"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__</title>
<style>
:root{--bg:#f5f4f0;--panel:#fff;--ink:#1c1b18;--muted:#68645c;--line:#e3e0d8;--soft:#efede7;--accent:#c2410c;
 --crit:#c62828;--high:#d9480f;--mid:#a67c00;--low:#5b6b7b;--ok:#2f855a;--warn:#b7791f;--bad:#c53030;--shadow:0 1px 2px rgb(0 0 0/.05),0 4px 16px rgb(0 0 0/.04)}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#131311;--panel:#1c1b19;--ink:#edebe6;--muted:#a29e95;--line:#302e2a;--soft:#262421;
 --accent:#fb8c4c;--crit:#f05252;--high:#ff8a4c;--mid:#e3b341;--low:#94a7ba;--ok:#5fbf8a;--warn:#e3b341;--bad:#f05252;--shadow:none}}
:root[data-theme="dark"]{--bg:#131311;--panel:#1c1b19;--ink:#edebe6;--muted:#a29e95;--line:#302e2a;--soft:#262421;
 --accent:#fb8c4c;--crit:#f05252;--high:#ff8a4c;--mid:#e3b341;--low:#94a7ba;--ok:#5fbf8a;--warn:#e3b341;--bad:#f05252;--shadow:none}
*{box-sizing:border-box}
html{scroll-behavior:smooth;scroll-padding-top:72px}
@media (prefers-reduced-motion:reduce){html{scroll-behavior:auto}*{transition:none!important}}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.6 ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;-webkit-font-smoothing:antialiased}
h1,h2,h3,h4{line-height:1.2;margin:0;text-wrap:balance}
a{color:inherit}
code,pre{font-family:ui-monospace,"SF Mono",Menlo,Consolas,monospace;font-size:13px}
button{font:inherit;color:inherit}
:focus-visible{outline:2px solid var(--accent);outline-offset:2px;border-radius:4px}

.top{position:sticky;top:0;z-index:5;background:color-mix(in srgb,var(--bg) 88%,transparent);backdrop-filter:blur(8px);border-bottom:1px solid var(--line)}
.bar{max-width:1080px;margin:0 auto;padding:0 20px;display:flex;gap:4px;align-items:center;height:52px;overflow-x:auto;white-space:nowrap}
.bar .brand{font-weight:700;margin-inline-end:auto;letter-spacing:-.01em}
.bar a{text-decoration:none;font-size:14px;color:var(--muted);padding:6px 10px;border-radius:6px}
.bar a:hover{background:var(--soft);color:var(--ink)}
.bar b{font-weight:600;color:var(--ink)}
main{max-width:1080px;margin:0 auto;padding:0 20px 96px}
section{padding-top:48px}

.hero .site{color:var(--muted);font-size:14px;margin:0 0 6px}
.hero h1{font-size:clamp(28px,4.5vw,44px);letter-spacing:-.025em;margin-bottom:24px}
.hero-grid{display:flex;gap:28px;align-items:flex-start}
.score{flex:none;width:150px;padding:18px;border-radius:14px;background:var(--panel);border:1px solid var(--line);box-shadow:var(--shadow);text-align:center}
.score-n{font-size:56px;font-weight:700;letter-spacing:-.04em;line-height:1}
.score-of{font-size:20px;color:var(--muted);margin-inline-start:2px}
.score-l{display:block;font-size:13px;font-weight:600;margin-top:8px}
.score.ok .score-n,.score.ok .score-l{color:var(--ok)}.score.warn .score-n,.score.warn .score-l{color:var(--warn)}.score.bad .score-n,.score.bad .score-l{color:var(--bad)}
.verdict .v{font-size:clamp(19px,2.4vw,23px);font-weight:600;line-height:1.35;margin:0 0 10px;letter-spacing:-.01em}
.verdict .sum{margin:0 0 10px;color:var(--muted);max-width:70ch}
.verdict .count{margin:0;font-size:14px;font-weight:600}

.start{margin-top:32px;background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:22px;box-shadow:var(--shadow)}
.start h2,.card h2{font-size:13px;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);margin-bottom:14px}
.start ol{list-style:none;margin:0;padding:0;display:grid;gap:8px}
.start a{display:flex;align-items:center;gap:14px;text-decoration:none;padding:10px 12px;border-radius:10px;background:var(--soft)}
.start a:hover{background:color-mix(in srgb,var(--accent) 10%,var(--soft))}
.st-t{font-weight:600;flex:1}
.st-m{font-size:13px;color:var(--muted);white-space:nowrap}

.duo{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:16px;margin-top:16px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:22px;box-shadow:var(--shadow)}
.areas{list-style:none;margin:0;padding:0;display:grid;gap:10px}
.areas li{display:flex;align-items:center;gap:10px}
.light{width:12px;height:12px;border-radius:50%;flex:none;background:var(--muted)}
.areas .ok .light{background:var(--ok)}.areas .warn .light{background:var(--warn)}.areas .bad .light{background:var(--bad)}
.a-name{flex:1}.a-state{font-size:13px;font-weight:600}
.areas .ok .a-state{color:var(--ok)}.areas .warn .a-state{color:var(--warn)}.areas .bad .a-state{color:var(--bad)}
.good{margin:0;padding:0;list-style:none;display:grid;gap:8px}
.good li{padding-inline-start:26px;position:relative}
.good li::before{content:"✓";position:absolute;inset-inline-start:0;color:var(--ok);font-weight:700}

.sec-head h2{font-size:clamp(22px,3vw,28px);letter-spacing:-.02em}
.sec-head p{color:var(--muted);margin:6px 0 0}
.filters{display:flex;flex-wrap:wrap;gap:8px;margin:20px 0 16px;align-items:center}
.filters button{border:1px solid var(--line);background:var(--panel);border-radius:999px;padding:6px 14px;font-size:14px;cursor:pointer}
.filters button:hover{border-color:var(--muted)}
.filters button.on{background:var(--ink);color:var(--bg);border-color:var(--ink)}
.filters button b{font-weight:600;margin-inline-start:2px;opacity:.7}
.filters .sep{width:1px;height:22px;background:var(--line);margin:0 4px}
.filters .expand{margin-inline-start:auto;border-style:dashed}

.num{flex:none;width:30px;height:30px;border-radius:50%;display:grid;place-items:center;font-weight:700;font-size:14px;color:#fff;background:var(--low)}
.num.sev-critical{background:var(--crit)}.num.sev-high{background:var(--high)}.num.sev-medium{background:var(--mid)}

.list{display:grid;gap:10px}
.f{background:var(--panel);border:1px solid var(--line);border-radius:14px;box-shadow:var(--shadow);overflow:hidden}
.f>summary{list-style:none;cursor:pointer;display:flex;gap:16px;align-items:center;padding:16px 18px}
.f>summary::-webkit-details-marker{display:none}
.f>summary:hover{background:color-mix(in srgb,var(--soft) 60%,transparent)}
.s-main{flex:1;min-width:0;display:grid;gap:3px}
.s-meta{font-size:12px;color:var(--muted);text-transform:uppercase;letter-spacing:.06em}
.sev-critical .sev-label{color:var(--crit);font-weight:700}.sev-high .sev-label{color:var(--high);font-weight:700}
.sev-medium .sev-label{color:var(--mid);font-weight:700}.sev-low .sev-label{color:var(--low);font-weight:700}
.s-title{font-size:17px;font-weight:600;line-height:1.3}
.s-tldr{color:var(--muted);font-size:15px}
.tags{display:flex;flex-wrap:wrap;gap:6px;margin-top:4px}
.tag{font-size:12px;padding:2px 9px;border-radius:999px;background:var(--soft);color:var(--muted)}
.tag.done{background:color-mix(in srgb,var(--ok) 15%,transparent);color:var(--ok);font-weight:600}
.is-done .s-title{text-decoration:line-through;text-decoration-color:var(--muted)}
.chev{flex:none;width:10px;height:10px;border-right:2px solid var(--muted);border-bottom:2px solid var(--muted);transform:rotate(45deg);margin:0 6px 4px}
.f[open] .chev{transform:rotate(-135deg);margin-bottom:-4px}
.crop{position:relative;display:block;overflow:hidden;border-radius:8px;background:var(--soft);border:1px solid var(--line)}
.crop img{position:absolute;max-width:none;display:block}
.crop .mark{position:absolute;border:2px solid var(--accent);border-radius:4px;box-shadow:0 0 0 9999px rgb(0 0 0/.25)}
.thumb{flex:none;width:150px}
.f[open] .thumb{visibility:hidden}
.f-body{display:grid;grid-template-columns:minmax(0,1.15fr) minmax(0,1fr);gap:28px;padding-block:4px 22px;padding-inline:64px 18px}
.shots{display:grid;gap:12px;align-content:start}
.shot{margin:0}.shot figcaption{font-size:12px;color:var(--muted);margin-top:4px}
.shot.after .mark{border-color:var(--ok)}
button.crop{width:100%;padding:0;cursor:zoom-in}
.zoom-hint{position:absolute;inset-inline-end:8px;bottom:8px;font-size:12px;background:rgb(0 0 0/.65);color:#fff;padding:2px 8px;border-radius:999px;opacity:0;transition:opacity .15s}
button.crop:hover .zoom-hint,button.crop:focus-visible .zoom-hint{opacity:1}
.explain h4{font-size:13px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);margin:0 0 4px}
.explain p{margin:0 0 16px}
.tech{border-top:1px solid var(--line);padding-top:12px}
.tech>summary{cursor:pointer;font-size:14px;font-weight:600;color:var(--muted)}
.tech>summary:hover{color:var(--ink)}
.where{font-size:14px;margin:12px 0 8px}
.where code{background:var(--soft);padding:2px 6px;border-radius:4px}
.code{position:relative;margin-top:8px}
.code pre{background:var(--soft);border-radius:8px;padding:14px;overflow:auto;margin:0;line-height:1.5}
.copy{position:absolute;top:8px;right:8px;font-size:12px;border:1px solid var(--line);background:var(--panel);border-radius:6px;padding:2px 10px;cursor:pointer}
.empty{color:var(--muted)}

.gallery{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:16px;margin-top:20px;align-items:start}
.extras{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:16px;margin-top:24px;align-items:start}
.gallery figure,.extras figure{margin:0}
.g-item{display:block;width:100%;padding:0;border:1px solid var(--line);border-radius:10px;overflow:hidden;background:var(--soft);cursor:zoom-in}
.g-item img,.extras video{display:block;width:100%}
.extras video{border-radius:10px;border:1px solid var(--line)}
.gallery figcaption,.extras figcaption{font-size:13px;color:var(--muted);margin-top:6px}

.appendix{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:18px 22px}
.appendix>summary{cursor:pointer;list-style:none;display:flex;align-items:baseline;gap:14px;flex-wrap:wrap}
.appendix>summary::-webkit-details-marker{display:none}
.appendix>summary h2{font-size:20px}
.appendix>summary span{color:var(--muted);font-size:14px}
.appendix h3{font-size:15px;margin:22px 0 10px}
.flags{margin:0;padding-inline-start:20px;font-size:14px}.flags li{margin:4px 0}
.table-wrap{overflow-x:auto}
table{width:100%;border-collapse:collapse;font-size:14px}
th,td{text-align:start;padding:8px 10px;border-bottom:1px solid var(--line);vertical-align:top}
th{font-size:12px;text-transform:uppercase;letter-spacing:.05em;color:var(--muted)}
.sw{display:inline-block;width:12px;height:12px;border-radius:3px;border:1px solid var(--line);vertical-align:-1px;margin-inline-end:4px}
.src{font-size:13px;color:var(--muted);margin-top:18px;word-break:break-all}
.missing{font-size:13px;color:var(--muted)}

dialog#lb{width:min(1200px,96vw);height:92vh;max-height:92vh;padding:0;border:0;border-radius:14px;background:var(--panel);color:var(--ink);overflow:hidden}
dialog#lb::backdrop{background:rgb(0 0 0/.7)}
dialog#lb[open]{display:flex;flex-direction:column}
.lb-bar{display:flex;justify-content:space-between;align-items:center;padding:10px 16px;border-bottom:1px solid var(--line);font-size:14px;color:var(--muted)}
.lb-x{border:0;background:var(--soft);width:32px;height:32px;border-radius:50%;cursor:pointer}
.lb-scroll{overflow:auto;flex:1;background:var(--soft)}
.lb-stage{position:relative;margin:0 auto}
.lb-stage img{display:block;width:100%}
.lb-stage .mark{position:absolute;border:3px solid var(--accent);border-radius:4px;box-shadow:0 0 0 9999px rgb(0 0 0/.3)}

/* Before and after */
#before-after h3{font-size:17px;margin:32px 0 4px}
.muted{color:var(--muted);margin:0}
.c-head{margin:20px 0 16px;padding:12px 16px;border-radius:10px;font-weight:600}
.c-head.ok{background:color-mix(in srgb,var(--ok) 12%,transparent);color:var(--ok)}
.c-head.bad{background:color-mix(in srgb,var(--bad) 12%,transparent);color:var(--bad)}
.cmp{list-style:none;margin:0;padding:0;display:grid}
.cmp li{display:flex;gap:12px;align-items:center;padding:10px 0;border-bottom:1px solid var(--line)}
.cmp li:last-child{border-bottom:0}
.c-icon{flex:none;width:26px;height:26px;border-radius:50%;display:grid;place-items:center;font-weight:700;background:var(--soft);color:var(--muted)}
.cmp .ok .c-icon{background:color-mix(in srgb,var(--ok) 15%,transparent);color:var(--ok)}
.cmp .bad .c-icon{background:color-mix(in srgb,var(--bad) 15%,transparent);color:var(--bad)}
.c-main{flex:1;min-width:0;display:flex;flex-direction:column}
.c-scope,.c-new{font-size:13px;color:var(--muted)}.c-new code{word-break:break-all}
.c-val{flex:none;font-variant-numeric:tabular-nums;color:var(--muted)}.c-val b{color:var(--ink)}
.cmp .bad .c-val b{color:var(--bad)}.cmp .ok .c-val b{color:var(--ok)}
.c-same{margin-top:8px;border-top:1px solid var(--line);padding-top:10px}
.c-same>summary{cursor:pointer;font-size:14px;color:var(--muted)}
.bad-list{margin:0;padding:0;list-style:none;display:grid;gap:8px}
.bad-list li{padding-inline-start:26px;position:relative}
.bad-list li::before{content:"✗";position:absolute;inset-inline-start:0;color:var(--bad);font-weight:700}
.pair{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.ba-wrap{position:relative;border-radius:8px;overflow:hidden}
.ba-layer{position:absolute!important;inset:0}
.ba-after .mark{border-color:var(--ok)}
.ba-line{position:absolute;top:0;bottom:0;width:2px;margin-left:-1px;background:#fff;box-shadow:0 0 0 1px rgb(0 0 0/.35);pointer-events:none}
.ba-line::after{content:"⇆";position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);width:30px;height:30px;border-radius:50%;background:#fff;color:#222;display:grid;place-items:center;font-size:15px;box-shadow:0 1px 4px rgb(0 0 0/.35)}
.ba-tag{position:absolute;top:8px;font-size:12px;background:rgb(0 0 0/.65);color:#fff;padding:2px 8px;border-radius:999px;pointer-events:none}
.ba-tag.l{left:8px}.ba-tag.r{right:8px}
.ba-range{position:absolute;inset:0;width:100%;height:100%;margin:0;opacity:0;cursor:ew-resize}
.ba-wrap:has(.ba-range:focus-visible){outline:2px solid var(--accent);outline-offset:2px}
.linky{border:0;background:none;padding:0;margin-inline-start:8px;font:inherit;color:var(--accent);cursor:zoom-in;text-decoration:underline;text-underline-offset:2px}
@media (max-width:760px){
 .hero-grid{flex-direction:column;gap:16px}
 .score{width:auto;display:flex;align-items:baseline;gap:6px;text-align:start;padding:14px 18px}
 .score-n{font-size:40px}.score-l{margin:0;margin-inline-start:auto}
 .thumb{display:none}
 .f>summary{padding:14px;gap:12px;align-items:flex-start}
 .f-body{grid-template-columns:1fr;padding:0 14px 18px}
 .st-m{display:none}
 .bar .brand{display:none}
 .bar{padding:0 12px}
 .filters .expand{margin-inline-start:0}
 .pair{grid-template-columns:1fr}
}
</style></head>
<body>
__BODY__
<script>
(() => {
  const T_ = __I18N__;
  const $ = (s, r = document) => r.querySelector(s), $$ = (s, r = document) => [...r.querySelectorAll(s)];
  // Filters
  let fs = 'all', fd = null;
  const apply = () => {
    let shown = 0;
    $$('.f').forEach(f => {
      const ok = (fs === 'all' || f.dataset.sev === fs) && (!fd || f.dataset.dev.split(' ').includes(fd));
      f.hidden = !ok; if (ok) shown++;
    });
    $('.empty').hidden = shown > 0;
  };
  $$('[data-f-sev]').forEach(b => b.addEventListener('click', () => {
    fs = b.dataset.fSev; $$('[data-f-sev]').forEach(x => x.classList.toggle('on', x === b)); apply();
  }));
  $$('[data-f-dev]').forEach(b => b.addEventListener('click', () => {
    fd = fd === b.dataset.fDev ? null : b.dataset.fDev;
    $$('[data-f-dev]').forEach(x => x.classList.toggle('on', x.dataset.fDev === fd)); apply();
  }));
  const ex = $('.expand');
  if (ex) ex.addEventListener('click', () => {
    const open = ex.dataset.open !== '1'; ex.dataset.open = open ? '1' : '';
    $$('.f').forEach(f => f.open = open); ex.textContent = open ? T_.collapse : T_.expand;
  });
  // "Start here" opens the problem
  $$('[data-open]').forEach(a => a.addEventListener('click', () => { const f = document.getElementById(a.dataset.open); if (f) f.open = true; }));
  if (location.hash) { const f = document.getElementById(location.hash.slice(1)); if (f && f.tagName === 'DETAILS') f.open = true; }
  // Copy code
  $$('.copy').forEach(b => b.addEventListener('click', async () => {
    const t = b.nextElementSibling.innerText;
    try { await navigator.clipboard.writeText(t); } catch (e) {
      const r = document.createRange(); r.selectNodeContents(b.nextElementSibling); getSelection().removeAllRanges(); getSelection().addRange(r); document.execCommand('copy');
    }
    b.textContent = T_.copied; setTimeout(() => b.textContent = T_.copy, 1500);
  }));
  // Before/after slider
  $$('.ba-range').forEach(r => r.addEventListener('input', () => {
    const w = r.closest('.ba-wrap');
    $('.ba-after', w).style.clipPath = `inset(0 0 0 ${r.value}%)`; $('.ba-line', w).style.left = r.value + '%';
  }));
  // Screenshot lightbox
  const lb = $('#lb'), img = $('img', lb), mark = $('.mark', lb), stage = $('.lb-stage', lb);
  $$('.zoom').forEach(b => b.addEventListener('click', () => {
    const [iw, ih] = (b.dataset.size || '1,1').split(',').map(Number);
    img.src = b.dataset.full; $('.lb-t', lb).textContent = b.dataset.title || b.closest('figure')?.querySelector('figcaption')?.textContent || '';
    stage.style.maxWidth = Math.max(iw, 360) + 'px';
    if (b.dataset.box) {
      const [x, y, w, h] = b.dataset.box.split(',').map(Number);
      Object.assign(mark.style, {display: 'block', left: 100 * x / iw + '%', top: 100 * y / ih + '%', width: 100 * w / iw + '%', height: 100 * h / ih + '%'});
    } else mark.style.display = 'none';
    lb.showModal();
    const go = () => { if (b.dataset.box) mark.scrollIntoView({block: 'center', inline: 'center'}); else $('.lb-scroll', lb).scrollTop = 0; };
    img.complete ? go() : img.addEventListener('load', go, {once: true});
  }));
  $('.lb-x', lb).addEventListener('click', () => lb.close());
  lb.addEventListener('click', e => { if (e.target === lb) lb.close(); });
})();
</script>
</body></html>"""


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dir", help="output directory of capture.py")
    ap.add_argument("--findings", default="findings.json")
    ap.add_argument("--out", default="report.html")
    ap.add_argument("--lang", help="report UI language (ISO 639-1); overrides \"lang\" in findings.json")
    ap.add_argument("--after", metavar="DIR", help="capture taken after the fixes (with comparison.json from compare.py); "
                    "copied into <dir>/after/ and adds the \"Before and after\" section")
    args = ap.parse_args()
    base = Path(args.dir)
    cmp = None
    if args.after:
        src, dst = Path(args.after).resolve(), (base / "after").resolve()
        if src != dst:
            shutil.copytree(src, dst, dirs_exist_ok=True)
        cpath = dst / "comparison.json"
        if cpath.exists():
            cmp = json.loads(cpath.read_text(encoding="utf-8"))
        else:
            print(f"warning: {cpath} does not exist; run compare.py {base} {args.after} first")
    cap = json.loads((base / "capture.json").read_text(encoding="utf-8"))
    fpath = Path(args.findings) if Path(args.findings).is_absolute() else base / args.findings
    fnd = normalize(json.loads(fpath.read_text(encoding="utf-8")))
    T, lang = strings(args.lang or fnd.get("lang"))
    problems = []
    ids = {f.get("id") for f in fnd.get("findings", [])}
    for f in fnd.get("findings", []):
        for e in f.get("evidence", []) + ([f["after"]] if f.get("after") else []):
            if e.get("shot") and not (base / e["shot"]).exists():
                problems.append(f"{f.get('id')}: {e['shot']} does not exist")
        if f.get("severity") not in SEV_ORDER:
            problems.append(f"{f.get('id')}: invalid severity '{f.get('severity')}' (use {', '.join(SEV_ORDER)})")
        for k in ("title", "tldr", "problem", "fix"):
            if not f.get(k):
                problems.append(f"{f.get('id')}: missing '{k}'")
    for fid in fnd.get("start_here", []):
        if fid not in ids:
            problems.append(f"start_here: finding {fid} does not exist")
    out = base / args.out
    out.write_text(render(cap, fnd, base, T, lang, cmp), encoding="utf-8")
    for p in problems:
        print("warning:", p)
    print(out.resolve())


if __name__ == "__main__":
    main()
