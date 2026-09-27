"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ArrowRight, BookOpen, CalendarDays, Code2 } from "lucide-react";
import { AppShell } from "@/app/components/AppShell";
import { RevealSection } from "@/app/components/RevealSection";
import { ScrollFilmIntro } from "@/app/components/ScrollFilmIntro";
import { EmptyState, PageError, PageLoading, StatusPill } from "@/app/components/ui";
import { api, formatApiError } from "@/app/lib/api";
import type { Competition, ContentItem, Track } from "@/app/lib/domain";
import { formatBeijing } from "@/app/lib/time";

const accents = ["coral", "ink", "sage", "sand"];
const homeUpdatesLimit = 6;

function trackAccent(track: Track, index: number) {
  const value = track.config?.accent;
  return value && accents.includes(value) ? value : accents[index % accents.length];
}

function shortDate(value?: string | null) {
  if (!value) return "待公布";
  return formatBeijing(value, { month: "2-digit", day: "2-digit" });
}

function UpdatesFallback({ error, retry }: { error: string; retry: () => void }) {
  return <div className="updates-fallback">
    <div className="updates-fallback-feature" role={error ? "alert" : undefined}>
      <span className="updates-fallback-kicker">COMMUNITY / NEXT</span>
      <h3>{error ? "动态暂时无法载入。" : "下一条灵感，正在路上。"}</h3>
      <p>{error || "公告、技术笔记与创作故事会在这里出现。现在也可以先探索社区的其他入口。"}</p>
      {error ? <button type="button" onClick={retry}>重新载入 <ArrowRight size={16} /></button>
        : <Link href="/news">进入资讯档案 <ArrowRight size={16} /></Link>}
    </div>
    <Link className="updates-fallback-link" href="/competitions"><span>01 / COMPETE</span><strong>探索赛事</strong><ArrowRight size={18} /></Link>
    <Link className="updates-fallback-link" href="/works"><span>02 / BUILD</span><strong>浏览作品</strong><ArrowRight size={18} /></Link>
  </div>;
}
export default function Home() {
  const [competition, setCompetition] = useState<Competition | null>(null);
  const [articles, setArticles] = useState<ContentItem[]>([]);
  const [articleError, setArticleError] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const updatesShowcaseRef = useRef<HTMLDivElement>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    setArticleError("");
    try {
      const competitions = await api<Competition[]>("/competitions?status=published");
      const featured = competitions[0] ?? null;
      const [detailResult, contentResult] = await Promise.allSettled([
        featured ? api<Competition>(`/competitions/${featured.slug}`) : Promise.resolve(null),
        api<ContentItem[]>("/content"),
      ]);
      if (detailResult.status === "fulfilled") setCompetition(detailResult.value);
      else setError(formatApiError(detailResult.reason));
      if (contentResult.status === "fulfilled") setArticles(contentResult.value.slice(0, homeUpdatesLimit));
      else setArticleError(formatApiError(contentResult.reason));
    } catch (requestError) {
      setError(formatApiError(requestError));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    let active = true;
    Promise.resolve().then(() => { if (active) return load(); });
    return () => { active = false; };
  }, [load]);

  useEffect(() => {
    const showcase = updatesShowcaseRef.current;
    if (!showcase || articles.length < 3) return;

    const rail = showcase.querySelector<HTMLElement>(".updates-featured-rail");
    const featured = rail?.querySelector<HTMLElement>(".update-featured");
    const featuredInfo = featured?.querySelector<HTMLElement>(".update-featured-info");
    const featuredVisual = featured?.querySelector<HTMLElement>(".update-featured-visual");
    const sidebar = showcase.querySelector<HTMLElement>(".updates-sidebar");
    const lastVisual = sidebar?.querySelector<HTMLElement>(".update-small:last-child .update-small-visual");
    if (!rail || !featured || !featuredInfo || !featuredVisual || !sidebar || !lastVisual) return;

    const desktop = window.matchMedia("(min-width: 1101px)");
    const alignImageEnds = () => {
      if (!desktop.matches) {
        rail.style.removeProperty("height");
        return;
      }
      const railTop = rail.getBoundingClientRect().top;
      const lastImageBottom = lastVisual.getBoundingClientRect().bottom;
      const captionHeight = featuredInfo.getBoundingClientRect().height;
      const railHeight = Math.max(featured.getBoundingClientRect().height, lastImageBottom - railTop + captionHeight);
      rail.style.height = `${railHeight}px`;
    };

    const resizeObserver = new ResizeObserver(alignImageEnds);
    resizeObserver.observe(sidebar);
    resizeObserver.observe(featuredInfo);
    resizeObserver.observe(featuredVisual);
    desktop.addEventListener("change", alignImageEnds);
    alignImageEnds();
    return () => {
      resizeObserver.disconnect();
      desktop.removeEventListener("change", alignImageEnds);
      rail.style.removeProperty("height");
    };
  }, [articles]);

  const featuredProblems = useMemo(
    () => competition?.tracks?.flatMap((track) => (track.problems ?? []).map((problem) => ({ problem, track }))).slice(0, 5) ?? [],
    [competition],
  );

  return (
    <AppShell contained={false}>
      <ScrollFilmIntro />

      <RevealSection className="home-updates" aria-label="最新动态" reveal="slide">
        <header className="section-header">
          <div><span className="eyebrow"><i />LATEST</span><h2>最新动态</h2></div>
          <Link href="/news">全部动态 <ArrowRight size={15} /></Link>
        </header>
        {loading ? <PageLoading label="正在读取动态" /> : articles.length ? <div className="news-cards news-showcase" ref={updatesShowcaseRef}>
          <div className="updates-featured-rail">
            <Link data-reveal-item className="update-card update-featured" data-kind={articles[0].kind} href={`/news/${articles[0].slug}`}>
              <div className="update-visual update-featured-visual" aria-hidden="true"><span>UESTC AI</span><i /></div>
              <div className="update-featured-info">
                <div className="update-card-top"><time>{articles[0].published_at ? shortDate(articles[0].published_at) : "草稿"}</time><span>{articles[0].kind}</span></div>
                <strong>{articles[0].title}</strong>
                <p>{articles[0].excerpt || "进入资讯档案查看完整内容。"}</p>
                <ArrowRight size={18} />
              </div>
            </Link>
          </div>
          {articles.length > 1 && <div className="updates-sidebar">
            {articles.slice(1).map((article, index) => (
              <Link data-reveal-item className="update-card update-small" data-kind={article.kind} href={`/news/${article.slug}`} key={article.id}>
                <div className="update-visual update-small-visual" aria-hidden="true"><span>{String(index + 2).padStart(2, "0")}</span><i /></div>
                <div className="update-small-info">
                  <div className="update-card-top"><time>{article.published_at ? shortDate(article.published_at) : "草稿"}</time><span>{article.kind}</span></div>
                  <strong>{article.title}</strong>
                  <p>{article.excerpt || "进入资讯档案查看完整内容。"}</p>
                  <ArrowRight size={17} />
                </div>
              </Link>
            ))}
          </div>}
        </div> : <UpdatesFallback error={articleError} retry={() => void load()} />}
      </RevealSection>

      <RevealSection className="home-manifesto" aria-label="产品矩阵">
        <div className="manifesto-intro"><span>01 / PLATFORM</span><h2>从想法，到<br />被看见。</h2><p>选择一个入口，开始下一次探索。</p><span className="mobile-swipe-hint" aria-hidden="true">左右滑动查看 3 个入口 <ArrowRight size={14} /></span></div>
        <div className="manifesto-cards" role="group" aria-label="产品入口">
          <Link data-reveal-item className="manifesto-card" href="/competitions"><span>01 / COMPETE</span><strong>真实挑战</strong><p>进入赛题，在约束中验证想法。</p><ArrowRight size={19} /></Link>
          <Link data-reveal-item className="manifesto-card" href="/news"><span>02 / PUBLISH</span><strong>公开方法</strong><p>记录过程，让知识持续流动。</p><ArrowRight size={19} /></Link>
          <Link data-reveal-item className="manifesto-card" href="/works"><span>03 / BUILD</span><strong>共同创造</strong><p>找到同行者，把原型推进为作品。</p><ArrowRight size={19} /></Link>
        </div>
      </RevealSection>

      <div className="home-sections" id="discover">
        {loading ? <PageLoading label="正在读取赛事" /> : error ? (
          <RevealSection className="home-section flagship-section">
            <header className="section-header"><div><span className="eyebrow"><i />FLAGSHIP</span><h2>旗舰系列</h2></div></header>
            <PageError message={error} retry={load} />
          </RevealSection>
        ) : competition ? (
          <RevealSection className="home-section flagship-section">
            <header className="section-header">
              <div><span className="eyebrow"><i />FLAGSHIP</span><h2>旗舰系列</h2><span className="mobile-swipe-hint" aria-hidden="true">左右滑动查看赛道 <ArrowRight size={14} /></span></div>
              <Link href={`/competitions/${competition.slug}`}>完整赛事说明 <ArrowRight size={15} /></Link>
            </header>
            <div className="track-grid">
              {(competition.tracks ?? []).map((track, index) => (
                <Link data-reveal-item className={`track-card track-${trackAccent(track, index)}`} key={track.id} href={`/competitions/${competition.slug}/tracks/${track.slug}`}>
                  <div className="track-card-top"><span>{String(index + 1).padStart(2, "0")}</span><Code2 size={18} strokeWidth={1.5} /></div>
                  <div><small>{track.config?.short ?? "TRACK"}</small><h3>{track.name}</h3><p>{track.description}</p></div>
                  <footer><span>{track.problems?.length ?? 0} 个题目</span><ArrowRight size={16} /></footer>
                </Link>
              ))}
            </div>
          </RevealSection>
        ) : <EmptyState title="暂无公开赛事" description="赛事发布后会在这里显示。" />}

        <RevealSection className="home-section split-section">
          <div className="problem-feature">
            <header className="section-header compact-header">
              <div><span className="eyebrow"><i />PROBLEM LIBRARY</span><h2>本期题目</h2></div>
              <Link href="/problems"><BookOpen size={15} />浏览全部</Link>
            </header>
            {featuredProblems.length ? <div className="line-list">
              {featuredProblems.map(({ problem, track }) => (
                <Link data-reveal-item className="problem-line" key={problem.id} href={`/competitions/${competition!.slug}/tracks/${track.slug}/problems/${problem.slug}`}>
                  <span className="mono-label">{problem.code}</span>
                  <div><strong>{problem.title}</strong><span>{track.name} · 难度 {problem.difficulty}/5</span></div>
                  <StatusPill tone={problem.status === "published" ? "live" : "warm"}>{problem.status === "published" ? "正式赛题" : "候选"}</StatusPill>
                  <ArrowRight size={15} />
                </Link>
              ))}
            </div> : <EmptyState title="题库正在整理" description="公开题目会按赛道显示。" />}
          </div>
          {competition && <aside className="deadline-block">
            <CalendarDays size={20} />
            <span>关键日期</span>
            <strong>{shortDate(competition?.registration_closes_at)}</strong>
            <p>报名与组队截止</p>
            <hr />
            <strong>{shortDate(competition?.ends_at)}</strong>
            <p>赛事结束</p>
          </aside>}
        </RevealSection>

      </div>
    </AppShell>
  );
}
