---
name: design-critic
description: "Use this skill when the user wants to know how an existing website really looks: their opinion, a critique, honest feedback, a visual review or audit, or “what's wrong”. Works for a whole page or just one part (hero, pricing, animations, mobile or tablet responsiveness, contrast, buttons, hierarchy, whether it looks professional or AI-made), on a URL, localhost, a project with a dev server, or an .astro/.html file. Also use it before launching a site or showing it to a client. Prefer it over other design skills whenever something already built has to be judged: it opens the site in a real browser, takes mobile, tablet and desktop screenshots, measures contrast, overflow, tap targets and motion, and delivers a prioritized report with a fix for each problem; if the user wants, it applies the fixes and verifies the before and after. Works in any language. Not for building new sites, reviewing code or PRs, debugging logic, performance tuning or Figma-to-code."
---

# design-critic

Claude writes a lot of CSS without ever seeing the result. This skill closes that loop: **capture → look → critique with evidence → propose → fix → look again**.

Its value comes from two things a code review can't give:
1. **Visual evidence.** Every problem points at a specific area of a real screenshot.
2. **Measured numbers.** Contrast, overflow and tap-target size are measured, not guessed.

A generic critique ("improve the visual hierarchy") is useless. A useful one says which element, in which viewport, why it bothers the user and which exact value to change.

Full rubric: `references/criteria.md`. Read it before critiquing; it has concrete thresholds and the catalog of AI-made design tells.

## Language

**Write everything the user will read in the user's language**: the findings, the area names, the verdict and the chat message. If they write in French, the critique is in French. Put the ISO 639-1 code in `findings.json` → `"lang": "fr"`; the report's interface (headings, buttons, filters) is translated automatically from `scripts/i18n.py` (falls back to English for languages it doesn't include). The scripts' console output and the automatic alerts are in English: read them, then explain them in the user's language.

## 1. Decide what to capture

- **Public URL or localhost:** use it as is.
- **Local project without a URL:** check whether a dev server already responds on the usual ports (5173, 3000, 4321, 8080…). If not, look at `package.json` and start it in the background (`npm run dev`). Wait until it responds and remember to stop it at the end.
- **Standalone `.html` file:** pass the path; the script turns it into `file://`.
- **Cookie banners, chats or popups covering the page:** hide them with `--hide '#cookie-banner, .intercom-launcher'`.

If the user only wants one part reviewed (e.g. "the hero on mobile"), narrow the capture with `--viewports mobile --no-motion` and focus the critique on that part. Don't run a full audit nobody asked for.

## 2. Capture

```bash
python3 <skill>/scripts/capture.py <url-or-path> --out .design-critic/<name>
```

Takes about 30-60 s. It produces:

| Path | What it is |
|---|---|
| `shots/{mobile,tablet,desktop}-{light,dark}-fold.png` | Above the fold |
| `shots/*-light-slice-N.png` | The page cut into screens (for close inspection) |
| `shots/*-full.png` | Full page (for the report; too scaled down to analyze) |
| `states/states-sheet.png` | Hover (rest/hover) and Tab focus, all on one sheet |
| `motion/motion-sheet.png` | Frames of the load (0/400/1200/2500 ms) and of scrolling (+60/+960 ms) |
| `motion/scroll.webm` | Video of the full scroll (for the user) |
| `capture.json` | Metrics: contrast, overflow, tap targets, typography, spacing, animations, `flags`… |

The script prints a summary with **ALERTS**, which are already-interpreted problems. Start there.

If the project uses git, suggest adding `.design-critic/` to `.gitignore`.

## 3. Look, for real

Read the images with the Read tool in this order. First the overall impression, then the details:

1. `desktop-light-fold.png` and `mobile-light-fold.png`. **5-second test:** what do you see first? Is it clear what this is and what to do? Write down your first impression before looking at metrics; it's the closest thing to what a visitor feels.
2. The desktop and mobile slices, top to bottom: rhythm between sections, alignment, consistency.
3. `tablet-light-fold.png`: the in-between size is where layouts break most.
4. The `-dark` folds, only if `dark_mode.supported` is true. If there's no dark mode, don't critique it unless the user asks.
5. `states/states-sheet.png` and `motion/motion-sheet.png`.

Then check what you saw against `capture.json`. **Automatic metrics can be wrong.** A contrast failure on disabled decorative text, or an "overflowing" element that is an intentionally scrollable carousel, are not problems. Anything you haven't confirmed visually, present as "to check", not as fact.

## 4. Critique

Go through the categories in `references/criteria.md`. For each problem:

- **Specific:** which element, in which viewport and color scheme.
- **With evidence:** the screenshot and the box that points at it (see "Box coordinates" below).
- **With the why:** the effect on the person using the site, not abstract aesthetics.
- **With an exact fix:** values, not adjectives. "`h2` from 19px to 28px/600, `margin-top` from 24px to 64px", not "more hierarchy".

### Write for whoever will read it

The report may be read by someone who doesn't know CSS: whoever commissioned it, a client, or the user in a hurry. So every finding is written in two layers:

- **Plain layer** (`title`, `tldr`, `problem`, `why`, `fix`): describes what the visitor *sees or notices*, with no jargon. No selectors, CSS properties, hex codes, "WCAG" or "viewport". Write "the light grey text is barely readable on the white", not "#b0b0b0 on #fff gives 2.17:1". A number is fine if it makes sense without context ("the buttons are 16 px, half of what's comfortable for a finger").
- **Technical layer** (`code`, `where`): everything precise goes here: selector, values, contrast ratio, file and line. The report shows it collapsed under "Technical details".

Title and summary example:
- ❌ `title`: "No meta viewport and .wide with width:1100px" · `tldr`: "The layout isn't responsive."
- ✅ `title`: "On mobile everything looks tiny" · `tldr`: "The page doesn't adapt to phones: you have to zoom in to read anything."

The `title` must make sense on its own, without opening the details. The `tldr` is one sentence of 20 words at most.

**Quality over quantity.** 6 to 12 well-chosen findings beat 40 minor details. Group repeats ("the #b0b0b0 grey on white fails on all 3 cards and in the footer" is one finding). Also include what works well: it tells the user what to keep while fixing the rest.

**Calibrate to the kind of site.** An experimental portfolio can break rules a SaaS tool shouldn't. If an odd decision looks intentional and coherent, treat it as style, not as a mistake.

Severities:
- **critical:** prevents using the site or reading the content (no meta viewport, horizontal scroll on mobile, invisible CTA, contrast < 3:1 on main text, invisible focus in the navigation).
- **high:** clearly hurts the experience or credibility (confusing hierarchy, tap targets < 24px, broken layout on tablet, generic AI look in the hero).
- **medium:** noticeable and lowers quality (inconsistent spacing, chaotic type scale, no hover states).
- **low:** polish (optical tweaks, widows, easing).

### If there is source code

Find the culprit for each finding: search for the class, selector or text shown in `capture.json`. Put `file:line` in `where` and write the fix in the project's own idiom: Tailwind classes if it uses Tailwind, its tokens or CSS variables if it has them, its animation library. A fix that ignores the project's system creates debt.

### Box coordinates

`box: [x, y, width, height]` in **pixels of the image** you cite.

- `shots/` screenshots are 1x, so the boxes in `capture.json` (page coordinates in CSS px) work as is for `*-full.png` and `*-fold.png` (if above the fold).
- For `slice-N`, subtract `(N-1) × viewport_height` from `y` (mobile 844, tablet 1180, desktop 900).
- If the capture has `shot_scale` < 1 (the page overflows and mobile shrinks it to fit), multiply metric boxes by `shot_scale` for that viewport.
- If you spotted the problem yourself and it's not in the metrics, estimate the box by looking at the image. Dimensions are in `fold_size`/`full_size` in `capture.json`. Approximate is fine.
- The `states/` and `motion/` sheets can be cited without a `box`.

## 5. Report

Write `findings.json` in the capture directory (full schema in the docstring of `scripts/build_report.py`). Besides the findings, fill in:

- `lang`: the user's language (see "Language").
- `score`: honest overall score from 0 to 10. A 5 means "works, but looks careless".
- `areas`: 5 to 7 areas with **names anyone understands**, scored 0 to 10. The report shows them as traffic lights. For example: "Easy to understand at a glance", "Easy to read", "Works on mobile", "Usable with a keyboard", "Animations help", "Has its own personality", "Order and consistency". Use only the ones that apply, in the user's language.
- `start_here`: the 3 findings with the most impact per effort. They don't have to be the 3 most severe: a 5-minute fix that helps a lot goes first.
- In each finding, `devices` (`mobile`, `tablet`, `desktop`) and `effort` (`5 min`, `30 min`, `2 h`).

Build the report and open it:

```bash
python3 <skill>/scripts/build_report.py .design-critic/<name>
xdg-open .design-critic/<name>/report.html   # macOS: open
```

If the script prints warnings (`warning: …`), fix `findings.json` and rebuild.

The report reads in layers: at the top the score, the verdict and "Start here"; then the collapsed problems, with filters by severity and device; screenshots zoom with a click; and the technical data comes last, also collapsed.

**Chat message:** always use this format, translated into the user's language. It's short on purpose, because the detail is in the report:

```
**Score: 3/10.** <one-sentence verdict, plain>

**Start here:**
1. <plain title> · <device> · <effort>
2. …
3. …

<One line about what already works and is worth keeping.>

📄 Full report (with screenshots and code for each fix): `<path>/report.html`

What should I do now?
- **a)** Fix these 3
- **b)** Fix all critical and high ones (N)
- **c)** You choose which
- **d)** Nothing, I only wanted the critique
```

If the site **isn't the user's** (a third-party site they only want analyzed), don't offer to fix anything. Close with other options: "a) I'll prepare a mockup of how it would look (HTML with the changes)", "b) I'll go deeper into one point", "c) Nothing else, thanks".

## 6. Propose, fix and verify

The chat message already ends with options a/b/c/d. Wait for the answer. **Don't touch code without the user's confirmation:** they may want the critique for someone else or disagree with a point.

After applying the fixes (`<dir>` is the directory of the first capture):

1. **Recapture** into `<dir>-after`, only what was affected:
   ```bash
   python3 <skill>/scripts/capture.py <url-or-path> --out <dir>-after --viewports mobile,desktop --schemes light --no-motion
   ```
   Drop `--no-motion` if a fix touched animations, and `--schemes light` if it touched dark mode.
2. **Compare:**
   ```bash
   python3 <skill>/scripts/compare.py <dir> <dir>-after
   ```
   It prints each metric as ✓ better / = same / ✗ worse plus resolved and new alerts, and writes `<dir>-after/comparison.json` and `diff/*.png` (what changed above the fold, in red).
3. **Review regressions.** If `compare.py` exits with code 1, something got worse or there's a new alert (also when a number drops but a different element fails: look at `new`). Fix it, repeat steps 1-2 and don't move on until it exits with 0. If a regression can't be avoided, explain it to the user; don't hide it.
4. **Look at the new screenshots** of the fixed problems (and at the `diff` if in doubt). Metrics don't see everything: a fix isn't verified until you've seen it.
5. **Update `findings.json`** for each fixed problem: `"status": "fixed"` and `after` with the new screenshot. `after` paths start with `after/` because the report copies `<dir>-after` into `<dir>/after/`:
   ```json
   "status": "fixed",
   "after": {"shot": "after/shots/mobile-light-full.png", "box": [0, 1000, 390, 1400], "label": "Mobile"}
   ```
   If the before and after screenshots are the same size, the report shows a comparison slider; otherwise, side by side. To get the slider, use in `after` the same file as in `evidence` (e.g. `*-fold.png` or `states/focus-N.png`); full pages change height as soon as anything moves.
6. **Rebuild the report:**
   ```bash
   python3 <skill>/scripts/build_report.py <dir> --after <dir>-after
   ```
   It adds the "Before and after" section, with the metrics in plain words, resolved and new alerts, and the diffs.
7. **In the chat**, one line per fixed problem (✓ plain title) and, if any, the regressions that remain, with the link to the report.

## If something goes wrong

- **The page doesn't load or is blank:** check that the URL responds. If it's a slow SPA, try `--wait 3000`. If it requires login, tell the user: the script doesn't handle sessions.
- **Errors in `capture.json` → `errors`:** usually states or motion failing on unusual sites. The rest of the capture is still usable.
- **No Playwright:** the scripts need the Playwright Python package with Chromium. Don't install anything yourself: tell the user it's missing and point them to the "Install" section of the README.
