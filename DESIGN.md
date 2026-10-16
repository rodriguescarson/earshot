---
name: Earshot
description: Live audio-described football commentary; the screen is a wayfinding plate for the ears.
colors:
  chalk-ground: "#f1f3f2"
  charcoal-ink: "#1f2427"
  slate-soft-ink: "#3f474c"
  on-plate-soft: "#c9cfcc"
  seam-grey: "#6c7479"
  signal-red: "#c42b21"
  signal-red-bright: "#ff6b5e"
  night-ground: "#121618"
  night-ink: "#eef1ef"
  night-soft-ink: "#bcc3bf"
  night-plate-soft: "#3c4448"
  night-seam: "#7d878c"
typography:
  display:
    fontFamily: "Atkinson Hyperlegible Next Variable, Atkinson Hyperlegible Next, system-ui, sans-serif"
    fontSize: "clamp(2.75rem, 11vw, 4.5rem)"
    fontWeight: 800
    lineHeight: 1
    letterSpacing: "-0.02em"
  headline:
    fontFamily: "Atkinson Hyperlegible Next Variable, Atkinson Hyperlegible Next, system-ui, sans-serif"
    fontSize: "clamp(2rem, 8vw, 3.25rem)"
    fontWeight: 800
    lineHeight: 1.1
    fontFeature: "tnum"
  title:
    fontFamily: "Atkinson Hyperlegible Next Variable, Atkinson Hyperlegible Next, system-ui, sans-serif"
    fontSize: "clamp(1.75rem, 7vw, 2.5rem)"
    fontWeight: 800
    lineHeight: 1.15
  on-air:
    fontFamily: "Atkinson Hyperlegible Next Variable, Atkinson Hyperlegible Next, system-ui, sans-serif"
    fontSize: "clamp(1.375rem, 5vw, 1.75rem)"
    fontWeight: 700
    lineHeight: 1.3
  body:
    fontFamily: "Atkinson Hyperlegible Next Variable, Atkinson Hyperlegible Next, system-ui, sans-serif"
    fontSize: "1.125rem"
    fontWeight: 400
    lineHeight: 1.5
  label:
    fontFamily: "Atkinson Hyperlegible Next Variable, Atkinson Hyperlegible Next, system-ui, sans-serif"
    fontSize: "1.125rem"
    fontWeight: 700
    lineHeight: 1.5
  small:
    fontFamily: "Atkinson Hyperlegible Next Variable, Atkinson Hyperlegible Next, system-ui, sans-serif"
    fontSize: "1rem"
    fontWeight: 400
    lineHeight: 1.5
rounded:
  plate: "2px"
spacing:
  cell: "0.5rem"
  cell-1-5: "0.75rem"
  cell-2: "1rem"
  cell-3: "1.5rem"
  cell-4: "2rem"
  cell-6: "3rem"
  cell-8: "4rem"
components:
  button-primary:
    backgroundColor: "{colors.charcoal-ink}"
    textColor: "{colors.chalk-ground}"
    typography: "{typography.label}"
    rounded: "{rounded.plate}"
    padding: "1rem 1.5rem"
    height: "3rem"
  button-secondary:
    backgroundColor: "{colors.chalk-ground}"
    textColor: "{colors.charcoal-ink}"
    typography: "{typography.label}"
    rounded: "{rounded.plate}"
    padding: "1rem 1.5rem"
    height: "3rem"
  button-link:
    textColor: "{colors.charcoal-ink}"
    typography: "{typography.label}"
    height: "3rem"
  plate:
    backgroundColor: "{colors.charcoal-ink}"
    textColor: "{colors.chalk-ground}"
    rounded: "{rounded.plate}"
    padding: "1.5rem"
  on-air-panel:
    backgroundColor: "{colors.chalk-ground}"
    textColor: "{colors.charcoal-ink}"
    typography: "{typography.on-air}"
    rounded: "{rounded.plate}"
    padding: "1.5rem"
  choice:
    backgroundColor: "{colors.chalk-ground}"
    textColor: "{colors.charcoal-ink}"
    typography: "{typography.label}"
    rounded: "{rounded.plate}"
    padding: "1rem"
---

# Design System: Earshot

## Overview

**Creative North Star: "The Tactile Plate"**

Earshot's screen is a station wayfinding plate: braille signage, tactile maps and blister paving, read at arm's length, zoomed in, or not at all. The match is a plate that always says where you are. Sound carries where the ball is; the screen confirms, in one glance, three things only: the score, the minute and the line on air now.

The world is two colours plus one spot. Charcoal plates sit on a chalk ground; a faint raised-dot grid (blister paving) runs under everything and doubles as the spacing armature, so every plate snaps to whole cells and the grid scales with the reader's own text size. Depth comes from seams and reversal, never shadows. The one spot colour, signal red, means "on air now" and nothing else. Every state is carried by form first (a filled, hollow or doubled dot; a corduroy band for attention) and colour second, so nothing depends on seeing red.

Density is low and the type is large because the people looking at this screen are often partially sighted, zoomed, or glancing at a phone in a pocket. Blind listeners meet the same screen as structure through VoiceOver or TalkBack, and for them the heading order and the live regions are the design.

**Key Characteristics:**
- Two colours and one spot; the spot is reserved for "on air now".
- Square-cut plates (2px corners) divided by seams; flat, no shadows.
- A raised-dot ground grid on a 0.5rem cell; all spacing in whole or half cells.
- One typeface, Atkinson Hyperlegible Next, ranked by weight and size only.
- State by form first: filled, hollow and doubled dots.
- Light and dark themes follow the device; both measured at WCAG AA or better.

## Colors

A cool chalk-and-charcoal pair with one signal red, inverted wholesale for dark mode.

### Primary
- **Signal Red** (`signal-red` on light surfaces, `signal-red-bright` on dark surfaces): the on-air dot, the focus ring, the "cut" mark on the airtime bar and the "On air" tag in the agent table. On chalk it measures 5.07:1; the bright red on the charcoal plate 5.61:1. In dark mode the two swap roles (bright red on the night ground at 6.52:1, the deeper red on a chalk plate at 4.97:1).

### Neutral
- **Chalk Ground** (`chalk-ground`): the page background and the text on charcoal plates. Cool, never cream.
- **Charcoal Ink** (`charcoal-ink`): body text, borders of the on-air panel, and every reversed plate (score plate, primary button). Text on chalk at 14.06:1.
- **Slate Soft Ink** (`slate-soft-ink`): secondary text, hints, earlier transcript lines, the step counter. 8.5:1 on chalk.
- **Plate Soft** (`on-plate-soft`): secondary text on a charcoal plate (the score status, the braille wordmark). 9.91:1.
- **Seam Grey** (`seam-grey`): seams between plates, unselected choice borders, table rules. 4.27:1 on chalk (a UI part, so 3:1 is the bar).
- **Night Ground, Night Ink, Night Soft Ink, Night Plate Soft, Night Seam** (`night-*`): the dark theme. The plate becomes chalk-coloured (`night-ink`) with night-ground text; ratios are 16:1 for plate text, 10.14:1 for soft ink on the ground, 4.95:1 for seams.
- **Dot grid**: a 12% mix of ink into the ground in light mode (10% in dark), computed with `color-mix` in OKLab so it stays faint in both themes.

### Named Rules
**The One Spot Rule.** Signal red appears only where something is on air now, or as the keyboard focus ring. It is never decoration, never an error colour by itself, never a brand fill.

**The No Blue Rule.** No blue anywhere, never orange with blue, no yellow or cream grounds. The palette is chalk, charcoal and one red.

**The Form Before Colour Rule.** Every state readable in red must also be readable in greyscale: the on-air dot is filled, waiting is hollow, never-drop is doubled, attention is a corduroy band.

## Typography

**Display Font:** Atkinson Hyperlegible Next (with system-ui)
**Body Font:** Atkinson Hyperlegible Next (with system-ui)

**Character:** One family designed for low vision, ranked by weight (400, 700, 800) and size steps only. Letterforms that cannot be confused (I/l/1, O/0) matter more here than personality, and the variable font's tabular figures keep the score from jittering as it changes.

### Hierarchy
- **Display** (800, clamp(2.75rem, 11vw, 4.5rem), line-height 1, -0.02em): the Earshot wordmark on the landing plate only.
- **Headline** (800, clamp(2rem, 8vw, 3.25rem), line-height 1.1, tabular figures): the score line ("Sparkwheel 0, Saltwings 0. Minute 3."). The largest type on the listening screen, always.
- **Title** (800, clamp(1.75rem, 7vw, 2.5rem), line-height 1.15): setup step questions, one per screen.
- **On air** (700, clamp(1.375rem, 5vw, 1.75rem), line-height 1.3): the line on air now, inside the on-air panel; it reserves two lines of height so the panel does not jump.
- **Body** (400, 1.125rem, line-height 1.5): running text, transcript lines, recaps (recaps cap at 65ch).
- **Label** (700, 1.125rem): buttons, choice labels, the score status and the step counter.
- **Small** (400, 1rem): hints, headphone notes, table cells and captions.

### Named Rules
**The Reader's Size Rule.** The root is 100% of the reader's setting and every size is in rem, so the device's text size scales the whole plate, including the dot grid.

**The Score Is Biggest Rule.** On the listening screen nothing outranks the score line in size or weight.

## Layout

A single centred column, `min(100%, 44rem)` wide, padded 4 cells top, 3 cells at the sides and 8 at the bottom, with 3-cell gaps between plates. The landing centres vertically in the viewport. Setup uses the same column with one fieldset per screen.

On the listening screen the order is fixed: score plate, on-air panel, controls, transcript, recaps. Controls sit in a two-column grid where the primary action (Pause/Resume) spans the full width; under 22rem the grid becomes one column. In short landscape (height under 32rem) the column widens to 64rem and splits in two, with the score plate and on-air panel spanning both.

At 64rem (1024px) and wider, a producer column appears beside the listening column: `minmax(0, 40rem)` plus `1fr`, 6-cell gap, up to 88rem wide, with the agent panel sticky at 4 cells from the top.

All spacing is in multiples of the 0.5rem cell: 1, 1.5, 2, 3, 4, 6 and 8.

## Elevation & Depth

Flat, by doctrine. There are no shadows anywhere. Depth is conveyed three ways: reversal (a charcoal plate on a chalk ground), seams (2px seam-grey or charcoal borders), and the raised-dot ground grid, which reads as a surface everything else sits on. Pressing a button moves it down 2px, like a lift button, and that is the only motion that suggests height.

### Named Rules
**The Seams Not Shadows Rule.** Separate plates with a seam or a reversal. A box-shadow is a defect.

## Shapes

Square-cut signage: every plate, button, panel and choice has the same 2px corner, just enough to stop the edge from looking sharp. Borders are 2px (3px for a selected choice). The only round forms are the state dots (18px, drawn in SVG) and the dot grid itself.

## Components

### Buttons
Lift-button plates: blunt, large, left-aligned text.
- **Shape:** square-cut (2px), minimum height 3rem, padding 2 cells by 3 cells.
- **Primary:** a charcoal plate with chalk text and a 2px charcoal border. One per screen.
- **Secondary:** chalk ground, charcoal text, 2px seam border. Used for "Where's the ball?" and "Catch me up".
- **Link:** no plate, underlined 2px at 0.25em offset, 3rem tall for touch. Used for the quiet second path on a screen ("Choose language and style", "Join the live match", Back).
- **Press:** moves down 2px over 120ms (cubic-bezier(0.2, 0.8, 0.2, 1)); motion is removed under reduced-motion.
- **Focus:** a 3px signal-red outline at 3px offset (the bright red on dark plates).
- **Disabled:** 70% opacity with a progress cursor (used while connecting).

### Choice (radio plate)
- **Style:** a plate-outlined row with the native radio (1.5rem, ink accent), the label in label weight and a hint in soft ink below.
- **Selected:** the border goes from 2px seam to 3px charcoal. The radio's own fill carries the state too, so selection never depends on the border alone.

### Score plate
A reversed charcoal plate holding the headline score line and a status line in plate-soft ink ("Live", "Paused", "Reconnecting to the match..."). It takes focus when the screen opens, so a screen reader starts from the score.

### On-air panel
A chalk panel with a 2px charcoal border holding the state dot and the line on air now. The dot is filled red when a line is on air, hollow when waiting, and doubled for never-drop events.

### State dot (signature)
The state grammar's marker, 18px SVG: **filled** signal red = on air now; **hollow** charcoal ring = waiting; **doubled** charcoal dots = never-drop (goals, cards, penalties). Always paired with text; `aria-hidden` because the text carries the meaning.

### Transcript and recaps
- **Transcript:** a ruled list (2px charcoal rule on top, 1px seams between rows). The current line is in ink; earlier lines fade to soft ink over 300ms.
- **Recaps:** each recap is a block with a 2px seam on top, a small 800-weight heading ("Half-time") and body text capped at 65ch. Recaps never go through the live region line by line; one announcement says the recap is ready.

### Producer column (desktop)
- **Airtime bar:** an SVG track with a 2px charcoal border; caller airtime in ink, Analyst in a 45° hatch, fallbacks in a dot pattern, cuts in signal red. The legend uses swatches with the same patterns, so agents are told apart by pattern, not colour.
- **Agent table:** 1rem text, seam rules, a 2px ink rule under the header, agent names in 800 weight, times in tabular soft ink.

## Do's and Don'ts

### Do:
- **Do** use signal red only for "on air now" and the focus ring.
- **Do** pair every coloured state with a form (filled, hollow, doubled dot; corduroy band) and with text.
- **Do** separate surfaces with seams (2px) or a charcoal reversal.
- **Do** size everything in rem and space in 0.5rem cells, so the reader's text size scales the whole plate.
- **Do** keep one primary action per screen; second paths are underlined links.
- **Do** keep the score line the largest type on the listening screen, with tabular figures.
- **Do** measure contrast for any new colour pairing in both themes before shipping (AA: 4.5:1 text, 3:1 large text and UI parts).

### Don't:
- **Don't** use box-shadows, gradients as fills, or glass effects; the only patterned fills are the dot grid, the corduroy attention band and the agent legend hatches.
- **Don't** use blue, orange with blue, yellow or cream anywhere.
- **Don't** add a second typeface or decorative braille; the braille wordmark on the landing is the only braille.
- **Don't** round corners beyond 2px or introduce pill shapes.
- **Don't** put a visual ball strip on the listening screen; where the ball is lives in the sound.
- **Don't** make colour the only signal for any state.
