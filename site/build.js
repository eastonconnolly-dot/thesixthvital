#!/usr/bin/env node
// Copies the single-source-of-truth brand assets from shared/ into site/ so
// the static site is fully self-contained when deployed to GitHub Pages
// (which serves only this directory — it can't reach ../shared/). Run this
// after any change to shared/brand/*, and as part of your deploy step.
// No dependencies; plain Node.

const fs = require("fs");
const path = require("path");

const ROOT = path.join(__dirname, "..");
const COPIES = [
  [path.join(ROOT, "shared/brand/tokens.css"), path.join(__dirname, "css/tokens.css")],
  [path.join(ROOT, "shared/brand/badge.svg"), path.join(__dirname, "assets/badge.svg")],
];

for (const [src, dest] of COPIES) {
  fs.mkdirSync(path.dirname(dest), { recursive: true });
  fs.copyFileSync(src, dest);
  console.log(`copied ${path.relative(ROOT, src)} -> ${path.relative(ROOT, dest)}`);
}
