# MetalFlow Pro Color Theme Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Apply the exact user-supplied dark UI palette, typography, scrollbar, and strictly scoped PDC Living Circuit theme to MetalFlow Pro.

**Architecture:** Define one canonical token map in Tailwind and the React base stylesheet, then mirror those values into the standalone application's CSS variables. Add a final, narrowly scoped theme layer that supersedes the previous executive-theme overrides without changing application behavior. Keep the light PDC treatment dormant unless an element explicitly uses `.pdc-canvas`.

**Tech Stack:** React 19, Tailwind CSS 3, static HTML/CSS/JavaScript, Fontsource, Node.js verification script, Railway.

---

## File Structure

- Create `frontend/scripts/verify-theme.js`: static contract test for exact palette values, fonts, dark-mode defaults, PDC isolation, grain, and synchronized HTML files.
- Modify `frontend/package.json`: add Fontsource dependencies and a `test:theme` script.
- Modify `frontend/yarn.lock`: lock the three Fontsource packages.
- Modify `frontend/tailwind.config.js`: expose canonical `gold`, `teal`, `surface`, and `pdc` colors.
- Modify `frontend/src/index.css`: import Fontsource packages, set dark semantic tokens, default typography, scrollbar, and React-level PDC utilities.
- Modify `frontend/src/index.js`: apply the `dark` class before React renders.
- Modify `frontend/public/index.html`: replace external font loading, map legacy variables to the canonical palette, supersede conflicting executive-theme rules, and define isolated PDC classes.
- Modify `MetalFlowPro_v3_1.html`: keep the standalone file byte-identical to `frontend/public/index.html`.

### Task 1: Add the Theme Contract Test

**Files:**
- Create: `frontend/scripts/verify-theme.js`
- Modify: `frontend/package.json`

- [ ] **Step 1: Write the failing static contract test**

Create `frontend/scripts/verify-theme.js`:

```js
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const root = path.resolve(__dirname, "..", "..");
const htmlPath = path.join(root, "frontend", "public", "index.html");
const standalonePath = path.join(root, "MetalFlowPro_v3_1.html");
const cssPath = path.join(root, "frontend", "src", "index.css");
const tailwindPath = path.join(root, "frontend", "tailwind.config.js");
const indexPath = path.join(root, "frontend", "src", "index.js");

const html = fs.readFileSync(htmlPath, "utf8");
const standalone = fs.readFileSync(standalonePath, "utf8");
const css = fs.readFileSync(cssPath, "utf8");
const tailwind = fs.readFileSync(tailwindPath, "utf8");
const index = fs.readFileSync(indexPath, "utf8");
const allStyles = `${html}\n${css}\n${tailwind}`;

for (const color of [
  "#FCD34D", "#F59E0B", "#D97706",
  "#2DD4BF", "#0D9488", "#0F766E",
  "#0B0F1A", "#111827", "#1A2235", "#222D42",
  "#F5EFE2", "#1A2330", "#5D6878", "#C9A24A", "#FFD063", "#C24545",
]) {
  assert.ok(allStyles.includes(color), `missing canonical color ${color}`);
}

for (const fontImport of [
  '@fontsource-variable/inter',
  '@fontsource/ibm-plex-mono',
  '@fontsource/caveat',
]) {
  assert.ok(css.includes(fontImport), `missing ${fontImport}`);
}

assert.ok(index.includes('classList.add("dark")'), "dark class is not applied by default");
assert.ok(allStyles.includes("color-scheme:dark") || allStyles.includes("color-scheme: dark"));
assert.ok(allStyles.includes("#2A3A54"), "scrollbar color is not canonical");
assert.ok(html.includes(".pdc-canvas{"), "PDC scope is missing");
assert.ok(html.includes(".pdc-canvas .pdc-annotation"), "Caveat is not scoped to PDC annotations");
assert.ok(html.includes(".pdc-canvas-grain"), "paper grain class is missing");
assert.ok(html.includes("pointer-events:none"), "grain overlay must not capture input");
assert.ok(html.includes("rgba(63,111,168,.12)") || html.includes("rgba(63, 111, 168, 0.12)"));
assert.ok(html.includes("rgba(126,91,168,.12)") || html.includes("rgba(126, 91, 168, 0.12)"));
assert.equal(html, standalone, "standalone HTML is not synchronized");

console.log("MetalFlow theme contract: PASS");
```

Add this script entry to `frontend/package.json`:

```json
"test:theme": "node scripts/verify-theme.js"
```

- [ ] **Step 2: Run the test and verify it fails**

Run:

```bash
npm run test:theme --prefix frontend
```

Expected: failure on the first missing canonical theme value or Fontsource import.

- [ ] **Step 3: Commit the test**

```bash
git add frontend/scripts/verify-theme.js frontend/package.json
git commit -m "test: define MetalFlow theme contract"
```

### Task 2: Install Fonts and Define Canonical React/Tailwind Tokens

**Files:**
- Modify: `frontend/package.json`
- Modify: `frontend/yarn.lock`
- Modify: `frontend/tailwind.config.js`
- Modify: `frontend/src/index.css`
- Modify: `frontend/src/index.js`

- [ ] **Step 1: Install the specified Fontsource packages**

Run:

```bash
yarn --cwd frontend add @fontsource-variable/inter @fontsource/ibm-plex-mono @fontsource/caveat
```

Expected: the three packages appear in `frontend/package.json` and `frontend/yarn.lock`.

- [ ] **Step 2: Extend Tailwind with the exact palette**

Add these entries under `theme.extend.colors` in `frontend/tailwind.config.js`:

```js
gold: {
  400: "#FCD34D",
  500: "#F59E0B",
  600: "#D97706",
},
teal: {
  400: "#2DD4BF",
  500: "#0D9488",
  600: "#0F766E",
},
surface: {
  900: "#0B0F1A",
  800: "#111827",
  700: "#1A2235",
  600: "#222D42",
},
pdc: {
  canvas: "#F5EFE2",
  ink: "#1A2330",
  "ink-soft": "#5D6878",
  gold: "#C9A24A",
  "gold-hot": "#FFD063",
  alert: "#C24545",
},
```

- [ ] **Step 3: Replace React base tokens and typography**

At the top of `frontend/src/index.css`, add:

```css
@import "@fontsource-variable/inter";
@import "@fontsource/ibm-plex-mono/400.css";
@import "@fontsource/ibm-plex-mono/500.css";
@import "@fontsource/ibm-plex-mono/600.css";
@import "@fontsource/caveat/400.css";
@import "@fontsource/caveat/600.css";
```

Replace the light default semantic token block with a dark canonical default:

```css
:root,
.dark {
    color-scheme: dark;
    --background: 222 41% 7%;
    --foreground: 210 40% 96%;
    --card: 221 39% 11%;
    --card-foreground: 210 40% 96%;
    --popover: 221 39% 11%;
    --popover-foreground: 210 40% 96%;
    --primary: 38 92% 50%;
    --primary-foreground: 222 47% 6%;
    --secondary: 172 83% 32%;
    --secondary-foreground: 166 76% 92%;
    --muted: 222 34% 15%;
    --muted-foreground: 215 20% 65%;
    --accent: 222 34% 15%;
    --accent-foreground: 210 40% 96%;
    --destructive: 0 62% 50%;
    --destructive-foreground: 0 0% 98%;
    --border: 217 32% 20%;
    --input: 217 32% 20%;
    --ring: 38 92% 50%;
    --radius: 0.5rem;
}
```

Set the base fonts and scrollbar:

```css
html {
    color-scheme: dark;
}

body {
    font-family: "Inter Variable", Inter, system-ui, sans-serif;
}

code,
pre,
.font-mono {
    font-family: "IBM Plex Mono", ui-monospace, monospace;
}

* {
    scrollbar-color: #2A3A54 transparent;
}

*::-webkit-scrollbar-track {
    background: transparent;
}

*::-webkit-scrollbar-thumb {
    background: #2A3A54;
}
```

- [ ] **Step 4: Add reusable React-level PDC utilities**

Append to `frontend/src/index.css`:

```css
.pdc-canvas {
    color-scheme: light;
    --pdc-canvas: #F5EFE2;
    --pdc-ink: #1A2330;
    --pdc-ink-soft: #5D6878;
    --pdc-gold: #C9A24A;
    --pdc-gold-hot: #FFD063;
    --pdc-alert: #C24545;
    position: relative;
    isolation: isolate;
    background: var(--pdc-canvas);
    color: var(--pdc-ink);
}

.pdc-canvas .pdc-annotation {
    font-family: Caveat, cursive;
}

.pdc-canvas .pdc-source-lims { background: rgba(63, 111, 168, 0.12); }
.pdc-canvas .pdc-source-calculated { background: rgba(126, 91, 168, 0.12); }
.pdc-canvas .pdc-source-manual { background: rgba(184, 106, 42, 0.10); }
.pdc-canvas .pdc-source-project { background: rgba(181, 138, 46, 0.12); }
.pdc-canvas .pdc-source-design { background: rgba(140, 124, 104, 0.08); }
.pdc-canvas .pdc-source-default { background: rgba(168, 154, 130, 0.06); }
```

- [ ] **Step 5: Apply dark mode before React renders**

Add before `createRoot` in `frontend/src/index.js`:

```js
document.documentElement.classList.add("dark");
document.documentElement.style.colorScheme = "dark";
```

- [ ] **Step 6: Run the theme contract**

Run:

```bash
npm run test:theme --prefix frontend
```

Expected: still fails because the static HTML theme has not yet been updated.

- [ ] **Step 7: Commit React and Tailwind theme foundations**

```bash
git add frontend/package.json frontend/yarn.lock frontend/tailwind.config.js frontend/src/index.css frontend/src/index.js
git commit -m "feat: add canonical MetalFlow theme tokens"
```

### Task 3: Replace the Standalone HTML Theme and Add Strict PDC Scope

**Files:**
- Modify: `frontend/public/index.html`
- Modify: `MetalFlowPro_v3_1.html`

- [ ] **Step 1: Remove the external Google Fonts import**

Delete:

```css
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=Inter:wght@300;400;500;600;700&display=swap');
```

The built application receives fonts from Fontsource. The standalone HTML retains system fallbacks through `--sans` and `--mono`.

- [ ] **Step 2: Replace the legacy variable map with canonical values**

In the final theme `:root`, use:

```css
:root{
  color-scheme:dark;
  --bg:#0B0F1A;
  --bg1:#0B0F1A;
  --bg2:#111827;
  --bg3:#1A2235;
  --bg4:#222D42;
  --bg5:#2A3A54;
  --card:#111827;
  --surface:#1A2235;
  --txt:#E5E7EB;
  --txt1:#F8FAFC;
  --txt2:#CBD5E1;
  --txt3:#94A3B8;
  --txt4:#64748B;
  --border:#222D42;
  --border2:#2A3A54;
  --border3:#3A4A63;
  --gold:#F59E0B;
  --gold2:#FCD34D;
  --gold3:rgba(245,158,11,.16);
  --gold4:rgba(245,158,11,.08);
  --teal:#0D9488;
  --teal2:#2DD4BF;
  --teal3:rgba(13,148,136,.16);
  --sans:"Inter Variable",Inter,system-ui,sans-serif;
  --mono:"IBM Plex Mono",ui-monospace,monospace;
}
```

- [ ] **Step 3: Supersede conflicting executive-theme colors**

Update the final override layer so cards, panels, tables, inputs, navigation, authentication surfaces, buttons, selection, and scrollbars use only the canonical `surface`, `gold`, and `teal` tokens. Remove graphite, copper, steel, and metallic gradient values that redefine the supplied palette.

Use neutral text variables for body content. Restrict gold to accents, active states, headings that are already semantically highlighted, and primary actions.

- [ ] **Step 4: Add the isolated PDC Living Circuit layer**

Before the closing `</style>`, add:

```css
.pdc-canvas{
  color-scheme:light;
  --pdc-canvas:#F5EFE2;
  --pdc-ink:#1A2330;
  --pdc-ink-soft:#5D6878;
  --pdc-gold:#C9A24A;
  --pdc-gold-hot:#FFD063;
  --pdc-alert:#C24545;
  position:relative;
  isolation:isolate;
  overflow:hidden;
  background:var(--pdc-canvas)!important;
  color:var(--pdc-ink)!important;
}

.pdc-canvas :where(h1,h2,h3,h4,h5,h6,strong,b,label,span,p,small){
  color:inherit!important;
}

.pdc-canvas .pdc-ink-soft{color:var(--pdc-ink-soft)!important}
.pdc-canvas .pdc-gold{color:var(--pdc-gold)!important}
.pdc-canvas .pdc-gold-hot{color:var(--pdc-gold-hot)!important}
.pdc-canvas .pdc-alert{color:var(--pdc-alert)!important}
.pdc-canvas .pdc-annotation{font-family:Caveat,cursive;color:var(--pdc-ink)!important}
.pdc-canvas .pdc-source-lims{background:rgba(63,111,168,.12)!important}
.pdc-canvas .pdc-source-calculated{background:rgba(126,91,168,.12)!important}
.pdc-canvas .pdc-source-manual{background:rgba(184,106,42,.10)!important}
.pdc-canvas .pdc-source-project{background:rgba(181,138,46,.12)!important}
.pdc-canvas .pdc-source-design{background:rgba(140,124,104,.08)!important}
.pdc-canvas .pdc-source-default{background:rgba(168,154,130,.06)!important}

.pdc-canvas-grain::after{
  content:"";
  position:absolute;
  inset:0;
  z-index:50;
  pointer-events:none;
  border-radius:inherit;
  opacity:.04;
  background-image:url("data:image/svg+xml,%3Csvg viewBox='0 0 180 180' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='.9' numOctaves='3' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)' opacity='.55'/%3E%3C/svg%3E");
  mix-blend-mode:multiply;
}
```

- [ ] **Step 5: Synchronize the standalone HTML**

Run:

```bash
cp frontend/public/index.html MetalFlowPro_v3_1.html
```

Expected: `cmp -s frontend/public/index.html MetalFlowPro_v3_1.html` exits `0`.

- [ ] **Step 6: Run the theme contract**

Run:

```bash
npm run test:theme --prefix frontend
```

Expected:

```text
MetalFlow theme contract: PASS
```

- [ ] **Step 7: Validate inline JavaScript syntax**

Run:

```bash
node -e "const fs=require('fs'),vm=require('vm');const h=fs.readFileSync('frontend/public/index.html','utf8');const m=h.match(/<script>([\\s\\S]*)<\\/script>/);if(!m)throw new Error('inline script missing');new vm.Script(m[1]);console.log('inline script syntax: ok')"
```

Expected:

```text
inline script syntax: ok
```

- [ ] **Step 8: Commit the HTML theme**

```bash
git add frontend/public/index.html MetalFlowPro_v3_1.html
git commit -m "feat: apply MetalFlow dark and PDC themes"
```

### Task 4: Build, Inspect, and Deploy

**Files:**
- Verify: `frontend/public/index.html`
- Verify: `frontend/src/index.css`
- Verify: `frontend/build/`

- [ ] **Step 1: Run the complete local verification**

Run:

```bash
npm run test:theme --prefix frontend
npm run build --prefix frontend
cmp -s frontend/public/index.html MetalFlowPro_v3_1.html
```

Expected: theme contract passes, React compiles successfully, and `cmp` exits `0`.

- [ ] **Step 2: Start the local production build**

Run:

```bash
python3 -m http.server 4173 --directory frontend/build
```

Expected: server listens on port `4173`.

- [ ] **Step 3: Inspect dark UI in the browser**

Open `http://localhost:4173` and verify:

- `html` has class `dark` and `color-scheme: dark`;
- background resolves to `#0B0F1A`;
- primary surfaces resolve to `#111827`;
- card/input surfaces resolve to `#1A2235`;
- gold and teal accents match the canonical values;
- body text remains neutral and readable;
- scrollbar uses `#2A3A54`.

- [ ] **Step 4: Inspect a temporary PDC fixture**

Using browser DOM evaluation or a temporary in-memory fixture, render:

```html
<section class="pdc-canvas pdc-canvas-grain">
  <div class="pdc-annotation">Annotation PDC</div>
  <div class="pdc-source-lims">LIMS</div>
  <div class="pdc-source-calculated">Calculé</div>
  <div class="pdc-source-manual">Manuel</div>
  <div class="pdc-source-project">Projet</div>
  <div class="pdc-source-design">Design</div>
  <div class="pdc-source-default">Défaut</div>
</section>
```

Verify the fixture uses the light paper palette and Caveat while adjacent application UI remains dark. Remove the fixture after inspection.

- [ ] **Step 5: Deploy the frontend to Railway**

Run from the repository root with one stable Railway telemetry session:

```bash
RAILWAY_CALLER=skill:use-railway@1.2.0 \
RAILWAY_AGENT_SESSION=railway-skill-20260614-theme-palette \
railway up frontend --path-as-root \
  --service metalflow-frontend \
  --environment production \
  --ci \
  -m "Apply canonical MetalFlow dark and PDC palette"
```

Expected: `Deploy complete`.

- [ ] **Step 6: Verify the public release**

Run:

```bash
curl -fsS https://metalflow-frontend-production.up.railway.app |
python3 -c "import sys; s=sys.stdin.read(); required=['#0B0F1A','#111827','#1A2235','#222D42','#F59E0B','#2DD4BF','.pdc-canvas{','.pdc-canvas-grain']; missing=[x for x in required if x not in s]; print('missing=',missing); raise SystemExit(bool(missing))"
```

Then run:

```bash
RAILWAY_CALLER=skill:use-railway@1.2.0 \
RAILWAY_AGENT_SESSION=railway-skill-20260614-theme-palette \
railway deployment list --service metalflow-frontend --limit 3 --json
```

Expected: `missing=[]` and the newest deployment status is `SUCCESS`.

- [ ] **Step 7: Review runtime logs**

Run:

```bash
RAILWAY_CALLER=skill:use-railway@1.2.0 \
RAILWAY_AGENT_SESSION=railway-skill-20260614-theme-palette \
railway logs --service metalflow-frontend --lines 80 --json
```

Expected: the static server accepts connections and returns HTTP `200` without startup errors.
