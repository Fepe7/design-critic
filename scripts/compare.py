#!/usr/bin/env python3
"""Compare two design-critic captures (before and after applying fixes).

  python compare.py .design-critic/site .design-critic/site-after

Writes <after>/comparison.json and diff/<viewport>.png (changed pixels in red over the
"before" screenshot in grey), and prints a summary:

  ✓ better   = same   ✗ worse (regression: check it before calling the fix done)

Metrics are compared for every viewport and color scheme present in both captures, so
you can recapture only what was affected (e.g. --viewports mobile --no-motion).
Exits with code 1 if anything got worse or a new alert appeared.
"""
import argparse
import base64
import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

# (key, plain label, function extracting the number; lower is better)
# build_report.py translates the labels by key; these are the English fallback.
METRICS = [
    ("horizontal_scroll", "Horizontal scroll", lambda m: 1 if m.get("horizontal_scroll") else 0),
    ("overflow", "Elements that spill off the screen", lambda m: len(m.get("overflowing_elements", []))),
    ("truncated", "Cut-off text", lambda m: sum(1 for t in m.get("truncated_text", []) if not t.get("intentional_ellipsis"))),
    ("contrast", "Low-contrast text", lambda m: sum(c.get("count", 1) for c in m.get("contrast", {}).get("failures", []))),
    ("tap24", "Buttons/links smaller than 24px", lambda m: m.get("tap_targets", {}).get("under_24", 0)),
    ("tap44", "Buttons/links smaller than 44px", lambda m: m.get("tap_targets", {}).get("under_44", 0)),
    ("noname", "Controls without an accessible name", lambda m: len(m.get("missing_accessible_name", []))),
    ("noalt", "Images without alt text", lambda m: len(m.get("images", {}).get("missing_alt", []))),
    ("broken", "Broken images", lambda m: len(m.get("images", {}).get("broken", []))),
    ("shrunk", "Page shrunk to fit", lambda m: 1 if m.get("meta", {}).get("rendered_width", 0) > m.get("meta", {}).get("layout_width", 1e9) else 0),
]
TOUCH_ONLY = {"tap24", "tap44"}
# Counting isn't enough for these metrics: if one element stops failing but a different one
# starts failing, it's a regression even though the number goes down.
IDENTITY = {
    "overflow": lambda m: {o["selector"] for o in m.get("overflowing_elements", [])},
    "truncated": lambda m: {t["selector"] for t in m.get("truncated_text", []) if not t.get("intentional_ellipsis")},
    "contrast": lambda m: {f"{c['fg']} on {c['bg']}" for c in m.get("contrast", {}).get("failures", [])},
    "noname": lambda m: {n["selector"] for n in m.get("missing_accessible_name", [])},
}

DIFF_JS = r"""
async ([a, b]) => {
  const load = src => new Promise((res, rej) => { const i = new Image(); i.onload = () => res(i); i.onerror = rej; i.src = src; });
  const [ia, ib] = await Promise.all([load(a), load(b)]);
  const w = Math.max(ia.width, ib.width), h = Math.max(ia.height, ib.height);
  const ctx = (img) => { const c = document.createElement('canvas'); c.width = w; c.height = h; const x = c.getContext('2d');
    x.fillStyle = '#fff'; x.fillRect(0, 0, w, h); x.drawImage(img, 0, 0); return x; };
  const da = ctx(ia).getImageData(0, 0, w, h).data, db = ctx(ib).getImageData(0, 0, w, h).data;
  const out = document.createElement('canvas'); out.width = w; out.height = h;
  const ox = out.getContext('2d'); const od = ox.createImageData(w, h);
  let changed = 0; const CELL = 24; const cells = new Set();
  for (let i = 0; i < da.length; i += 4) {
    const d = Math.abs(da[i] - db[i]) + Math.abs(da[i + 1] - db[i + 1]) + Math.abs(da[i + 2] - db[i + 2]);
    const g = 0.3 * da[i] + 0.59 * da[i + 1] + 0.11 * da[i + 2];
    if (d > 40) {
      changed++; od.data[i] = 230; od.data[i + 1] = 40; od.data[i + 2] = 40; od.data[i + 3] = 255;
      const p = i / 4; cells.add(Math.floor((p % w) / CELL) + ',' + Math.floor(Math.floor(p / w) / CELL));
    } else { const v = 200 + g * 0.2; od.data[i] = od.data[i + 1] = od.data[i + 2] = v; od.data[i + 3] = 255; }
  }
  ox.putImageData(od, 0, 0);
  // Group changed cells into vertical bands to describe where the changes are
  const rows = [...new Set([...cells].map(c => +c.split(',')[1]))].sort((x, y) => x - y);
  const bands = []; for (const r of rows) { const last = bands[bands.length - 1]; if (last && r - last[1] <= 2) last[1] = r; else bands.push([r, r]); }
  return {png: out.toDataURL('image/png'), changed_pct: +(100 * changed / (w * h)).toFixed(2),
          size_changed: ia.width !== ib.width || ia.height !== ib.height, before: [ia.width, ia.height], after: [ib.width, ib.height],
          bands_px: bands.map(([a, b]) => [a * CELL, (b + 1) * CELL])};
}
"""


def data_url(path):
    return "data:image/png;base64," + base64.b64encode(Path(path).read_bytes()).decode()


def global_counts(cap):
    s = cap.get("states") or {}
    mo = cap.get("motion") or {}
    out = {}
    if s:
        out["hover"] = ("Elements with no hover feedback", sum(1 for h in s.get("hover", []) if not h.get("visual_change_on_hover")))
        out["focus"] = ("Elements with an invisible keyboard focus", sum(1 for f in s.get("focus_order", []) if not (f.get("outline") or f.get("box_shadow"))))
    if mo.get("reduced_motion"):
        out["reduced"] = ("Ignores “reduce motion”", 0 if mo["reduced_motion"].get("respected") else 1)
    if mo:
        anims = {(a["target"], a["name"]): a for a in mo.get("animations_on_load", []) + mo.get("animations_on_scroll", [])}.values()
        out["layout_anim"] = ("Animations that move the layout", sum(1 for a in anims if a.get("animates_layout")))
        out["infinite"] = ("Infinite animations", sum(1 for a in anims if a.get("iterations") == "infinite"))
    return out


def verdict(a, b):
    return "better" if b < a else ("worse" if b > a else "same")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("before")
    ap.add_argument("after")
    args = ap.parse_args()
    A, B = Path(args.before), Path(args.after)
    ca = json.loads((A / "capture.json").read_text(encoding="utf-8"))
    cb = json.loads((B / "capture.json").read_text(encoding="utf-8"))

    rows = []
    common = [k for k in cb["captures"] if k in ca["captures"]]
    for key in common:
        ma, mb = ca["captures"][key]["metrics"], cb["captures"][key]["metrics"]
        for mid, label, fn in METRICS:
            if mid in TOUCH_ONLY and key.startswith("desktop"):
                continue
            a, b = fn(ma), fn(mb)
            if a == 0 and b == 0:
                continue
            row = {"scope": key, "metric": mid, "label": label, "before": a, "after": b, "status": verdict(a, b)}
            if mid in IDENTITY:
                new = sorted(IDENTITY[mid](mb) - IDENTITY[mid](ma))
                if new:
                    row["new"] = new[:5]
                    row["status"] = "worse"
            rows.append(row)
    ga, gb = global_counts(ca), global_counts(cb)
    for mid in gb:
        if mid in ga:
            label, a = ga[mid]
            b = gb[mid][1]
            if a or b:
                rows.append({"scope": "global", "metric": mid, "label": label, "before": a, "after": b, "status": verdict(a, b)})

    flags_a, flags_b = set(ca.get("flags", [])), set(cb.get("flags", []))
    result = {"before": str(A.resolve()), "after": str(B.resolve()), "metrics": rows,
              "resolved_flags": sorted(flags_a - flags_b), "new_flags": sorted(flags_b - flags_a), "diffs": []}

    diff_dir = B / "diff"
    diff_dir.mkdir(exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.set_content("<!doctype html><title>diff</title>")
        for key in common:
            fa, fb = ca["captures"][key].get("fold"), cb["captures"][key].get("fold")
            if not (fa and fb and (A / fa).exists() and (B / fb).exists()):
                continue
            d = page.evaluate(DIFF_JS, [data_url(A / fa), data_url(B / fb)])
            out = diff_dir / f"{key}.png"
            out.write_bytes(base64.b64decode(d.pop("png").split(",", 1)[1]))
            result["diffs"].append({"scope": key, "diff": str(out.relative_to(B)), "before": str(A / fa), "after": str(B / fb), **d})
        browser.close()

    (B / "comparison.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")

    icon = {"better": "✓", "same": "=", "worse": "✗"}
    print("Comparison before → after\n")
    for r in sorted(rows, key=lambda r: {"worse": 0, "better": 1, "same": 2}[r["status"]]):
        tail = "   ← REGRESSION, check it" if r["status"] == "worse" else ""
        if r.get("new"):
            tail += f" (new: {', '.join(r['new'][:3])})"
        print(f"  {icon[r['status']]} [{r['scope']}] {r['label']}: {r['before']} → {r['after']}{tail}")
    for f in result["resolved_flags"]:
        print(f"  ✓ resolved alert: {f[:110]}")
    for f in result["new_flags"]:
        print(f"  ✗ NEW alert: {f[:110]}")
    if result["diffs"]:
        print("\nVisual changes above the fold (see diff/*.png: changes in red):")
        for d in result["diffs"]:
            where = ", ".join(f"y={a}-{b}px" for a, b in d["bands_px"][:4]) or "none"
            print(f"  · {d['scope']}: {d['changed_pct']}% of pixels changed ({where})")
    print(f"\nDetails: {B / 'comparison.json'}")
    worse = [r for r in rows if r["status"] == "worse"] + result["new_flags"]
    sys.exit(1 if worse else 0)


if __name__ == "__main__":
    main()
