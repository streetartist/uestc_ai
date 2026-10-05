"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { Plus, Search } from "lucide-react";
import { AppShell } from "@/app/components/AppShell";
import { CommunityCard, CommunityEmpty } from "@/app/components/CommunityContent";
import { PageError, PageLoading } from "@/app/components/ui";
import { UserLink, UserLinks } from "@/app/components/UserLink";
import type { ContentItem, PublicWork } from "@/app/lib/domain";
import { formatBeijing } from "@/app/lib/time";
import { useApiResource } from "@/app/lib/useApiResource";

function displayDate(value: string | null) { return value ? formatBeijing(value, { year: "numeric", month: "2-digit", day: "2-digit" }) : ""; }

export default function WorksPage() {
  const competition = useApiResource<PublicWork[]>("/works");
  const community = useApiResource<ContentItem[]>("/content?kind=work");
  const [query, setQuery] = useState("");
  const [track, setTrack] = useState("all");
  const [kind, setKind] = useState("all");
  const tracks = useMemo(() => Array.from(new Map((competition.data ?? []).map((item) => [item.track_slug, item.track_name])).entries()), [competition.data]);
  const needle = query.trim().toLowerCase();
  const works = kind === "community" ? [] : (competition.data ?? []).filter((item) => (track === "all" || item.track_slug === track) && `${item.title} ${item.team_name} ${item.problem_code} ${item.track_name} ${item.competition_name} ${(item.team_members ?? []).map((member) => member.name).join(" ")}`.toLowerCase().includes(needle));
  const independentWorks = kind === "competition" || track !== "all" ? [] : (community.data ?? []).filter((item) => `${item.title} ${item.excerpt} ${item.author ?? ""}`.toLowerCase().includes(needle));
  const loading = (kind !== "community" && competition.loading) || (kind !== "competition" && community.loading);
  const error = (kind !== "community" && competition.error) || (kind !== "competition" && community.error);
  const count = works.length + independentWorks.length;
  const filtered = Boolean(needle || track !== "all");

  return <AppShell title="作品档案" eyebrow="COMMUNITY / WORKS"><div className="community-page">
    <header className="community-intro"><div><h2>让想法成为作品</h2><p>发现参赛成果，也分享比赛之外的项目、工具与创作。</p></div><Link className="primary-button" href="/contribute?kind=work"><Plus size={18} />分享作品</Link></header>
    <div className="community-toolbar"><div className="community-tabs" aria-label="作品分类">{[{ value: "all", label: "全部作品" }, { value: "competition", label: "参赛作品" }, { value: "community", label: "社区作品" }].map((item) => <button type="button" key={item.value} aria-pressed={kind === item.value} onClick={() => { setKind(item.value); setTrack("all"); }}>{item.label}</button>)}</div><div className="community-filters"><label className="community-search"><Search size={17} /><span className="sr-only">搜索作品</span><input type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索作品、队伍或作者" /></label>{kind !== "community" && tracks.length > 0 && <label><span className="sr-only">筛选赛道</span><select value={track} onChange={(event) => setTrack(event.target.value)}><option value="all">全部赛道</option>{tracks.map(([slug, name]) => <option value={slug} key={slug}>{name}</option>)}</select></label>}</div></div>
    <div className="community-results"><span>{loading ? "正在读取作品" : `共 ${count} 件作品`}</span><span>{kind === "competition" ? "正式提交的作品公开展示，成绩另行发布。" : kind === "community" ? "社区投稿审核通过后公开。" : "参赛成果与社区创作，汇聚于此。"}</span></div>
    {loading ? <PageLoading /> : error ? <PageError message={error} retry={() => { competition.reload(); community.reload(); }} /> : !count ? <CommunityEmpty title={filtered ? "没有找到匹配作品" : "第一份作品，等你来分享"} description={filtered ? "试试其他关键词或赛道。" : "参赛作品正式提交后展示，独立作品审核通过后收录。"} href={filtered ? undefined : "/contribute?kind=work"} action="分享作品" /> : <div className="community-cards">{works.map((work) => <CommunityCard key={work.id} href={`/works/${work.id}`} title={work.title} category="参赛作品" context={work.track_name} description={`${work.competition_name} · ${work.team_name} · ${work.problem_code}`} author={<UserLinks users={work.team_members ?? []} />} date={displayDate(work.version.created_at)} />)}{independentWorks.map((item) => <CommunityCard key={item.id} href={`/news/${item.slug}`} title={item.title} category="社区作品" description={item.excerpt} author={<UserLink id={item.author_id} name={item.author ?? "UESTC AI 社"} />} date={displayDate(item.published_at)} />)}</div>}
  </div></AppShell>;
}
