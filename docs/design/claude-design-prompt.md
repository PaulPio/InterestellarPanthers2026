# Claude Design prompt: Crew Context Monitor, mission-control restyle

Two parts. Part A is the prompt to paste into Claude Design. Part B is the research behind it and a gallery of NASA and SpaceX tools, with what was verified and what was not.

Evidence tags in Part B: `[proof: ...]` means read or inspected during this research session; `{unverified}` means it came from a search summary or press coverage only, or could not be opened.

---

## Part A. The prompt (paste everything between the rules)

---

You are redesigning the visual language of an existing, working web dashboard so it feels like professional-grade mission-control software, in the family of NASA Open MCT, the NASA/JPL F Prime ground data system, and SpaceX's Crew Dragon displays. This is a restyle and a layout refinement. It is not a feature project.

### 1. The product

Crew Context Monitor is a decision-support prototype for a spacecraft crew and the ground team that supports them. A wearable and cabin sensors feed a monitor. The dashboard shows four risk cards in a fixed order, **Cardiovascular, Air quality, Thermal, Radiation (proxy)**, each with a level of Green, Yellow or Red, up to three numbers shown against the person's own baseline, one plain sentence of reasoning, and a suggested next step. Around them sit an overall status bar, one chip per crew member, a fused cabin-CO2 and pulse-rate-variability timeline, an Earth queue of summaries that only leave the vehicle after a crew member approves them, an events log, a history view with a CUSUM drift chart, settings, and a floating daily check-in.

Readers: a crew member who glances at it for two seconds between tasks, and a flight surgeon or engineer who studies it for twenty minutes. Both must be served by one interface. The home view is calm and sparse at a glance; detail is one click deeper and dense.

Screens to design (all exist today; keep their content, order and behavior):
1. **Home**: status bar, crew strip, four risk cards (each card is a link to its domain), fused timeline, Earth queue panel.
2. **Domain detail**: one domain, its metric charts, the detector explanation, the "why not a fixed threshold" note.
3. **History**: metric charts plus the CUSUM chart.
4. **Events**: the event log with filter chips.
5. **Settings**: preferences, with an in-app confirm dialog for risky changes.
6. **Daily check-in**: floating prompt with short word scales (for example Sleep).
7. **Banner states**: "Waiting for the monitor", "Cannot reach the monitor", stale data ("Showing the last data, N s old"), and offline nodes. One fixed-height slot under the header.
8. **Learning state**: while a baseline is still being learned, the status bar shows "Learning. Alerts start in 38 min" instead of a domain and attribution. This is not a level; design it as a distinct fourth state that never looks like Green.

### 2. Design direction

Aim for "quiet board, loud exceptions". Nominal state should be nearly monochrome and calm. Color, motion and contrast are spent only on things that need a decision.

**Visual language**
- Dark-first "ops room" theme as the hero, with a light "day" theme of equal quality, switched by `prefers-color-scheme`. Both are required. Neutral blue-gray surfaces, 1px hairlines, almost no shadows, no gradients, no glass, no decorative illustration.
- Dense but ordered: a 4/8px spacing grid, compact chrome (rows and toolbars in the 22 to 30px range for the expert views, as Open MCT does), and generous touch targets (at least 48px, ideally 56px) on the check-in, the confirm dialog and the Approve button.
- Typography: a clean sans for labels and prose, and a monospace or tabular-figure face for every number, timestamp and unit so columns align and digits do not jiggle as values change. Choose faces whose `l`, `1`, `I`, `O`, `0` are clearly distinct. Mixed case for words; uppercase only for tiny section labels. No network fonts: ship self-hosted subsets (the product must work offline) or use a system stack.
- Instrument-panel detail that earns its place: a thin level rail on the left edge of each tile, tick marks on axes, a baseline band behind each sparkline, small caps labels, unit suffixes in a muted tone, consistent 12-column alignment. Nothing ornamental.

**Alert semantics (this is the core of the brief)**
- Level palette follows aviation and NASA display practice: red is the warning level, yellow/amber is the caution level, green is nominal. A blue "advisory/info" tone is allowed for non-alert notices. Keep the whole palette to five hues or fewer (red, amber, green, blue, neutrals) plus two chart series colors.
- Every level is shown with **three cues**: the word ("Green", "Yellow", "Red", spelled exactly so), a distinct shape glyph (for example a filled circle, a triangle, a square-cornered diamond), and the color. Color is never the only carrier. It must still be readable in grayscale and for red-green color blindness.
- Text on any level background must reach at least 6:1 contrast (10:1 for the large headline readouts). Verify, do not assume.
- Out-of-range values get a small up or down arrow after the number in the level color, as NASA display practice does, plus a limit or baseline line on charts with a text label, not color alone.
- Distinguish **measured**, **baseline/nominal**, and **stale** data visually and consistently: measured is a solid line; the person's baseline is a dashed line or a soft band; stale data is dimmed and hatched with an explicit data-age label ("12 s ago", "3 min ago"). Never present a baseline as if it were a measurement.
- No blinking. If you want to draw attention to a change, use one slow single pulse on the tile border, and disable it under `prefers-reduced-motion`.

**Status and link indicators**
- Add an always-visible **data-flow indicator** in the header or status bar, modeled on F Prime's "orb": a small green dot while data is arriving, an amber ring while data is late, a red cross when the link has timed out. It must be driven only by what the client already knows (whether polling is current, stale or failing, and whether any node reports offline); it adds no API field. It supplements the banner and the per-crew Hardware or Simulator badge and Online or Offline word, and replaces none of them.
- Optional and display-only: a UTC clock in the header, mono and tabular. Timeline axes may be labeled in UTC. Do not introduce any new data.

**Components to reimagine**
Only restyle what exists. Where a component below mentions a new visual device, it must be computed from data the component already receives.

- **Status bar** (today: level badge, the worst card's sentence, "Next step", and a right-hand column of domain and attribution of Cabin, Individual or Unclear, or the Learning countdown) becomes a master-annunciator strip. Same content, same fixed 96px height, with the level annunciator on the left, the sentence in the middle, and a right-hand readout column in mono. Add the data-flow indicator here or in the header.
- **Crew strip** (today: one 240px chip per person with name, level badge, Hardware or Simulator word, Online or Offline; selecting a chip chooses whose pulse-rate variability the timeline shows) becomes compact crew tiles with a clear selected state that is not color-only. The Hardware or Simulator badge must stay on every chip. Fixed 88px row height; chips wrap rather than scroll.
- **Risk cards** (today: domain name, level badge, up to three rows of value, unit and baseline, a reason clamped to four lines, and a "Next step" clamped to two) become telemetry channel tiles. For each row keep label, value, unit and baseline, and add a thin **value-against-baseline tick bar** drawn from those two numbers alone, with a baseline tick. Level annunciator top right, reason sentence below, next step anchored to the bottom. Fixed 288px height; the whole tile stays one link with a clear hover and focus state.
- **Fused timeline** (today: one plot, cabin CO2 on the left axis and pulse-rate variability or heart-rate residual on the right axis, a 6-hour window, a two-button series toggle, solid event lines for observed events and dashed lines for simulated ones, a shaded lag band, and a one-line lag sentence above) keeps that fused dual-axis design, because the point is to see one against the other. Restyle it with the time-conductor feel: a quiet grid, mono ticks in UTC or local time, axis labels colored to match their series with the series name spelled out, a crosshair that reads both values at once, and event markers that differ by line style and by a small glyph, not color alone. The two series colors must stay distinct and colorblind-safe (today amber and blue).
- **Earth queue** (today: a header with "Next comms window in ...", then one row per item with a tier of Crew only, Flight surgeon or Urgent, a byte count, a state of Waiting for approval, Approved waiting for the next window, or Sent, an Approve button, a one-line summary, and an expandable "What would be sent" payload) becomes a downlink-queue table in the style of F Prime's Downlink tab. Make the approval gate unmistakable: unapproved rows look pending and carry the one primary action; approved and sent rows are visibly settled. Show the payload disclosure in a mono block.
- **Events** (today: filter chips All, Spikes, Drifts, Links, Simulated events, Check-ins, Nodes; rows with mono time, a kind label, a sentence, crew, domain and a "Simulated" pill; check-ins muted as context) becomes a dense table in the F Prime Events style. Events have kinds, not severities, so give each kind a distinct small glyph and word and do not color them like alert levels. Keep the Simulated pill, the muted check-in rows and the filter chips (as proper toggle buttons).
- **Settings** becomes a labeled form with grouped sections. The confirm dialog uses a black-and-yellow diagonal hazard-stripe border for irreversible actions only (a NASA display convention) and nowhere else.
- **Daily check-in** stays a floating, dismissible prompt, with large word buttons usable with gloves. At most one decision per screen. Check-ins are context only and never change a level; keep that visually calm.

Interaction principle from the Dragon crew displays: few controls, big targets, nothing hidden behind gestures, and a visible way to back out of everything.

### 3. Hard constraints (these are tested in CI and must not break)

**Copy rules (visible strings)**
- Footer, always visible: "Decision-support prototype, not a medical device".
- Each reason is one sentence containing a number and a comparison to the person's own baseline. Never show z-scores.
- Say "pulse-rate variability", never "HRV". Radiation is always labeled a "proxy": the card is named "Radiation (proxy)".
- Use "consistent with", never "caused by". The product detects nothing and diagnoses nothing; do not invent medical wording, new alerts, new metrics or new fields.
- Level words are exactly Green, Yellow and Red, shown beside the color.
- Card order is fixed: Cardiovascular, Air quality, Thermal, Radiation (proxy). Nav is Home, History, Events, Settings.

**Layout stability**
- Nothing may jump. The status bar (96px), the risk cards (288px), and the banner slot (3rem) keep their fixed sizes whatever the data does. Level text changing from Green to Yellow must not move a single pixel of the surrounding layout.
- Content max width stays 1280px, centered. The tested viewports are 1280x800 and 390x844; at 390px nothing may overflow horizontally, and risk cards stack in one column (two columns from the small breakpoint, four from the extra-large one, as today).
- The status bar must be exactly the same height in the Green, Yellow, Red and Learning states.

**Accessibility**
- Zero axe violations; WCAG 2.2 AA at minimum for contrast, focus and target size. Visible focus rings (2px, offset 2px, high contrast in both themes). Full keyboard operation. Accessible names must contain their visible label text. Respect `prefers-reduced-motion`. Charts need a text alternative and a data summary.

**Tech**
- React 19, TypeScript, Tailwind v4, Recharts 3. Express the design as CSS custom properties in `src/styles/index.css` using the current token names (`--surface`, `--panel`, `--ink`, `--muted`, `--line`, `--accent`, `--green` `--green-bg` `--green-mark`, the same for yellow and red, `--series-co2`, `--series-prv`), adding new tokens where needed, with dark overrides under `prefers-color-scheme`.
- Keep every existing test hook: `data-testid` values `status-bar`, `crew-strip`, `card-{domain}`, `source-{crewId}`, `timeline-chart`, `lag-label`, `cusum-chart`, `detector`, `why-not-fixed`, `window-countdown`, `queue-{id}`, `queue-state-{id}`, `event-{id}`, `simulated-{id}`, `banner-slot`; the `data-level`, `data-state` and `data-kind` attributes; the `aria-pressed` toggle buttons; and every accessible name the tests read.
- JavaScript budget: gzip total at or under 400 kB (today 184.7 kB). No new runtime dependency unless it is small and justified. No CDN, no external images, no external fonts.

**Out of scope**
- No new pages, features, commands or data. No detection logic. No hardware or firmware concepts. No SpaceX or NASA logos, names, marks or copied proprietary assets; take the principles, not the pixels.

### 4. What to deliver

1. A one-page **design brief** stating the visual principles you chose and why, and a token table (both themes) with computed contrast ratios for every text-on-background pair.
2. High-fidelity designs for **Home** (dark and light), **Domain detail**, **Events**, **Settings with confirm dialog**, **check-in**, and each **banner state**, plus a Red state, a Learning state and a Stale state of Home.
3. A **component sheet**: level annunciator (3 levels x 3 cues) plus the Learning state, data-flow indicator (3 states), source badges, crew tile (selected and not), telemetry tile with tick bar, queue row (pending, approved, sent), event row per kind, series toggle, buttons, focus ring.
4. A short **handoff note** mapping each component to the existing React file (`StatusBar`, `CrewStrip`, `RiskCards`, `LevelBadge`, `Timeline`, `MetricChart`, `CusumChart`, `EarthQueuePanel`, `ConfirmDialog`, `CheckinPrompt`, `Banner`, `Layout`) and listing any new CSS tokens.

Before you finish, check your own work against section 3 and say which constraints you verified and how.

---

## Part B. Research notes and sample gallery

### What drove each rule

| Rule in the prompt | Source | Evidence |
|---|---|---|
| Red for emergency and warning, yellow for caution, blue for advisory | NASA HIDH Appendix F Display Standard | [proof: WebFetch of nasa.gov/reference/appendix-f-vol-2, section F.6.4] |
| Critical information needs a cue beyond color; color must convey meaning | same, section F.5.10 | [proof: same fetch] |
| Contrast at least 6:1, 10:1 preferred | same, F.5.10 | [proof: same fetch] |
| Sans-serif, fixed-width for numbers, mixed case, distinguishable `l`/`1`/`O`/`0` | same, F.5.1 | [proof: same fetch] |
| Flashing reserved for immediate action; slow 0.8 Hz at 70% duty and fast 3 Hz at 50%; crew can terminate after 10 s | same, F.5.11 | [proof: same fetch]. The prompt goes stricter than this and forbids blinking. |
| Arrow after an out-of-limit value; stale data must be indicated; graphs show limit lines with text, not color alone | same, F.4.3.1 | [proof: same fetch] |
| Hazard-stripe border for hazardous or irreversible commands | same, F.5.10 | [proof: same fetch] |
| "Orb" data-flow indicator: green circle for data arriving, red X on disconnect | NASA/JPL F Prime GDS docs | [proof: WebFetch of fprime.jpl.nasa.gov GDS introduction] |
| Events table colored by severity (gray, blue, yellow, orange, red), sortable and filterable; invalid fields outlined red; Downlink tab with progress; user-built Dashboard tab | same | [proof: same fetch] |
| Compact shell dimensions: time conductor 25px, toolbar 29px, tabular header 22px, spacing 3/5/10px | Open MCT source, `src/styles/_constants.scss` | [proof: WebFetch of raw.githubusercontent.com/nasa/openmct/master/src/styles/_constants.scss] |
| Web-based, desktop and mobile, used at JPL and for NASA Ames lunar rover concept work, extended by plugins | Open MCT site and README | [proof: WebFetch of nasa.github.io/openmct and github.com/nasa/openmct] |
| Few controls for mission-critical actions, hardware-button fallback, gloved touch, Chromium as renderer only | InfoQ on SpaceX Dragon | [proof: WebFetch of infoq.com/news/2020/06/javascript-spacex-dragon] |
| Color supplements other coding; at most five colors; neutral gray or black controls | NASA-STD-3001 crew controls technical brief OCHMO-TB-052 | {unverified}: search summary only, document not opened |
| Use few, highly distinct colors; apply consistently; pair with other cues; avoid on tiny elements | NASA Ames color usage guidelines | {unverified}: search summary only; the page itself failed to load (DNS error) |

### Sample gallery

Open these yourself or attach screenshots when you run the prompt; Claude Design does better with a visual reference.

**NASA and NASA-funded**
1. **Open MCT**: [nasa.github.io/openmct](https://nasa.github.io/openmct/) and [github.com/nasa/openmct](https://github.com/nasa/openmct). Pattern to borrow: tree plus display layouts, a time conductor, dense telemetry tables, stacked plots. [proof: both pages fetched]. I could not open the hosted demo (the demo URL I tried timed out), so I have not seen its screens.
2. **F Prime ground data system**: [GDS introduction](https://fprime.jpl.nasa.gov/devel/docs/user-manual/overview/gds-introduction/). Pattern to borrow: top tabs, severity-colored event rows, the orb. [proof: page fetched]. No screenshot was viewed.
3. **NASA HIDH Appendix F Display Standard**: [nasa.gov/reference/appendix-f-vol-2](https://www.nasa.gov/reference/appendix-f-vol-2/). The rules above. Tables F.5-1 and F.5-2 (the actual color assignments) are images the fetch could not read, so nominal and advisory colors are not confirmed; the prompt chooses green for nominal on its own judgment.
4. **NASA Human Integration Design Handbook, Crew Interfaces**: [section 10](https://www.nasa.gov/reference/10-0-crew-interfaces-vol-2/) and [Display Standard search hit](https://ntrs.nasa.gov/api/citations/20080010620/downloads/20080010620.pdf). {unverified}: listed in search results, not opened.

**Open-source ground systems (not NASA-owned, but used in that world)**
5. **Yamcs**: [yamcs.org](https://yamcs.org/). Synoptic displays, alarms and events, timelines, command stacks. [proof: page fetched]. The page names no NASA mission, so no NASA use is claimed here.
6. **OpenC3 COSMOS**: [openc3.com](https://openc3.com/). The page mentions limits and alerts, dashboards, logging and replay, with no design detail. [proof: page fetched]. Its docs at docs.openc3.com are the place to look for screen design; not opened.

**SpaceX**
7. **Crew Dragon ISS docking simulator**: [iss-sim.spacex.com](https://iss-sim.spacex.com/). Built by SpaceX as a public browser sim. [proof: inspected the live page's DOM and stylesheet]. It uses the typeface Lato, a black background, muted slate text `#818991`, white `#fff`, amber `#ffa100`, cyan `#00d0ff` and `#24d2fd`, green `#2aff00`, and dark panel tones `#151c23` and `#232b33`; its page is built from three.js, GSAP, a HUD, a navball and separate rotation and translation control groups. The palette is the useful lesson: near-black, one cool accent, amber for attention, green for success. I did not see the rendered UI because it sat on its loading spinner in the hidden browser pane. That the controls mirror the real spacecraft interface comes from press coverage ([TechCrunch](https://techcrunch.com/?p=1987635), [HotHardware](https://hothardware.com/news/spacex-docking-simulator)) {unverified}.
8. **How Dragon's displays are built**: [InfoQ, "SpaceX uses JavaScript for Dragon displays"](https://www.infoq.com/news/2020/06/javascript-spacex-dragon/). Chromium plus web components and a custom reactive framework; the same reliability process as other flight code; few buttons for critical actions; physical buttons as a fallback. [proof: page fetched]. The takeaway for this product is the discipline (restraint, big targets, graceful fallback), not any visual detail, since no screenshots were examined.

**How Part A was checked against the app**
- The component descriptions in section 2 were compared with the source of `StatusBar`, `CrewStrip`, `RiskCards`, `LevelBadge`, `Banner`, `Timeline`, `EarthQueuePanel`, `Events`, `Layout` and `lib/levels.ts`, and the token names with `dashboard/src/styles/index.css`. [proof: those files were read in this session]
- Not read before writing: `Settings`, `Domain`, `History`, `CheckinPrompt`, `ConfirmDialog`. Their descriptions in Part A are deliberately brief. {unverified}
- Not run: the redesign itself. Any output from Claude Design still has to pass `make check` and the Playwright layout and axe tests before it ships.

**Gaps, stated plainly**
- No official SpaceX webcast overlay or Starship ground-software screenshots were found with a usable source, so none are cited.
- No NASA or SpaceX screen was viewed as a rendered image in this session. Everything visual above is taken from page code, stylesheets, text descriptions and standards documents. Before locking the direction, someone should look at real screenshots of Open MCT and the F Prime GDS and attach two or three to the Claude Design run.
