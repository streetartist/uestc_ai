"use client";

import Link from "next/link";
import { useState } from "react";
import { PenLine, Search } from "lucide-react";
import { AppShell } from "@/app/components/AppShell";
import { CommunityCard, CommunityEmpty } from "@/app/components/CommunityContent";
import { UserLink } from "@/app/components/UserLink";
import { PageError, PageLoading } from "@/app/components/ui";
import type { ContentItem } from "@/app/lib/domain";
import { formatBeijing } from "@/app/lib/time";
import { useApiResource } from "@/app/lib/useApiResource";

const filters = [{ value: "all", label: "全部" }, { value: "announcement", label: "公告" }, { value: "blog", label: "文章" }, { value: "research", label: "题目调研" }];

export default function NewsPage() {
  const { data, loading, error, reload } = useApiResource<ContentItem[]>("/content");
  const [kind, setKind] = useState("all");
  const [query, setQuery] = useState("");
  const needle = query.trim().toLowerCase();
  const items = (data ?? []).filter((item) => item.kind !== "work" && (kind === "all" || item.kind === kind) && `${item.title} ${item.excerpt} ${item.author ?? ""}`.toLowerCase().includes(needle));
  return <AppShell title="资讯" eyebrow="COMMUNITY / JOURNAL"><div className="community-page">
    <header className="community-intro"><div><h2>记录探索，分享所见</h2><p>赛事公告、技术文章与赛题调研，发现社团正在发生的事。</p></div><Link className="primary-button" href="/contribute"><PenLine size={17} />发表文章</Link></header>
    <div className="community-toolbar"><div className="community-tabs" aria-label="资讯分类">{filters.map((item) => <button type="button" aria-pressed={kind === item.value} key={item.value} onClick={() => setKind(item.value)}>{item.label}</button>)}</div><label className="community-search"><Search size={17} /><span className="sr-only">搜索资讯</span><input type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索标题、内容或作者" /></label></div>
    <div className="community-results"><span>{loading ? "正在读取内容" : `共 ${items.length} 篇内容`}</span><span>观点与经验，值得被分享。</span></div>
    {loading ? <PageLoading /> : error ? <PageError message={error} retry={reload} /> : !items.length ? <CommunityEmpty title={needle ? "没有找到匹配内容" : "这里期待新的分享"} description={needle ? "换个关键词再试试。" : "分享一次实践、一份研究，或一个值得讨论的想法。"} href={needle ? undefined : "/contribute"} action="发表文章" /> : <div className="community-cards">{items.map((item) => <CommunityCard key={item.id} href={`/news/${item.slug}`} title={item.title} category={filters.find((filter) => filter.value === item.kind)?.label ?? "文章"} description={item.excerpt} author={<UserLink id={item.author_id} name={item.author ?? "UESTC AI 社"} />} date={item.published_at ? formatBeijing(item.published_at, { year: "numeric", month: "2-digit", day: "2-digit" }) : undefined} />)}</div>}
  </div></AppShell>;
}
