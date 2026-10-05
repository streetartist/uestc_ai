"use client";

import Link from "next/link";
import { UserLink } from "@/app/components/UserLink";
import { useParams } from "next/navigation";
import { ArrowLeft } from "lucide-react";
import { AppShell } from "@/app/components/AppShell";
import { Markdown } from "@/app/components/Markdown";
import { PageError, PageLoading, StatusPill } from "@/app/components/ui";
import type { ContentItem } from "@/app/lib/domain";
import { stripRepeatedLead } from "@/app/lib/markdownDisplay";
import { formatBeijing } from "@/app/lib/time";
import { useApiResource } from "@/app/lib/useApiResource";

const kindLabels: Record<string, string> = {
  announcement: "公告",
  research: "研究",
  blog: "文章",
  work: "社区作品",
};

export default function ArticlePage() {
  const params = useParams<{ slug: string }>();
  const { data, loading, error, reload } = useApiResource<ContentItem>(params.slug ? `/content/${params.slug}` : null);
  return <AppShell variant="detail" title={data?.title ?? "文章"} eyebrow="ARTICLE" actions={<Link className="outline-button" href={data?.kind === "work" ? "/works" : "/news"}><ArrowLeft size={15} />{data?.kind === "work" ? "返回作品档案" : "返回资讯"}</Link>}>
    {loading ? <PageLoading /> : error || !data ? <PageError message={error || "文章不存在。"} retry={reload} /> : <div className="article-layout"><aside><StatusPill tone={data.kind === "announcement" ? "live" : data.kind === "research" ? "warm" : "neutral"}>{kindLabels[data.kind] ?? data.kind}</StatusPill><dl><div><dt>作者</dt><dd><UserLink id={data.author_id} name={data.author ?? "UESTC AI 社"} /></dd></div><div><dt>发布时间</dt><dd>{data.published_at ? formatBeijing(data.published_at, { dateStyle: "long" }) : "尚未发布"}</dd></div></dl></aside><Markdown className="markdown-body article-body">{stripRepeatedLead(data.body_md ?? "", data.title)}</Markdown></div>}
  </AppShell>;
}
