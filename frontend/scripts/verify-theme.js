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
  "@fontsource-variable/inter",
  "@fontsource/ibm-plex-mono",
  "@fontsource/caveat",
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
