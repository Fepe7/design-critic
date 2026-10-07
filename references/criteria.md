# Critique criteria

Concrete thresholds for each category. They aren't laws. Breaking a rule with intent and coherence is design; breaking it by accident is a mistake. The underlying question is always the same: **what happens to the person using this?**

## Contents
1. First impression and hierarchy
2. Typography
3. Color and contrast
4. Spacing, alignment and rhythm
5. Layout and responsiveness
6. Interactive states
7. Motion
8. Basic accessibility
9. Tells of AI-made design

---

## 1. First impression and hierarchy

- **5-second test.** Looking only at the first screen: what is it, who is it for and what do I do now? If you have to scroll to understand the product, it's a high-severity problem.
- **One focal point per screen.** If the headline, the image and three buttons compete, nobody wins. Try squinting: whatever still stands out should be what matters.
- **Unmistakable primary CTA.** It should be the interactive element with the most visual weight. If the secondary CTA looks like it, the primary one loses strength.
- **Scale contrast.** Heading levels need a clear jump, ×1.25 or more (×1.5 or more reads clearly). A 40px `h1` next to a 36px `h2` doesn't create hierarchy.
- **Reading order.** The natural path (Z on landing pages, F on content) should match the order of importance.

## 2. Typography

- **Families:** 1 or 2 (plus a monospace if there's code). 3 or more is usually carelessness. See `inventory.font_families`.
- **Scale:** many different sizes (`distinct_font_sizes` above 10-12) means there's no scale. Healthy is 5-8 steps with a consistent ratio.
- **Body text:** 16-18px on desktop and never below 16px on mobile for running text. Line height 1.4-1.7 (`body_line_height_median`). Large headlines work better at 1.0-1.2.
- **Line length:** 45-75 characters. Above 85 it's tiring and below 30 it breaks the reading flow (`lines_too_long_or_short`). Fix: `max-width: 65ch`.
- **Weights:** hierarchy is also built with weight. If everything is 400 or everything is 700, there's no contrast.
- **Details:** widows in headlines (`text-wrap: balance`), uppercase without `letter-spacing` (raise it 0.04-0.08em), straight quotes ("") instead of typographic ones (“ ” or « »), misaligned numbers in tables (`font-variant-numeric: tabular-nums`).
- **Font not loading:** if `failed_requests` includes fonts, the fallback is being shown.

## 3. Color and contrast

- **WCAG AA:** normal text ≥ 4.5:1, large text (≥ 24px, or ≥ 18.66px bold) ≥ 3:1, and UI components and focus rings ≥ 3:1. The figures in `capture.json → contrast.failures` are measured: quote them.
- Light grey text (#999-#bbb) on white is the most common failure on modern sites. What gets lost is exactly the text that explains things.
- **Palette:** if `distinct_text_colors` goes above ~8 or there are many nearly identical backgrounds (#f5f5f5, #f6f6f6, #f7f7f8…), tokens are missing.
- **Accent:** an accent color loses strength if it's used everywhere. Keep it for actions and states.
- **Greys:** greys with a hint of the brand hue look more refined than pure neutrals, though this is a preference, not a mistake.
- **Dark mode (if present):** inverting isn't enough. A slightly tinted or very dark grey background (not pure #000 with pure #fff text, which vibrates), slightly muted saturated colors, depth through lighter surfaces instead of shadows, and images and logos that stay visible.

## 4. Spacing, alignment and rhythm

- **Spacing scale:** values in multiples of 4 or 8. If `spacing_off_4px_grid_pct` is above 20% or there are many unique values, spacing was done by eye.
- **Proximity:** related things together, different things apart. The space between a heading and its paragraph should be clearly smaller than between sections. If they're similar, content seems to float.
- **Vertical rhythm:** sections with consistent gaps. A cramped section between two roomy ones looks like a mistake.
- **Alignment:** everything should rest on a few axes. Look for edges that almost line up but don't (2-8px off): that's what "feels" wrong without anyone knowing why.
- **Inner padding:** sibling cards should have the same padding, and text shouldn't touch the edges.
- **Optical alignment:** icons next to text, buttons with icons and text centered on irregular shapes need adjusting by eye.

## 5. Layout and responsiveness

- **No `<meta name=viewport>`** is critical: on mobile everything looks tiny.
- **Horizontal scroll or clipped elements** (`horizontal_scroll`, `overflowing_elements`) are critical on mobile. Typical causes: fixed px widths, `100vw` with a scrollbar, images without `max-width: 100%`, and grids with `minmax` values that are too large.
- **Truncated text** (`truncated_text` without intentional ellipsis) means lost content.
- **Tap targets:** at least 24×24px (WCAG 2.5.8) and 44×44px recommended on touch. Standalone nav icons and footer links fail the most.
- **Tablet:** the forgotten size. Look for super-narrow 3-column grids, cramped desktop navigation or lots of empty space.
- **Mobile:** does the content reflow sensibly? Is the CTA still on the first screen? Does the hero take three screens of scrolling before saying anything?
- **Images:** pixelated from being upscaled (`upscaled_blurry`), oversized (`oversized`, which hurts performance), distorted or broken (`broken`).

## 6. Interactive states

Look at `states/states-sheet.png`:
- **Hover:** everything clickable should respond (color, underline, elevation). "NO CHANGE" on the sheet means an interface that doesn't respond. Buttons should have `cursor: pointer`.
- **Keyboard focus:** must always be visible, with at least 3:1 contrast against its surroundings. `outline: none` without a replacement is a critical accessibility failure. Fix: `:focus-visible { outline: 2px solid <accent>; outline-offset: 2px; }`.
- **Tab order:** should follow the visual order. Ideally the first Tab shows "Skip to content" (`skip_link_first`).
- **Feedback:** `:active` states, a distinguishable disabled state (not only opacity on an already grey background), and loading or success states on forms if there are any.

## 7. Motion

Look at `motion/motion-sheet.png` and `motion` in `capture.json`:
- **Purpose:** every animation should orient (where did this come from), give feedback (you pressed it) or guide attention. If it only decorates, it's too much.
- **Duration:** micro-interactions 100-250ms, entrances 250-500ms, and nothing in the UI should exceed 700ms. Slow animations make the site feel slow.
- **Easing:** ease-out (deceleration) for things entering, ease-in for things leaving, and never `linear` for UI movement (fine for spinners or continuous progress).
- **Performance:** animate only `transform` and `opacity`. Animating `width`, `height`, `top`, `left` or `margin` (`animates_layout`) causes reflow and jank.
- **Content invisible until scrolled:** if at "Load +0 ms" or "Scroll +60 ms" the content appears blank or semi-transparent, fast visitors or those with slow JS will see gaps. Scroll reveals should be short (≤ 400ms) and subtle (≤ 24px of movement).
- **Infinite animations:** one at most, and discreet. Several pulses, shimmers and moving gradients at once create noise and fatigue.
- **`prefers-reduced-motion`:** if `reduced_motion.respected` is false, it's a high-severity accessibility problem (dizziness, vestibular disorders). Fix: `@media (prefers-reduced-motion: reduce) { *, *::before, *::after { animation-duration: .01ms !important; animation-iteration-count: 1 !important; transition-duration: .01ms !important; scroll-behavior: auto !important; } }`, or better, disable the specific animations.
- **Hijacked scroll** (Lenis, Locomotive and similar in `libraries`): does the wheel respond naturally or lag behind? The `scroll.webm` video shows it.

## 8. Basic accessibility

On top of the above: `lang` on `<html>`, a single `h1` without skipped levels (`headings.skipped_levels`), images with `alt` (decorative ones with `alt=""`), buttons and links with an accessible name (`missing_accessible_name`, typical of icon-only buttons), and information that doesn't depend on color alone.

## 9. Tells of AI-made design

The problem isn't that an AI made it. The problem is that **it looks like a thousand other sites, so it says nothing**. Once visitors notice, they stop believing the content. Typical tells (one alone doesn't condemn; several together do):

**Visual**
- Purple-indigo-pink gradient (#6366f1 → #a855f7 → #ec4899) in the hero, or a blurred "aurora" background.
- Headline in gradient text (`contrast.gradient_text`).
- Pill badge above the headline: "✨ Now with AI", "🚀 New".
- Emojis used as icons in card titles (⚡🔒🎯🚀).
- Three identical "feature" cards with icon, title and two lines, all `rounded-2xl` with a diffuse brand-colored shadow.
- Everything centered and every section with the same padding, with no variation in rhythm.
- Inter or the system font with no other typographic decision.
- Glassmorphism, glows and gradient borders everywhere.
- 3D mockups, blobs and made-up client logos.

**Copy**
- "Unlock the power of…", "Seamless", "Supercharge", "Revolutionary", "Cutting-edge", "Effortlessly", "Elevate", "Empower", "Transform the way you…", "Built for the modern…", and their equivalents in other languages (e.g. Spanish "Lleva tu X al siguiente nivel", French "Propulsez votre…").
- Benefits that could belong to any product ("Fast", "Secure", "Smart").
- The "Get Started Free" + "Learn more →" pair.

**How to critique it well.** Saying "it looks AI-made" doesn't help. Point out the specific pattern and propose a direction specific to *this* product: a typeface with character that fits its tone, its own brand color instead of the default purple, benefits rewritten with real data or examples from the product, an asymmetric composition, or a visual element only this brand would have. If other design skills are installed (for redesigning or for component inspiration), suggest them as the next step for the redesign.
