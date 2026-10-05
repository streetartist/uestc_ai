import assert from "node:assert/strict";
import { access, readFile } from "node:fs/promises";
import test from "node:test";

async function render() {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("test", String(process.pid) + "-" + String(Date.now()));
  const { default: worker } = await import(workerUrl.href);
  if (typeof worker === "function") {
    return worker(new Request("http://localhost/", { headers: { accept: "text/html" } }));
  }
  return worker.fetch(
    new Request("http://localhost/", { headers: { accept: "text/html" } }),
    { ASSETS: { fetch: async () => new Response("Not found", { status: 404 }) } },
    { waitUntil() {}, passThroughOnException() {} },
  );
}

test("server-renders the UESTC AI community homepage", async () => {
  const response = await render();
  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-type") ?? "", /^text\/html\b/i);
  const html = await response.text();
  assert.match(html, /<title>.*UESTC AI.*<\/title>/i);
  assert.match(html, /2026/);
  assert.equal((html.match(/class="kinetic-type-backdrop"/g) ?? []).length, 1);
  assert.match(html, /class="kinetic-type-backdrop__canvas"/);
  assert.match(html, /class="home-updates"/);
  assert.match(html, /正在读取动态/);
  assert.equal((html.match(/class="manifesto-card"/g) ?? []).length, 3);
  assert.match(html, /最新动态/);
  assert.match(html, /href="\/about"/);
  assert.doesNotMatch(html, /class="home-section home-about"/);
  assert.match(html, /class="app-footer app-footer-rich"/);
  assert.match(html, /COMPETE · PUBLISH · BUILD/);
  assert.doesNotMatch(html, /home-hero|throwing-card hero-season/);
  assert.doesNotMatch(html, /draggable-visit-card|VISIT SITE|grasshopper-backdrop|digital-rain-backdrop|fluid-field-backdrop|number-globe-backdrop|globe-backdrop/);
  assert.doesNotMatch(html, /SCROLL TO EXPAND/);
  assert.match(html, /class="scroll-film__prelude"/);
  assert.match(html, /UESTC AI/);
  assert.doesNotMatch(html, /class="home-intro"/);
});

test("starter preview infrastructure is removed", async () => {
  const page = await readFile(new URL("../app/page.tsx", import.meta.url), "utf8");
  const layout = await readFile(new URL("../app/layout.tsx", import.meta.url), "utf8");
  assert.doesNotMatch(page, /_sites-preview|SkeletonPreview|codex-preview/);
  assert.doesNotMatch(layout, /Starter Project|codex-preview|_sites-preview/);
  await assert.rejects(access(new URL("../app/_sites-preview", import.meta.url)));
});

test("updates extend the featured rail as the sidebar gains cards", async () => {
  const page = await readFile(new URL("../app/page.tsx", import.meta.url), "utf8");
  const sectionCss = await readFile(new URL("../app/reference-video-sections.css", import.meta.url), "utf8");
  assert.match(page, /className="news-cards news-showcase"/);
  assert.match(page, /className="updates-featured-rail"/);
  assert.match(page, /className="updates-sidebar"/);
  assert.match(page, /homeUpdatesLimit = 6/);
  assert.match(page, /articles\.slice\(1\)/);
  assert.match(page, /lastImageBottom - railTop \+ captionHeight/);
  assert.match(page, /resizeObserver\.observe\(sidebar\)/);
  assert.match(page, /desktop\.addEventListener\("change", alignImageEnds\)/);
  assert.match(sectionCss, /\.news-showcase \.updates-featured-rail \{\s*align-self: stretch/);
  assert.match(sectionCss, /\.news-showcase \.update-featured \{\s*position: relative/);
  assert.match(sectionCss, /\.news-showcase:has\(\.updates-sidebar \.update-small:nth-child\(2\)\) \.updates-featured-rail > \.update-featured \{\s*position: sticky;\s*top: 112px/);
  assert.match(sectionCss, /\.updates-sidebar \.update-small \{\s*display: grid;\s*grid-template-columns: 90px/);
  assert.doesNotMatch(page, /UpdatesShowcase|translate3d/);
  assert.doesNotMatch(page, /NewsCardParticles/);
  assert.doesNotMatch(sectionCss, /update-card-particles/);
  await assert.rejects(access(new URL("../app/components/NewsCardParticles.tsx", import.meta.url)));
});

test("mobile platform and flagship cards swipe one at a time", async () => {
  const page = await readFile(new URL("../app/page.tsx", import.meta.url), "utf8");
  const css = await readFile(new URL("../app/reference-video-sections.css", import.meta.url), "utf8");
  const mobile = css.split("/* One full card per swipe on phones;")[1];
  assert.ok(mobile);
  assert.match(page, /className="manifesto-cards"/);
  assert.match(page, /左右滑动查看赛道/);
  assert.match(css, /\.manifesto-cards \{ display: contents; \}/);
  assert.match(mobile, /scroll-snap-type: x mandatory/);
  assert.equal((mobile.match(/flex: 0 0 100%/g) ?? []).length, 2);
  assert.equal((mobile.match(/scroll-snap-align: start/g) ?? []).length, 2);
});

test("about content lives on its own linked page", async () => {
  const home = await readFile(new URL("../app/page.tsx", import.meta.url), "utf8");
  const about = await readFile(new URL("../app/about/page.tsx", import.meta.url), "utf8");
  const shell = await readFile(new URL("../app/components/AppShell.tsx", import.meta.url), "utf8");
  assert.doesNotMatch(home, /home-about|从浩瀚数据中提炼世界规律/);
  assert.match(about, /title="关于我们"/);
  assert.match(about, /从浩瀚数据中提炼世界规律/);
  assert.match(about, /我们相信智能的核心是同理心/);
  assert.match(shell, /href: "\/about", label: "关于我们"/);
  assert.match(shell, /<Link href="\/about">关于我们<\/Link>/);
});

test("public detail pages expose desktop actions and mobile reading styles", async () => {
  const shell = await readFile(new URL("../app/components/AppShell.tsx", import.meta.url), "utf8");
  const layout = await readFile(new URL("../app/layout.tsx", import.meta.url), "utf8");
  const css = await readFile(new URL("../app/public-detail.css", import.meta.url), "utf8");
  const paths = [
    "../app/news/[slug]/page.tsx",
    "../app/works/[id]/page.tsx",
    "../app/competitions/[competitionSlug]/page.tsx",
    "../app/competitions/[competitionSlug]/tracks/[trackSlug]/page.tsx",
    "../app/competitions/[competitionSlug]/tracks/[trackSlug]/problems/[problemSlug]/page.tsx",
  ];
  for (const path of paths) {
    assert.match(await readFile(new URL(path, import.meta.url), "utf8"), /<AppShell variant="detail"/);
  }
  assert.match(shell, /className="page-title-actions"/);
  assert.match(layout, /import "\.\/public-detail\.css"/);
  assert.match(css, /\.public-detail-shell \.page-title-actions \{/);
  assert.match(css, /@media \(max-width: 820px\) \{[\s\S]*\.page-title-actions \{ display: none; \}/);
  assert.match(css, /\.public-detail-shell \.markdown-body p,[\s\S]*font-size: 15px/);
});

test("homepage film has no skip button or trailing scroll cue", async () => {
  const film = await readFile(new URL("../app/components/ScrollFilmIntro.tsx", import.meta.url), "utf8");
  const css = await readFile(new URL("../app/components/scroll-film-intro.css", import.meta.url), "utf8");
  assert.doesNotMatch(film, /进入首页 \/ 跳过|继续下滑，进入社区|SCROLL TO EXPAND|scrollIntoView/);
  assert.doesNotMatch(film, /scroll-film__prelude-action|scroll-film__scroll-cue/);
  assert.doesNotMatch(css, /scroll-film__prelude-action|scroll-film__scroll-cue/);
});

test("homepage film resynchronizes its visual layers after navigation", async () => {
  const film = await readFile(new URL("../app/components/ScrollFilmIntro.tsx", import.meta.url), "utf8");
  assert.match(film, /useLayoutEffect\(\(\) => \{/);
  assert.match(film, /bounds\.top < headerHeight && bounds\.bottom > headerHeight/);
  assert.match(film, /window\.scrollTo\(\{ top: 0, behavior: "instant" \}\)/);
  assert.match(film, /addEventListener\("pageshow", restoreEntrance\)/);
  assert.match(film, /addEventListener\("popstate", restoreEntrance\)/);
});

test("homepage follows the reference video sequence with kinetic type", async () => {
  const page = await readFile(new URL("../app/page.tsx", import.meta.url), "utf8");
  const component = await readFile(new URL("../app/components/KineticTypeBackdrop.tsx", import.meta.url), "utf8");
  const sectionCss = await readFile(new URL("../app/reference-video-sections.css", import.meta.url), "utf8");
  const backdropCss = await readFile(new URL("../app/components/kinetic-type-backdrop.css", import.meta.url), "utf8");
  const scrollFilm = await readFile(new URL("../app/components/ScrollFilmIntro.tsx", import.meta.url), "utf8");
  const scrollFilmCss = await readFile(new URL("../app/components/scroll-film-intro.css", import.meta.url), "utf8");
  const particleCanvas = await readFile(new URL("../app/components/ParticleTextCanvas.tsx", import.meta.url), "utf8");

  assert.doesNotMatch(page, /KineticTypeBackdrop/);
  assert.match(scrollFilm, /KineticTypeBackdrop/);
  assert.match(page, /赛事赛道/);
  assert.match(backdropCss, /kinetic-type-backdrop__mascot/);
  assert.match(component, /src="\/ai-mascot.webp"/);
  assert.match(particleCanvas, /<canvas/);
  assert.match(particleCanvas, /requestAnimationFrame/);
  assert.match(particleCanvas, /fillText/);
  assert.match(particleCanvas, /const WORD = "UESTC AI"/);
  assert.match(particleCanvas, /Math\.floor\(time \/ 170\)/);
  assert.doesNotMatch(particleCanvas, /IDEASPRACTICEBUILD/);
  assert.match(scrollFilmCss, /scroll-film__window \.kinetic-type-backdrop \{\s*z-index: 1/);
  assert.match(scrollFilm, /<h1[\s\S]*className="scroll-film__heading"/);
  assert.ok(scrollFilm.indexOf('className="scroll-film__heading"') < scrollFilm.indexOf('className="scroll-film__window"'));
  assert.doesNotMatch(scrollFilm, /className="scroll-film__content"/);
  assert.match(scrollFilmCss, /@keyframes scroll-film-type-drift/);
  assert.match(scrollFilmCss, /radial-gradient\(circle at 78% 18%/);
  assert.match(scrollFilmCss, /animation-direction: reverse/);
  assert.match(scrollFilmCss, /prefers-reduced-motion: reduce[\s\S]*scroll-film__type-ticker \{ animation: none/);
  assert.equal((scrollFilm.match(/className="scroll-film__type-ticker"/g) ?? []).length, 2);
  assert.match(backdropCss, /kinetic-type-backdrop__canvas \{[\s\S]*pointer-events: none/);
  assert.match(scrollFilm, /requestAnimationFrame|scroll-film__window/);
  assert.doesNotMatch(scrollFilm, /hasSeenFilm|sessionStorage|localStorage/);
  assert.ok(page.indexOf('className="home-updates"') < page.indexOf('aria-label="产品矩阵"'));
  assert.ok(page.indexOf('aria-label="产品矩阵"') < page.indexOf('赛事赛道'));
  assert.ok(page.indexOf('赛事赛道') < page.indexOf('split-section'));
  assert.match(sectionCss, /news-cards/);
  assert.match(sectionCss, /updates-fallback-feature/);
  assert.match(sectionCss, /manifesto-card/);
  assert.match(sectionCss, /One continuous editorial surface/);
  assert.match(sectionCss, /Subtle two-tone edges/);
  assert.match(sectionCss, /Borderless editorial card pass/);
  assert.match(sectionCss, /Reference screenshot grid and rhythm pass/);
  assert.match(sectionCss, /Exact composition from the supplied homepage reference/);
  assert.match(sectionCss, /Balanced homepage grids and a consistent reading rhythm/);
  assert.match(sectionCss, /grid-template-columns: minmax\(300px, 1\.25fr\) repeat\(3/);
  assert.match(sectionCss, /track-card:is\(\.track-coral, \.track-ink, \.track-sage, \.track-sand\)/);
  assert.match(page, /从想法，到<br \/>被看见。/);
  assert.match(sectionCss, /grid-template-columns: minmax\(330px, 1\.25fr\) repeat\(3/);
  assert.match(sectionCss, /grid-template-columns: repeat\(4, minmax\(0, 1fr\)\)/);
  assert.match(sectionCss, /Unified public typography scale/);
  assert.match(sectionCss, /Restored editorial display type/);
  assert.match(sectionCss, /Bodoni MT/);
  assert.match(sectionCss, /--home-section-title-size/);
  const theme = await readFile(new URL("../app/theme.css", import.meta.url), "utf8");
  assert.match(theme, /Frosted navigation surface/);
  assert.match(theme, /backdrop-filter: blur\(18px\)/);
  assert.match(sectionCss, /border: 0 !important/);
  assert.match(sectionCss, /--edge-outer/);
  assert.match(particleCanvas, /IntersectionObserver/);
  assert.match(particleCanvas, /context.drawImage\(field/);
  assert.match(sectionCss, /about-layout/);
  assert.doesNotMatch(page, /GlobeBackdrop|HeroNetwork|GrasshopperBackdrop|DigitalRainBackdrop|FluidFieldBackdrop|NumberGlobeBackdrop/);
  await assert.rejects(access(new URL("../app/components/HeroNetwork.tsx", import.meta.url)));
  await assert.rejects(access(new URL("../app/components/HeroNetworkScene.ts", import.meta.url)));
});
