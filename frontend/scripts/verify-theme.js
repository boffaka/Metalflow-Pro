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

function escapeRegex(value) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function assertDeclaration(source, name, value, message) {
  const pattern = new RegExp(
    `${escapeRegex(name)}\\s*:\\s*["']?${escapeRegex(value)}["']?\\s*[,;}]`,
    "i",
  );
  assert.match(source, pattern, message || `missing ${name}: ${value}`);
}

function assertRule(source, selector, declaration, message) {
  const declarationSource =
    declaration instanceof RegExp ? declaration.source : declaration;
  const pattern = new RegExp(
    `${escapeRegex(selector)}\\s*\\{[^}]*${declarationSource}[^}]*\\}`,
    "i",
  );
  assert.match(source, pattern, message);
}

const goldBlock = tailwind.match(/gold\s*:\s*\{[^}]*\}/i)?.[0] || "";
for (const [name, value] of Object.entries({
  "400": "#FCD34D",
  "500": "#F59E0B",
  "600": "#D97706",
})) {
  assertDeclaration(goldBlock, name, value, `missing Tailwind gold-${name}: ${value}`);
}

const tealBlock = tailwind.match(/teal\s*:\s*\{[^}]*\}/i)?.[0] || "";
for (const [name, value] of Object.entries({
  "400": "#2DD4BF",
  "500": "#0D9488",
  "600": "#0F766E",
})) {
  assertDeclaration(tealBlock, name, value, `missing Tailwind teal-${name}: ${value}`);
}

const surfaceBlock = tailwind.match(/surface\s*:\s*\{[^}]*\}/i)?.[0] || "";
for (const [name, value] of Object.entries({
  "900": "#0B0F1A",
  "800": "#111827",
  "700": "#1A2235",
  "600": "#222D42",
})) {
  assertDeclaration(surfaceBlock, name, value, `missing Tailwind surface-${name}: ${value}`);
}

const pdcBlock = tailwind.match(/pdc\s*:\s*\{[^}]*\}/i)?.[0] || "";
for (const [name, value] of Object.entries({
  canvas: "#F5EFE2",
  ink: "#1A2330",
  "ink-soft": "#5D6878",
  gold: "#C9A24A",
  "gold-hot": "#FFD063",
  alert: "#C24545",
})) {
  assertDeclaration(pdcBlock, name, value, `missing Tailwind pdc-${name}: ${value}`);
}

for (const [name, value] of Object.entries({
  "--bg": "#0B0F1A",
  "--bg1": "#0B0F1A",
  "--bg2": "#111827",
  "--bg3": "#1A2235",
  "--bg4": "#222D42",
  "--bg5": "#2A3A54",
  "--card": "#111827",
  "--surface": "#1A2235",
  "--gold": "#F59E0B",
  "--gold2": "#FCD34D",
  "--teal": "#0D9488",
  "--teal2": "#2DD4BF",
})) {
  assertDeclaration(html, name, value, `missing HTML token ${name}: ${value}`);
}

const pdcTokens = {
  "--pdc-canvas": "#F5EFE2",
  "--pdc-ink": "#1A2330",
  "--pdc-ink-soft": "#5D6878",
  "--pdc-gold": "#C9A24A",
  "--pdc-gold-hot": "#FFD063",
  "--pdc-alert": "#C24545",
};

for (const [name, value] of Object.entries(pdcTokens)) {
  assertDeclaration(html, name, value, `missing HTML PDC token ${name}: ${value}`);
  assertDeclaration(css, name, value, `missing CSS PDC token ${name}: ${value}`);
}

for (const fontImport of [
  "@fontsource-variable/inter",
  "@fontsource/ibm-plex-mono",
  "@fontsource/caveat",
]) {
  assert.ok(css.includes(fontImport), `missing ${fontImport}`);
}

assert.ok(index.includes('classList.add("dark")'), "dark class is not applied by default");
assert.ok(allStyles.includes("color-scheme:dark") || allStyles.includes("color-scheme: dark"));
assertDeclaration(html, "--border2", "#2A3A54", "HTML scrollbar token is not canonical");
assert.match(css, /scrollbar-color\s*:\s*#2A3A54\s+transparent/i);
assertRule(html, ".pdc-canvas", /color-scheme\s*:\s*light/i, "HTML PDC scope is missing");
assertRule(css, ".pdc-canvas", /color-scheme\s*:\s*light/i, "CSS PDC scope is missing");

const pdcSources = {
  ".pdc-canvas .pdc-source-lims": [63, 111, 168, "0?\\.12"],
  ".pdc-canvas .pdc-source-calculated": [126, 91, 168, "0?\\.12"],
  ".pdc-canvas .pdc-source-manual": [184, 106, 42, "0?\\.10"],
  ".pdc-canvas .pdc-source-project": [181, 138, 46, "0?\\.12"],
  ".pdc-canvas .pdc-source-design": [140, 124, 104, "0?\\.08"],
  ".pdc-canvas .pdc-source-default": [168, 154, 130, "0?\\.06"],
};

for (const [selector, [red, green, blue, alpha]] of Object.entries(pdcSources)) {
  const background = new RegExp(
    `background\\s*:\\s*rgba\\(\\s*${red}\\s*,\\s*${green}\\s*,\\s*${blue}\\s*,\\s*${alpha}\\s*\\)`,
    "i",
  );
  assertRule(html, selector, background, `missing HTML ${selector} background`);
  assertRule(css, selector, background, `missing CSS ${selector} background`);
}

assertRule(
  html,
  ".pdc-canvas .pdc-annotation",
  /font-family\s*:\s*Caveat(?:\s*,\s*cursive)?/i,
  "HTML Caveat font is not scoped to PDC annotations",
);
assertRule(
  css,
  ".pdc-canvas .pdc-annotation",
  /font-family\s*:\s*Caveat(?:\s*,\s*cursive)?/i,
  "CSS Caveat font is not scoped to PDC annotations",
);
assertRule(
  html,
  ".pdc-canvas-grain::after",
  /pointer-events\s*:\s*none/i,
  "HTML grain overlay must not capture input",
);
assert.equal(html, standalone, "standalone HTML is not synchronized");

console.log("MetalFlow theme contract: PASS");
