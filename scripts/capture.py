#!/usr/bin/env python3
"""Capture a website for design-critic.

Writes, into an output directory:
  shots/    above-the-fold + full-page screenshots per viewport and color scheme,
            plus "slices" (one screen tall each) in light mode for close inspection
  states/   hover and keyboard focus of the main interactive elements + summary sheet
  motion/   frames of the entrance animation and of scrolling + summary sheet + .webm video
  capture.json  automatic metrics (WCAG contrast, overflow, tap targets, typography,
                spacing, animations, prefers-reduced-motion, network/console errors...)

Usage:
  python capture.py https://example.com --out .design-critic/example
  python capture.py ./index.html            (local path -> file://)
  python capture.py http://localhost:5173 --viewports mobile,desktop --no-motion
"""
import argparse
import json
import re
import shutil
import struct
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

VIEWPORTS = {
    "mobile": {"width": 390, "height": 844, "is_mobile": True, "has_touch": True},
    "tablet": {"width": 820, "height": 1180, "is_mobile": True, "has_touch": True},
    "desktop": {"width": 1440, "height": 900, "is_mobile": False, "has_touch": False},
}
MAX_FULL_HEIGHT = 16000
MAX_SLICES = 8

# --------------------------------------------------------------------------- JS

# Page metrics. Returns only concrete problems + bounded inventories,
# so the JSON stays readable for a model.
METRICS_JS = r"""
(opts) => {
  // clientWidth is the real layout width. innerWidth grows on mobile when content overflows
  // (Chrome shrinks the page to fit), so it can't be used to detect overflow.
  const vw = document.documentElement.clientWidth, vh = innerHeight;
  const out = {};
  const LIMIT = 20;

  const cv = document.createElement('canvas'); cv.width = cv.height = 1;
  const cx = cv.getContext('2d', {willReadFrequently: true});
  const cache = new Map();
  // Converts any CSS color (rgb, oklch, color(), hsl...) to sRGBA via canvas.
  const rgba = (c) => {
    if (cache.has(c)) return cache.get(c);
    cx.clearRect(0, 0, 1, 1); cx.fillStyle = '#000'; cx.fillStyle = c; cx.fillRect(0, 0, 1, 1);
    const d = cx.getImageData(0, 0, 1, 1).data;
    const v = [d[0], d[1], d[2], d[3] / 255]; cache.set(c, v); return v;
  };
  const hex = (c) => '#' + c.slice(0, 3).map(v => Math.round(v).toString(16).padStart(2, '0')).join('');
  const blend = (top, bot) => {
    const a = top[3] + bot[3] * (1 - top[3]);
    if (a === 0) return [0, 0, 0, 0];
    return [0, 1, 2].map(i => (top[i] * top[3] + bot[i] * bot[3] * (1 - top[3])) / a).concat([a]);
  };
  const lum = (c) => {
    const f = v => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
    return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]);
  };
  const ratio = (a, b) => { const l1 = lum(a), l2 = lum(b); return (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05); };

  const rootScheme = getComputedStyle(document.documentElement).colorScheme || '';
  const darkCanvas = rootScheme.includes('dark') && matchMedia('(prefers-color-scheme: dark)').matches;
  const CANVAS = darkCanvas ? [18, 18, 18, 1] : [255, 255, 255, 1];

  // Effective background of an element. If there's a gradient below, returns all its colors
  // (to compute the worst case); if there's an image, marks it as "image" (not measurable).
  const COLOR_RE = /(rgba?|hsla?|oklch|oklab|lab|lch|color)\([^()]*\)|#[0-9a-f]{3,8}\b/gi;
  const bgOf = (el) => {
    const layers = []; let kind = 'solid'; let base = [CANVAS]; let e = el;
    while (e && e.nodeType === 1) {
      const cs = getComputedStyle(e);
      const c = rgba(cs.backgroundColor);
      if (c[3] > 0) layers.push(c);
      if (c[3] >= 1) break;
      const bi = cs.backgroundImage;
      if (bi && bi !== 'none') {
        const stops = /gradient\(/.test(bi) ? (bi.match(COLOR_RE) || []).map(rgba).filter(s => s[3] > 0) : [];
        if (stops.length) { base = stops.map(s => blend(s, CANVAS)); kind = 'gradient'; break; }
        if (/url\(/.test(bi)) kind = 'image';
      }
      e = e.parentElement;
    }
    const colors = base.map(b => { let res = b; for (let i = layers.length - 1; i >= 0; i--) res = blend(layers[i], res); return res; });
    return {color: colors[0], colors, kind, uncertain: kind !== 'solid'};
  };

  const visible = (el) => {
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden' || parseFloat(cs.opacity) === 0) return false;
    const r = el.getBoundingClientRect();
    return r.width > 2 && r.height > 2;
  };
  const sel = (el) => {
    const parts = []; let e = el; let depth = 0;
    while (e && e.nodeType === 1 && depth < 4) {
      let s = e.tagName.toLowerCase();
      if (e.id) { parts.unshift(s + '#' + e.id); break; }
      const cls = [...e.classList].filter(c => !/[:\[\]\/()%.@!]/.test(c)).slice(0, 2);
      if (cls.length) s += '.' + cls.join('.');
      parts.unshift(s); e = e.parentElement; depth++;
    }
    return parts.join(' > ');
  };
  const box = (el) => {
    const r = el.getBoundingClientRect();
    return [Math.round(r.left + scrollX), Math.round(r.top + scrollY), Math.round(r.width), Math.round(r.height)];
  };
  const ownText = (el) => [...el.childNodes].filter(n => n.nodeType === 3).map(n => n.textContent).join(' ').replace(/\s+/g, ' ').trim();
  const snip = (s, n = 60) => s.length > n ? s.slice(0, n) + '…' : s;

  const all = [...document.body.querySelectorAll('*')].slice(0, 6000);
  const textEls = all.filter(el => !['SCRIPT', 'STYLE', 'NOSCRIPT', 'TEMPLATE', 'SVG'].includes(el.tagName) && ownText(el) && visible(el));

  // ---- WCAG contrast
  const contrast = new Map(); let checked = 0; const gradientText = [], overImage = [];
  for (const el of textEls) {
    const cs = getComputedStyle(el);
    const fgRaw = rgba(cs.color);
    // Gradient text (background-clip: text): the color is transparent, it can't be measured.
    if (fgRaw[3] === 0 || /text/.test(cs.webkitBackgroundClip || cs.backgroundClip || '')) {
      if (gradientText.length < 10) gradientText.push({selector: sel(el), text: snip(ownText(el)), box: box(el)});
      continue;
    }
    const bg = bgOf(el);
    if (bg.kind === 'image') { if (overImage.length < 10) overImage.push({selector: sel(el), text: snip(ownText(el)), box: box(el)}); continue; }
    const size = parseFloat(cs.fontSize), weight = parseInt(cs.fontWeight) || 400;
    const large = size >= 24 || (size >= 18.66 && weight >= 700);
    const need = large ? 3 : 4.5;
    // With a gradient, measure against each of its colors and keep the worst.
    let worst = null;
    for (const b of bg.colors) {
      const r = ratio(blend(fgRaw, b), b);
      if (!worst || r < worst.r) worst = {r, b};
    }
    checked++;
    if (worst.r < need) {
      const fg = blend(fgRaw, worst.b);
      const key = hex(fg) + '|' + hex(worst.b) + '|' + Math.round(size);
      if (contrast.has(key)) { contrast.get(key).count++; continue; }
      contrast.set(key, {ratio: +worst.r.toFixed(2), needed: need, fg: hex(fg), bg: hex(worst.b), font_size: size,
        weight, bg_kind: bg.kind, text: snip(ownText(el)), selector: sel(el), box: box(el), count: 1});
    }
  }
  out.contrast = {checked, failures: [...contrast.values()].sort((a, b) => a.ratio - b.ratio).slice(0, 30),
    gradient_text: gradientText, text_over_image_unmeasured: overImage};

  // ---- Overflow / responsive
  const docW = document.documentElement.scrollWidth;
  out.horizontal_scroll = docW > vw + 1 ? {scroll_width: docW, viewport: vw} : null;
  const clips = (el) => {
    let e = el.parentElement;
    while (e && e !== document.body && e !== document.documentElement) {
      const ox = getComputedStyle(e).overflowX;
      if (ox !== 'visible') return true;
      e = e.parentElement;
    }
    return false;
  };
  function* ancestors(el) { for (let e = el; e && e !== document.body; e = e.parentElement) yield e; }
  const overflow = [];
  for (const el of all) {
    if (overflow.length >= 15) break;
    if (!visible(el)) continue;
    const r = el.getBoundingClientRect();
    const partial = (r.right > vw + 1 && r.left < vw) || (r.left < -1 && r.right > 0);
    // Fully off to the right also counts (grid columns spilling out), except
    // off-canvas menus with position: fixed, which are outside on purpose.
    const offRight = r.left >= vw && !el.closest('[style*="fixed"]') && ![...ancestors(el)].some(a => getComputedStyle(a).position === 'fixed');
    if (!(partial || offRight) || clips(el)) continue;
    if (overflow.some(o => o._el.contains(el))) continue;
    overflow.push({_el: el, selector: sel(el), box: box(el), right: Math.round(r.right), viewport: vw, text: snip(el.innerText || '', 40)});
  }
  out.overflowing_elements = overflow.map(({_el, ...o}) => o);

  const truncated = [];
  for (const el of textEls) {
    if (truncated.length >= 15) break;
    const cs = getComputedStyle(el);
    if (!/hidden|clip/.test(cs.overflow + cs.overflowX + cs.overflowY)) continue;
    const cut = el.scrollWidth > el.clientWidth + 1 || el.scrollHeight > el.clientHeight + 1;
    if (!cut) continue;
    const intentional = cs.textOverflow === 'ellipsis' || (cs.webkitLineClamp && cs.webkitLineClamp !== 'none');
    truncated.push({selector: sel(el), text: snip(ownText(el)), intentional_ellipsis: !!intentional, box: box(el)});
  }
  out.truncated_text = truncated;

  // ---- Tap targets and accessible names
  const interactive = [...document.querySelectorAll('a[href],button,input:not([type=hidden]),select,textarea,summary,[role=button],[role=link],[tabindex]:not([tabindex="-1"])')].filter(visible);
  const small = [], noName = [];
  for (const el of interactive) {
    const r = el.getBoundingClientRect(); const cs = getComputedStyle(el);
    const inline = cs.display === 'inline' && el.parentElement && ownText(el.parentElement).length > 0;
    if (!inline && (r.width < 44 || r.height < 44)) {
      small.push({selector: sel(el), w: Math.round(r.width), h: Math.round(r.height),
        level: (r.width < 24 || r.height < 24) ? 'fail (<24px, WCAG 2.5.8)' : 'warning (<44px)',
        text: snip((el.innerText || el.getAttribute('aria-label') || '').trim(), 30), box: box(el)});
    }
    const name = (el.innerText || '').trim() || el.getAttribute('aria-label') || el.getAttribute('title') ||
      (el.getAttribute('aria-labelledby') && 'labelledby') || [...el.querySelectorAll('img[alt]')].map(i => i.alt).join('') ||
      (el.labels && el.labels.length ? 'label' : '') || el.querySelector('svg title')?.textContent || el.value || '';
    if (!name.trim()) noName.push({selector: sel(el), tag: el.tagName.toLowerCase(), box: box(el)});
  }
  const failsFirst = small.sort((a, b) => (a.level < b.level ? -1 : 1));
  out.tap_targets = {total_interactive: interactive.length, under_44: small.length,
    under_24: small.filter(s => s.level.startsWith('fail')).length, examples: failsFirst.slice(0, 40)};
  out.missing_accessible_name = noName.slice(0, LIMIT);

  // ---- Images
  const imgs = [...document.images].filter(visible);
  out.images = {
    total: imgs.length,
    missing_alt: imgs.filter(i => !i.hasAttribute('alt')).slice(0, LIMIT).map(i => ({src: snip(i.currentSrc || i.src, 80), box: box(i)})),
    upscaled_blurry: imgs.filter(i => i.naturalWidth && i.naturalWidth * devicePixelRatio < i.getBoundingClientRect().width * 0.9)
      .slice(0, 10).map(i => ({src: snip(i.currentSrc || i.src, 80), natural: i.naturalWidth, rendered: Math.round(i.getBoundingClientRect().width), box: box(i)})),
    oversized: imgs.filter(i => i.naturalWidth > 3 * i.getBoundingClientRect().width * devicePixelRatio && i.naturalWidth > 1200)
      .slice(0, 10).map(i => ({src: snip(i.currentSrc || i.src, 80), natural: i.naturalWidth, rendered: Math.round(i.getBoundingClientRect().width)})),
    broken: [...document.images].filter(i => i.complete && i.naturalWidth === 0 && (i.currentSrc || i.src)).slice(0, 10).map(i => snip(i.currentSrc || i.src, 80)),
  };

  // ---- Headings
  const hs = [...document.querySelectorAll('h1,h2,h3,h4,h5,h6')].filter(visible);
  const outline = hs.map(h => ({level: +h.tagName[1], text: snip(h.innerText.trim(), 70), font_size: parseFloat(getComputedStyle(h).fontSize)}));
  const skips = [];
  for (let i = 1; i < outline.length; i++) if (outline[i].level > outline[i - 1].level + 1) skips.push(`h${outline[i - 1].level} → h${outline[i].level}: "${outline[i].text}"`);
  out.headings = {h1_count: outline.filter(h => h.level === 1).length, outline: outline.slice(0, 40), skipped_levels: skips.slice(0, 10)};

  out.meta = {
    title: document.title, lang: document.documentElement.lang || null,
    viewport_meta: document.querySelector('meta[name=viewport]')?.content || null,
    color_scheme_css: rootScheme || null,
    page_bg: hex(bgOf(document.body).color),
    page_height: document.documentElement.scrollHeight,
    layout_width: vw, rendered_width: innerWidth,
  };

  if (opts.inventory) {
    const count = (m, k) => m.set(k, (m.get(k) || 0) + 1);
    const top = (m, n) => [...m.entries()].sort((a, b) => b[1] - a[1]).slice(0, n).map(([k, v]) => ({value: k, count: v}));
    const fam = new Map(), sizes = new Map(), weights = new Map(), colors = new Map(), lh = [];
    for (const el of textEls) {
      const cs = getComputedStyle(el);
      count(fam, cs.fontFamily.split(',')[0].replace(/["']/g, '').trim());
      count(sizes, Math.round(parseFloat(cs.fontSize)) + 'px');
      count(weights, cs.fontWeight);
      count(colors, hex(rgba(cs.color)));
    }
    // Line length and line-height of running-text paragraphs
    const longLines = [];
    for (const p of document.querySelectorAll('p, li, dd, blockquote')) {
      if (!visible(p)) continue;
      const t = (p.innerText || '').trim(); if (t.length < 120) continue;
      const cs = getComputedStyle(p); const fs = parseFloat(cs.fontSize);
      const lhv = cs.lineHeight === 'normal' ? 1.2 : parseFloat(cs.lineHeight) / fs;
      lh.push(+lhv.toFixed(2));
      const chars = Math.round(p.getBoundingClientRect().width / (fs * 0.5));
      if (chars > 85 || chars < 30) longLines.push({selector: sel(p), approx_chars_per_line: chars, font_size: fs, text: snip(t, 50)});
    }
    const bgs = new Map(), spacing = new Map(), radii = new Map(); let shadows = 0;
    for (const el of all) {
      if (!visible(el)) continue;
      const cs = getComputedStyle(el);
      const b = rgba(cs.backgroundColor); if (b[3] > 0) count(bgs, hex(b));
      for (const p of ['marginTop', 'marginBottom', 'paddingTop', 'paddingBottom', 'paddingLeft', 'paddingRight', 'rowGap', 'columnGap']) {
        const v = parseFloat(cs[p]); if (v > 0) count(spacing, Math.round(v));
      }
      if (cs.borderRadius && cs.borderRadius !== '0px') count(radii, cs.borderRadius);
      if (cs.boxShadow && cs.boxShadow !== 'none') shadows++;
    }
    const spTotal = [...spacing.values()].reduce((a, b) => a + b, 0) || 1;
    const offGrid = [...spacing.entries()].filter(([v]) => v % 4 !== 0).reduce((a, [, c]) => a + c, 0);
    lh.sort((a, b) => a - b);
    out.inventory = {
      font_families: top(fam, 8), font_sizes: top(sizes, 20), distinct_font_sizes: sizes.size,
      font_weights: top(weights, 8), text_colors: top(colors, 12), distinct_text_colors: colors.size,
      background_colors: top(bgs, 12), distinct_background_colors: bgs.size,
      body_line_height_median: lh.length ? lh[Math.floor(lh.length / 2)] : null,
      lines_too_long_or_short: longLines.slice(0, 10),
      spacing_values_px: top(spacing, 20), spacing_off_4px_grid_pct: Math.round(100 * offGrid / spTotal),
      border_radii: top(radii, 10), elements_with_shadow: shadows,
      loaded_fonts: [...new Set([...document.fonts].filter(f => f.status === 'loaded').map(f => f.family.replace(/["']/g, '')))].slice(0, 12),
    };
  }
  return out;
}
"""

PRIME_SCROLL_JS = r"""
async () => {
  const wait = ms => new Promise(r => setTimeout(r, ms));
  const step = innerHeight * 0.8;
  for (let y = 0; y < Math.min(document.documentElement.scrollHeight, 20000); y += step) {
    scrollTo({top: y, behavior: 'instant'}); await wait(120);
  }
  scrollTo({top: 0, behavior: 'instant'}); await wait(400);
}
"""

HOVER_CANDIDATES_JS = r"""
() => {
  const vh = innerHeight; const c = [];
  for (const el of document.querySelectorAll('a[href],button,[role=button],input[type=submit]')) {
    const r = el.getBoundingClientRect(); const cs = getComputedStyle(el);
    if (r.width < 8 || r.height < 8 || cs.visibility === 'hidden' || cs.display === 'none') continue;
    if (r.top + scrollY > vh * 2 || r.width > innerWidth * 0.9) continue;
    const filled = cs.backgroundColor !== 'rgba(0, 0, 0, 0)' || parseFloat(cs.borderTopWidth) > 0;
    c.push({el, score: Math.min(r.width * r.height, 40000) + (filled ? 30000 : 0) + (el.tagName === 'BUTTON' ? 10000 : 0)});
  }
  c.sort((a, b) => b.score - a.score);
  return c.slice(0, 6).map((x, i) => {
    x.el.setAttribute('data-dc-hover', String(i));
    return {i, tag: x.el.tagName.toLowerCase(), text: (x.el.innerText || x.el.getAttribute('aria-label') || '').trim().slice(0, 40),
            cursor: getComputedStyle(x.el).cursor};
  });
}
"""

FOCUS_JS = r"""
() => {
  const el = document.activeElement;
  if (!el || el === document.body || el === document.documentElement) return null;
  const r = el.getBoundingClientRect(); const cs = getComputedStyle(el);
  const sel = el.tagName.toLowerCase() + (el.id ? '#' + el.id : '') + ([...el.classList].slice(0, 2).map(c => '.' + c).join(''));
  return {selector: sel, text: (el.innerText || el.getAttribute('aria-label') || el.value || '').trim().slice(0, 40),
          rect: [r.left, r.top, r.width, r.height], focus_visible: el.matches(':focus-visible'),
          outline: cs.outlineStyle !== 'none' && parseFloat(cs.outlineWidth) > 0 ? `${cs.outlineWidth} ${cs.outlineStyle} ${cs.outlineColor}` : null,
          box_shadow: cs.boxShadow !== 'none' ? cs.boxShadow.slice(0, 80) : null};
}
"""

ANIMATIONS_JS = r"""
() => {
  const layoutProps = /^(width|height|top|left|right|bottom|margin|padding|inset)/i;
  const res = [];
  for (const a of document.getAnimations()) {
    const t = a.effect && a.effect.target; if (!t) continue;
    const timing = a.effect.getComputedTiming();
    let props = [];
    try { props = [...new Set(a.effect.getKeyframes().flatMap(k => Object.keys(k)))].filter(k => !['offset', 'easing', 'composite', 'computedOffset'].includes(k)); } catch (e) {}
    if (a.transitionProperty) props = [a.transitionProperty];
    res.push({type: a.constructor.name, name: a.animationName || a.transitionProperty || a.id || '',
      target: t.tagName.toLowerCase() + (t.id ? '#' + t.id : '') + ([...t.classList].slice(0, 2).map(c => '.' + c).join('')),
      duration_ms: Math.round(timing.duration || 0), easing: timing.easing || (a.effect.getTiming && a.effect.getTiming().easing),
      iterations: timing.iterations === Infinity ? 'infinite' : timing.iterations, play_state: a.playState, properties: props.slice(0, 6),
      animates_layout: props.some(p => layoutProps.test(p))});
  }
  return res;
}
"""

LIBS_JS = r"""
() => ({gsap: !!window.gsap, scroll_trigger: !!window.ScrollTrigger, lenis: !!(window.lenis || window.Lenis || document.documentElement.classList.contains('lenis')),
        locomotive: !!document.querySelector('[data-scroll-container]'), aos: !!(window.AOS || document.querySelector('[data-aos]')),
        three: !!window.THREE || !!document.querySelector('canvas'), framer_motion_hint: !!document.querySelector('[data-framer-appear-id],[style*="will-change"]')})
"""

RUNNING_ANIMS_JS = "() => document.getAnimations().filter(a => a.playState === 'running').length"

# ----------------------------------------------------------------------- helpers


def png_size(path):
    with open(path, "rb") as f:
        head = f.read(24)
    return struct.unpack(">II", head[16:24])


def to_url(target):
    p = Path(target).expanduser()
    if p.exists():
        return p.resolve().as_uri()
    if not re.match(r"^[a-z]+://", target):
        return "https://" + target
    return target


def slug_for(url):
    u = urlparse(url)
    base = (u.netloc or Path(u.path).stem or "page").replace(":", "-")
    return re.sub(r"[^a-zA-Z0-9.-]+", "-", base).strip("-")


def open_page(page, url, args, log):
    try:
        page.goto(url, wait_until="load", timeout=45000)
    except Exception as e:  # noqa: BLE001
        log(f"  warning: the page did not finish loading ({e.__class__.__name__}); continuing with what is there")
    try:
        page.wait_for_load_state("networkidle", timeout=8000)
    except Exception:  # noqa: BLE001
        pass
    try:
        page.evaluate("document.fonts.ready.then(() => true)")
    except Exception:  # noqa: BLE001
        pass
    if args.hide:
        page.add_style_tag(content=f"{args.hide} {{ display: none !important; }}")
    page.wait_for_timeout(args.wait)


def clip_around(rect, vw, vh, pad=16):
    x, y, w, h = rect
    x0, y0 = max(0, x - pad), max(0, y - pad)
    x1, y1 = min(vw, x + w + pad), min(vh, y + h + pad)
    if x1 - x0 < 4 or y1 - y0 < 4:
        return None
    return {"x": x0, "y": y0, "width": x1 - x0, "height": y1 - y0}


def make_sheet(browser, items, out_path, title, cols=2, width=1400):
    """Combine several images into one sheet (so the model reads 1 image instead of 20)."""
    cells = "\n".join(
        f'<figure><img src="{Path(p).resolve().as_uri()}"><figcaption>{label}</figcaption></figure>'
        for label, p in items
    )
    html = f"""<!doctype html><meta charset="utf-8"><style>
      body{{margin:0;padding:20px;background:#e9e9ec;font:14px/1.3 system-ui,sans-serif;color:#222}}
      h1{{font-size:18px;margin:0 0 14px}}
      .g{{display:grid;grid-template-columns:repeat({cols},minmax(0,1fr));gap:14px;align-items:start}}
      figure{{margin:0;background:#fff;padding:8px;border-radius:6px}}
      img{{max-width:100%;display:block;margin:0 auto;outline:1px solid #ccc;background:repeating-conic-gradient(#ddd 0 25%,#fff 0 50%) 0 0/12px 12px}}
      figcaption{{margin-top:6px;font-weight:600}}</style>
      <h1>{title}</h1><div class="g">{cells}</div>"""
    html_path = Path(out_path).with_suffix(".html")
    html_path.write_text(html, encoding="utf-8")
    pg = browser.new_page(viewport={"width": width, "height": 800})
    pg.goto(html_path.resolve().as_uri())
    pg.wait_for_load_state("load")
    pg.screenshot(path=str(out_path), full_page=True)
    pg.close()
    html_path.unlink()


# ------------------------------------------------------------------------ steps


def capture_static(browser, url, out, args, result, log):
    shots = out / "shots"
    shots.mkdir(parents=True, exist_ok=True)
    for vp_name in args.viewports:
        vp = VIEWPORTS[vp_name]
        for scheme in args.schemes:
            key = f"{vp_name}-{scheme}"
            log(f"· {key}")
            ctx = browser.new_context(
                viewport={"width": vp["width"], "height": vp["height"]}, device_scale_factor=1,
                is_mobile=vp["is_mobile"], has_touch=vp["has_touch"], color_scheme=scheme,
                ignore_https_errors=True,
            )
            page = ctx.new_page()
            console, failed = [], []
            page.on("console", lambda m: m.type == "error" and console.append(m.text[:200]))
            page.on("pageerror", lambda e: console.append(str(e)[:200]))
            page.on("response", lambda r: r.status >= 400 and failed.append(f"{r.status} {r.url[:120]}"))
            page.on("requestfailed", lambda r: failed.append(f"FAILED {r.url[:120]}"))
            try:
                open_page(page, url, args, log)
                page.evaluate(PRIME_SCROLL_JS)
                entry = {"viewport": vp, "scheme": scheme}
                height = page.evaluate("document.documentElement.scrollHeight")
                fold = shots / f"{key}-fold.png"
                page.screenshot(path=str(fold), animations="disabled")
                full = shots / f"{key}-full.png"
                if height > MAX_FULL_HEIGHT:
                    page.screenshot(path=str(full), full_page=True, animations="disabled",
                                    clip={"x": 0, "y": 0, "width": vp["width"], "height": MAX_FULL_HEIGHT})
                    entry["full_truncated_at"] = MAX_FULL_HEIGHT
                else:
                    page.screenshot(path=str(full), full_page=True, animations="disabled")
                entry["fold"] = str(fold.relative_to(out))
                entry["full"] = str(full.relative_to(out))
                if scheme == "light" or len(args.schemes) == 1:
                    slices = []
                    n = min(MAX_SLICES, -(-min(height, MAX_FULL_HEIGHT) // vp["height"]))
                    for i in range(n):
                        y = i * vp["height"]
                        h = min(vp["height"], height - y)
                        if h < 40:
                            break
                        sp = shots / f"{key}-slice-{i + 1}.png"
                        page.screenshot(path=str(sp), full_page=True, animations="disabled",
                                        clip={"x": 0, "y": y, "width": vp["width"], "height": h})
                        slices.append(str(sp.relative_to(out)))
                    entry["slices"] = slices
                    if height > MAX_SLICES * vp["height"]:
                        entry["slices_note"] = f"the page is {height}px tall; slices only cover the first {MAX_SLICES * vp['height']}px (see the full screenshot for the rest)"
                page.evaluate("scrollTo({top: 0, behavior: 'instant'})")
                inventory = scheme == "light" and vp_name in ("desktop", "mobile")
                entry["metrics"] = page.evaluate(METRICS_JS, {"inventory": inventory})
                rw = entry["metrics"]["meta"]["rendered_width"]
                # If the page shrinks (mobile with overflowing content or no meta viewport), screenshots
                # show it scaled down: metric boxes (CSS px) × shot_scale = image px.
                entry["shot_scale"] = round(vp["width"] / rw, 4) if rw > vp["width"] else 1
                entry["console_errors"] = sorted(set(console))[:15]
                entry["failed_requests"] = sorted(set(failed))[:15]
                result["captures"][key] = entry
            except Exception as e:  # noqa: BLE001
                result["errors"].append(f"{key}: {e}")
                log(f"  error in {key}: {e}")
            finally:
                ctx.close()

    light, dark = result["captures"].get("desktop-light"), result["captures"].get("desktop-dark")
    if light and dark:
        same = (out / light["fold"]).read_bytes() == (out / dark["fold"]).read_bytes()
        result["dark_mode"] = {
            "supported": not same,
            "note": "Light/dark screenshots are identical: the site does not respond to prefers-color-scheme." if same
            else f"Light background {light['metrics']['meta']['page_bg']} / dark {dark['metrics']['meta']['page_bg']}.",
        }


def capture_states(browser, url, out, args, result, log):
    log("· states (hover / focus)")
    st = out / "states"
    st.mkdir(parents=True, exist_ok=True)
    vp = VIEWPORTS["desktop"]
    # At 2x so small crops (buttons, links) stay readable.
    ctx = browser.new_context(viewport={"width": vp["width"], "height": vp["height"]}, color_scheme="light",
                              device_scale_factor=2, ignore_https_errors=True)
    page = ctx.new_page()
    sheet_items, hover_res, focus_res = [], [], []
    try:
        open_page(page, url, args, log)
        # Infinite animations (pulse, shimmer...) mean the element is never "still" and rest/hover
        # differ even if hover does nothing: pause them. Transitions keep working.
        page.evaluate("""() => document.getAnimations().forEach(a => {
            if (a.effect && a.effect.getComputedTiming().iterations === Infinity) { a.pause(); a.currentTime = 0; } })""")
        for c in page.evaluate(HOVER_CANDIDATES_JS):
            loc = page.locator(f'[data-dc-hover="{c["i"]}"]').first
            try:
                loc.evaluate("el => el.scrollIntoView({block: 'center', behavior: 'instant'})")
                page.mouse.move(vp["width"] - 2, vp["height"] - 2)
                page.wait_for_timeout(300)
                b = loc.bounding_box()
                clip = b and clip_around([b["x"], b["y"], b["width"], b["height"]], vp["width"], vp["height"])
                if not clip:
                    continue
                rest = st / f"hover-{c['i']}-rest.png"
                hov = st / f"hover-{c['i']}-hover.png"
                page.screenshot(path=str(rest), clip=clip)
                loc.hover(timeout=3000, force=True)
                page.wait_for_timeout(450)
                page.screenshot(path=str(hov), clip=clip)
                changed = rest.read_bytes() != hov.read_bytes()
                hover_res.append({**c, "rest": str(rest.relative_to(out)), "hover": str(hov.relative_to(out)),
                                  "visual_change_on_hover": changed})
                sheet_items += [(f"#{c['i']} “{c['text'][:25]}” rest", rest),
                                (f"#{c['i']} hover {'(changes)' if changed else '(NO CHANGE)'} · cursor {c['cursor']}", hov)]
            except Exception as e:  # noqa: BLE001
                result["errors"].append(f"hover {c['i']}: {e}")

        page.mouse.move(vp["width"] - 2, vp["height"] - 2)
        page.evaluate("scrollTo({top: 0, behavior: 'instant'})")
        page.wait_for_timeout(200)
        seen = set()
        for i in range(args.tabs):
            page.keyboard.press("Tab")
            page.wait_for_timeout(250)
            info = page.evaluate(FOCUS_JS)
            if not info:
                continue
            sig = (info["selector"], tuple(round(v) for v in info["rect"]))
            if sig in seen:
                break
            seen.add(sig)
            clip = clip_around(info["rect"], vp["width"], vp["height"], pad=14)
            entry = {"order": i + 1, **{k: v for k, v in info.items() if k != "rect"}}
            if clip:
                fp = st / f"focus-{i + 1}.png"
                page.screenshot(path=str(fp), clip=clip)
                entry["shot"] = str(fp.relative_to(out))
                sheet_items.append((f"Tab {i + 1}: {info['selector'][:30]} “{info['text'][:20]}”", fp))
            focus_res.append(entry)

        if sheet_items:
            sheet = st / "states-sheet.png"
            make_sheet(browser, sheet_items, sheet, "Interactive states (desktop, light): hover and keyboard focus", cols=2)
            result["states_sheet"] = str(sheet.relative_to(out))
    except Exception as e:  # noqa: BLE001
        result["errors"].append(f"states: {e}")
    finally:
        ctx.close()
    first = focus_res[0]["text"].lower() if focus_res else ""
    result["states"] = {
        "hover": hover_res,
        "focus_order": focus_res,
        "skip_link_first": any(w in first for w in ("skip", "saltar", "ir al contenido", "aller au contenu", "zum inhalt", "vai al contenuto", "pular")),
    }


def capture_motion(browser, url, out, args, result, log):
    log("· motion (animations, scroll, reduced-motion)")
    mo = out / "motion"
    mo.mkdir(parents=True, exist_ok=True)
    vp = VIEWPORTS["desktop"]
    motion = {}
    ctx = browser.new_context(viewport={"width": vp["width"], "height": vp["height"]}, color_scheme="light",
                              reduced_motion="no-preference", ignore_https_errors=True,
                              record_video_dir=str(mo / "_video"), record_video_size={"width": 960, "height": 600})
    page = ctx.new_page()
    frames = []
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        t_prev = 0
        for t in (0, 400, 1200, 2500):
            page.wait_for_timeout(t - t_prev)
            t_prev = t
            fp = mo / f"load-{t}ms.png"
            page.screenshot(path=str(fp))
            frames.append((f"Load +{t} ms", fp))
        motion["animations_on_load"] = page.evaluate(ANIMATIONS_JS)[:30]
        motion["libraries"] = page.evaluate(LIBS_JS)
        running_np = [page.evaluate(RUNNING_ANIMS_JS)]
        height = page.evaluate("document.documentElement.scrollHeight")
        scroll_anims = []
        for k in (1, 2, 3):
            y = k * vp["height"]
            if y >= height - 100:
                break
            page.evaluate(f"scrollTo({{top: {y}, behavior: 'instant'}})")
            page.wait_for_timeout(60)
            a = mo / f"scroll-{k}-entering.png"
            page.screenshot(path=str(a))
            scroll_anims += page.evaluate(ANIMATIONS_JS)
            page.wait_for_timeout(900)
            b = mo / f"scroll-{k}-settled.png"
            page.screenshot(path=str(b))
            frames += [(f"Scroll {k} screen(s): +60 ms", a), (f"Scroll {k}: +960 ms", b)]
            if k == 1:
                running_np.append(page.evaluate(RUNNING_ANIMS_JS))
        dedup = {}
        for an in scroll_anims:
            dedup.setdefault((an["target"], an["name"]), an)
        motion["animations_on_scroll"] = list(dedup.values())[:30]
        # Smooth scroll-through for the video
        page.evaluate("""async () => { const H = Math.min(document.documentElement.scrollHeight, 15000);
            const t0 = performance.now(); const dur = Math.min(12000, H * 1.2);
            await new Promise(res => { const f = () => { const p = Math.min(1, (performance.now() - t0) / dur);
              scrollTo({top: p * H, behavior: 'instant'}); p < 1 ? requestAnimationFrame(f) : res(); }; f(); }); }""")
        page.wait_for_timeout(500)
    except Exception as e:  # noqa: BLE001
        result["errors"].append(f"motion: {e}")
        running_np = locals().get("running_np", [0])
    finally:
        video = page.video
        ctx.close()
        try:
            if video:
                dst = mo / "scroll.webm"
                shutil.move(video.path(), dst)
                motion["video"] = str(dst.relative_to(out))
        except Exception:  # noqa: BLE001
            pass
        shutil.rmtree(mo / "_video", ignore_errors=True)

    # Does it respect prefers-reduced-motion?
    try:
        ctx = browser.new_context(viewport={"width": vp["width"], "height": vp["height"]}, reduced_motion="reduce",
                                  ignore_https_errors=True)
        page = ctx.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(400)
        running_r = [page.evaluate(RUNNING_ANIMS_JS)]
        page.evaluate(f"scrollTo({{top: {vp['height']}, behavior: 'instant'}})")
        page.wait_for_timeout(960)
        running_r.append(page.evaluate(RUNNING_ANIMS_JS))
        ctx.close()
        total_np, total_r = sum(running_np), sum(running_r)
        motion["reduced_motion"] = {
            "running_no_preference": running_np, "running_reduce": running_r,
            "respected": total_np == 0 or total_r < max(1, total_np * 0.5),
            "note": "No animations detected through the Web Animations API (there may be JS/canvas animation: check the frames)."
            if total_np == 0 else None,
        }
    except Exception as e:  # noqa: BLE001
        result["errors"].append(f"reduced-motion: {e}")

    if frames:
        sheet = mo / "motion-sheet.png"
        make_sheet(browser, frames, sheet, "Motion (desktop): entrance animation and scroll reveals", cols=2)
        motion["sheet"] = str(sheet.relative_to(out))
    result["motion"] = motion


def compute_flags(result):
    """High-level, already interpreted alerts, so nobody has to dig through the JSON."""
    flags = []
    caps = result["captures"]
    any_cap = next(iter(caps.values()), None)
    if any_cap:
        meta = any_cap["metrics"]["meta"]
        if not meta.get("viewport_meta"):
            flags.append("CRITICAL: missing <meta name=viewport>. On mobile the page renders at ~980px and shrinks: "
                         "unreadable text and tiny tap targets. Mobile overflow metrics are unreliable until this is fixed.")
        for key, e in caps.items():
            if key.endswith("dark") and key.replace("dark", "light") in caps:
                continue
            mm = e["metrics"]["meta"]
            if mm["rendered_width"] > e["viewport"]["width"] + 1 and meta.get("viewport_meta"):
                flags.append(f"{key}: the page shrinks to fit ({mm['rendered_width']}px of content on a "
                             f"{e['viewport']['width']}px screen). Everything in the screenshots is scaled ×{e['shot_scale']}.")
        if not meta.get("lang"):
            flags.append("Missing lang attribute on <html> (screen readers, translation, hyphenation).")
    # One entry per viewport (the light one if present) to avoid duplicate alerts.
    per_vp = {}
    for key, e in caps.items():
        vpn = key.split("-")[0]
        if vpn not in per_vp or key.endswith("light"):
            per_vp[vpn] = (key, e)
    for key, e in per_vp.values():
        m = e["metrics"]
        if m.get("horizontal_scroll"):
            flags.append(f"{key}: horizontal scroll ({m['horizontal_scroll']['scroll_width']}px wide in a {m['horizontal_scroll']['viewport']}px viewport).")
        if m["contrast"].get("gradient_text") and key == "desktop-light":
            flags.append(f"Gradient text (background-clip:text) on {len(m['contrast']['gradient_text'])} element(s): contrast not measurable; check it by eye.")
        if m["images"]["broken"] and key == "desktop-light":
            flags.append(f"Broken images: {len(m['images']['broken'])}.")
        if m["headings"]["h1_count"] != 1 and key == "desktop-light":
            flags.append(f"The page has {m['headings']['h1_count']} <h1> (normally 1).")
    if result.get("dark_mode") and not result["dark_mode"]["supported"]:
        flags.append("No dark mode (does not respond to prefers-color-scheme). Not an error by itself; do not critique dark mode.")
    s = result.get("states") or {}
    nochg = [h for h in s.get("hover", []) if not h["visual_change_on_hover"]]
    if nochg:
        flags.append(f"{len(nochg)} interactive element(s) with no visual change on hover: " + ", ".join(f"“{h['text']}”" for h in nochg[:5]))
    nofocus = [f for f in s.get("focus_order", []) if not (f["outline"] or f["box_shadow"])]
    if nofocus:
        flags.append(f"{len(nofocus)} element(s) with a possibly invisible keyboard focus (no outline or box-shadow): "
                     + ", ".join(f"“{f['text'] or f['selector']}”" for f in nofocus[:5]) + ". Confirm it on the states sheet.")
    mo = result.get("motion") or {}
    if mo.get("reduced_motion") and not mo["reduced_motion"]["respected"]:
        flags.append("Does not respect prefers-reduced-motion: animations keep running with 'reduce'.")
    anims = list({(a["target"], a["name"]): a for a in mo.get("animations_on_load", []) + mo.get("animations_on_scroll", [])}.values())
    for a in anims:
        if a.get("animates_layout"):
            flags.append(f"Animation of layout properties ({', '.join(a['properties'])}) on {a['target']}: causes reflow; use transform/opacity.")
    inf = [a for a in anims if a.get("iterations") == "infinite"]
    if len(inf) >= 2:
        flags.append(f"{len(inf)} infinite animations at once ({', '.join(a['target'] for a in inf[:4])}): visual noise.")
    result["flags"] = flags


def summarize(result):
    lines = [f"URL: {result['url']}", f"Output: {result['out']}", ""]
    if result.get("flags"):
        lines += ["ALERTS:"] + [f"  ! {f}" for f in result["flags"]] + [""]
    for key, e in result["captures"].items():
        m = e.get("metrics", {})
        c = m.get("contrast", {})
        t = m.get("tap_targets", {})
        bits = [f"contrast: {len(c.get('failures', []))} failures"]
        if m.get("horizontal_scroll"):
            bits.append(f"HORIZONTAL SCROLL ({m['horizontal_scroll']['scroll_width']}px)")
        if m.get("overflowing_elements"):
            bits.append(f"{len(m['overflowing_elements'])} overflowing elements")
        cut = [x for x in m.get("truncated_text", []) if not x["intentional_ellipsis"]]
        if cut:
            bits.append(f"{len(cut)} truncated texts")
        if key.startswith(("mobile", "tablet")) and t:
            bits.append(f"tap targets <24px: {t.get('under_24', 0)}, <44px: {t.get('under_44', 0)}")
        lines.append(f"  {key:15} " + " · ".join(bits))
    if "dark_mode" in result:
        lines.append(f"  dark mode: {'yes' if result['dark_mode']['supported'] else 'NO'}")
    s = result.get("states")
    if s:
        nochg = [h["text"] for h in s["hover"] if not h["visual_change_on_hover"]]
        nofocus = [f["text"] or f["selector"] for f in s["focus_order"] if not (f["outline"] or f["box_shadow"])]
        lines.append(f"  hover without visual change: {len(nochg)}/{len(s['hover'])}  ·  focus possibly invisible: {len(nofocus)}/{len(s['focus_order'])}")
    mo = result.get("motion", {})
    if mo.get("reduced_motion"):
        rm = mo["reduced_motion"]
        state = "no animations detected" if not sum(rm["running_no_preference"]) else ("respected" if rm["respected"] else "NOT respected")
        lines.append(f"  prefers-reduced-motion: {state} (running animations {rm['running_no_preference']} vs {rm['running_reduce']})")
    layout_anims = [a for a in mo.get("animations_on_load", []) + mo.get("animations_on_scroll", []) if a.get("animates_layout")]
    if layout_anims:
        lines.append(f"  layout-property animations: {len(layout_anims)}")
    if result["errors"]:
        lines.append(f"  capture errors: {len(result['errors'])} (see capture.json)")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("target", help="URL or path to a local .html file")
    ap.add_argument("--out", help="output directory (default .design-critic/<site>-<date>)")
    ap.add_argument("--viewports", default="mobile,tablet,desktop")
    ap.add_argument("--schemes", default="light,dark")
    ap.add_argument("--wait", type=int, default=800, help="extra ms to wait after load")
    ap.add_argument("--hide", help="CSS selectors to hide (e.g. cookie banners): '#cookies, .chat-widget'")
    ap.add_argument("--tabs", type=int, default=10, help="Tab presses used to check focus")
    ap.add_argument("--no-states", action="store_true")
    ap.add_argument("--no-motion", action="store_true")
    args = ap.parse_args()
    args.viewports = [v.strip() for v in args.viewports.split(",") if v.strip() in VIEWPORTS]
    args.schemes = [s.strip() for s in args.schemes.split(",") if s.strip() in ("light", "dark")]

    url = to_url(args.target)
    out = Path(args.out or f".design-critic/{slug_for(url)}-{datetime.now():%Y%m%d-%H%M%S}")
    out.mkdir(parents=True, exist_ok=True)
    result = {"url": url, "out": str(out.resolve()), "date": datetime.now().isoformat(timespec="seconds"),
              "captures": {}, "errors": []}
    log = lambda s: print(s, file=sys.stderr, flush=True)  # noqa: E731

    with sync_playwright() as p:
        browser = p.chromium.launch()
        capture_static(browser, url, out, args, result, log)
        if not args.no_states and "desktop" in VIEWPORTS:
            capture_states(browser, url, out, args, result, log)
        if not args.no_motion:
            capture_motion(browser, url, out, args, result, log)
        browser.close()

    for e in result["captures"].values():
        for k in ("fold", "full"):
            if k in e:
                e[k + "_size"] = list(png_size(out / e[k]))
    compute_flags(result)
    (out / "capture.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(summarize(result))
    print(f"\nFull details: {out / 'capture.json'}")


if __name__ == "__main__":
    main()
