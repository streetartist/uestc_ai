import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import test from "node:test";
import vm from "node:vm";
import { createElement } from "react";
import ts from "typescript";
import { renderToStaticMarkup } from "react-dom/server";
const source = await readFile(new URL("../app/components/FooterCodeDivider.tsx", import.meta.url), "utf8");
const record = { exports: {} };
vm.runInNewContext(ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2017, jsx: ts.JsxEmit.ReactJSX } }).outputText, { exports: record.exports, require: createRequire(import.meta.url) });
const { FOOTER_DIVIDER_CODE, FOOTER_DIVIDER_CODE_MOBILE, FooterCodeDivider, isMobileUserAgent } = record.exports;

test("the decorative divider preserves the exact ten-group code", () => {
  assert.equal(FOOTER_DIVIDER_CODE, "-....--..-...- -....--..-...- -....--..-..-- -....--..-..-- -....--..-.... -....--..-..-. -....--..-.... -....--..-..-. -... .-");
  const html = renderToStaticMarkup(createElement(FooterCodeDivider));
  assert.equal(html.replace(/<[^>]*>/g, ""), FOOTER_DIVIDER_CODE);
  assert.equal((html.match(/<span/g) ?? []).length, 10);
  assert.match(html, /aria-hidden="true"/);
  assert.doesNotMatch(html, /<button|<a\b|tabindex|title=/i);
});
test("mobile devices receive the exact nine-group code", () => {
  assert.equal(FOOTER_DIVIDER_CODE_MOBILE, "-..----.------- ---.-.-..-.-... --.-....-..--.. -..-.---.--...-. ----.-.---.---- --...-..-.-..-- -.-----........ -------.-.-...- -..--....---.-.-");
  assert.equal(FOOTER_DIVIDER_CODE_MOBILE.split(" ").length, 9);
  assert.equal(isMobileUserAgent("Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) Mobile/15E148"), true);
  assert.equal(isMobileUserAgent("Mozilla/5.0 (Linux; Android 15; Pixel 9) Mobile Safari/537.36"), true);
  assert.equal(isMobileUserAgent("Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/140.0.0.0 Safari/537.36"), false);
});
test("the code replaces the copyright row border without restoring an entry button", async () => {
  const shell = await readFile(new URL("../app/components/AppShell.tsx", import.meta.url), "utf8");
  assert.match(shell, /className="footer-bottom">\s*<FooterCodeDivider \/>/);
  const css = await readFile(new URL("../app/reference-video-sections.css", import.meta.url), "utf8");
  assert.match(css, /\.footer-bottom\s*\{[^}]*border-top:\s*0;/);
  const entrance = await readFile(new URL("../app/components/EasterEggEntrance.tsx", import.meta.url), "utf8");
  assert.ok(!entrance.includes("egg-mark"));
});
