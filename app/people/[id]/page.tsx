"use client";

import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { useState } from "react";
import { ArrowUpRight, CalendarDays, PenLine, ShieldCheck, Trophy } from "lucide-react";
import { AppShell } from "@/app/components/AppShell";
import { useSession } from "@/app/components/SessionProvider";
import { PasswordChangeForm } from "@/app/components/PasswordChangeForm";
import { CommunityCard, CommunityEmpty } from "@/app/components/CommunityContent";
import { PageError, PageLoading } from "@/app/components/ui";
import type { ContentItem, PublicWork } from "@/app/lib/domain";
import { useApiResource } from "@/app/lib/useApiResource";
import { formatBeijing } from "@/app/lib/time";

type Profile = { id: string; name: string; joined_at: string; competitions: { id: string; competition_name: string; competition_slug: string; track_name: string; team_name: string }[]; works: PublicWork[]; contributions: ContentItem[] };

export default function ProfilePage() {
  const { id } = useParams<{ id: string }>();
  const params = useSearchParams();
  const { user } = useSession();
  const { data, loading, error, reload } = useApiResource<Profile>(id ? `/profiles/${id}` : null);
  const [kind, setKind] = useState("all");
  const own = user?.id === id;
  const settings = own && params.get("tab") === "settings";
  const worksCount = data ? data.works.length + data.contributions.filter((item) => item.kind === "work").length : 0;
  const articlesCount = data?.contributions.filter((item) => item.kind !== "work").length ?? 0;
  const works = kind === "articles" ? [] : data?.works ?? [];
  const contributions = (data?.contributions ?? []).filter((item) => kind === "all" || (kind === "works" ? item.kind === "work" : item.kind !== "work"));

  return <AppShell title="个人主页" eyebrow="COMMUNITY / PEOPLE">
    {loading ? <PageLoading /> : error || !data ? <PageError message={error || "个人主页不存在。"} retry={reload} /> : <div className="community-page person-page">
      <header className="person-header">
        <span className="person-avatar" aria-hidden="true">{data.name.slice(0, 1)}</span>
        <div className="person-identity"><h2>{data.name}</h2><p><CalendarDays size={15} />{formatBeijing(data.joined_at, { year: "numeric", month: "long" })}加入</p></div>
        {own && <Link className="primary-button" href="/contribute"><PenLine size={17} />发表文章或作品</Link>}
        <dl className="person-stats"><div><dt>参赛履历</dt><dd>{data.competitions.length}</dd></div><div><dt>公开作品</dt><dd>{worksCount}</dd></div><div><dt>已发文章</dt><dd>{articlesCount}</dd></div></dl>
      </header>
      <nav className="community-tabs person-nav" aria-label="个人主页导航"><Link href={`/people/${id}`} aria-current={!settings ? "page" : undefined}>个人概览</Link>{own && <><Link href={`/people/${id}?tab=settings`} aria-current={settings ? "page" : undefined}>账户设置</Link><Link className="person-manage" href="/contribute">管理我的投稿<ArrowUpRight size={15} /></Link></>}</nav>
      {settings ? <div className="person-settings"><aside className="person-account"><ShieldCheck size={24} /><h2>账户与安全</h2><p>仅你自己可以查看和管理。</p><dl><dt>登录邮箱</dt><dd>{user.email}</dd></dl><p>忘记当前密码？可以通过邮箱验证重新设置。</p><Link className="text-button" href="/login?mode=reset">找回密码<ArrowUpRight size={15} /></Link></aside><PasswordChangeForm userId={id} /></div> : <div className="person-overview">
        <section className="person-content"><div className="community-section-heading"><h2>作品与文章</h2><span>已公开的创作与成果</span></div><div className="community-tabs community-subtabs" aria-label="筛选个人内容">{[{ value: "all", label: "全部", count: worksCount + articlesCount }, { value: "works", label: "作品", count: worksCount }, { value: "articles", label: "文章", count: articlesCount }].map((item) => <button type="button" key={item.value} aria-pressed={kind === item.value} onClick={() => setKind(item.value)}>{item.label}<span>{item.count}</span></button>)}</div>
          {works.length || contributions.length ? <div className="community-cards person-cards">{works.map((work) => <CommunityCard key={work.id} href={`/works/${work.id}`} title={work.title} category="参赛作品" context={work.track_name} description={`${work.competition_name} · ${work.team_name}`} />)}{contributions.map((item) => <CommunityCard key={item.id} href={`/news/${item.slug}`} title={item.title} category={item.kind === "work" ? "社区作品" : "文章"} description={item.excerpt} />)}</div> : <CommunityEmpty title={kind === "all" ? "创作，从这里开始" : kind === "works" ? "还没有公开作品" : "还没有公开文章"} description={own ? "参赛作品与审核通过的投稿，会收录在你的主页。" : "这里会展示已公开的作品与文章。"} href={own ? "/contribute" : undefined} action="分享第一份创作" />}
        </section>
        <aside className="person-history"><div className="community-section-heading"><h2><Trophy size={18} />参赛履历</h2><span>{data.competitions.length}</span></div>{data.competitions.length ? <ol>{data.competitions.map((entry) => <li key={entry.id}><Link href={`/competitions/${entry.competition_slug}`}><strong>{entry.competition_name}</strong><ArrowUpRight size={15} /></Link><p>{entry.track_name}</p><span>{entry.team_name}</span></li>)}</ol> : <div className="person-history-empty"><p>尚无参赛履历</p><span>完成赛事报名后，将在这里留下足迹。</span>{own && <Link className="text-button" href="/competitions">探索赛事<ArrowUpRight size={15} /></Link>}</div>}</aside>
      </div>}
    </div>}
  </AppShell>;
}
