"use client";

import Link from "next/link";
import { UserLinks } from "@/app/components/UserLink";
import { useParams } from "next/navigation";
import { useEffect } from "react";
import { ArrowLeft, Download, ExternalLink, FileText, GitBranch, PencilLine } from "lucide-react";
import { AppShell } from "@/app/components/AppShell";
import { EvaluationMetrics } from "@/app/components/EvaluationMetrics";
import { Markdown } from "@/app/components/Markdown";
import { useSession } from "@/app/components/SessionProvider";
import { PageError, PageLoading, StatusPill } from "@/app/components/ui";
import { api, assetUrl } from "@/app/lib/api";
import type { PublicWork } from "@/app/lib/domain";
import { formatBeijing } from "@/app/lib/time";
import { useApiResource } from "@/app/lib/useApiResource";

function formatBytes(value: number) {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}

const fieldLabels: Record<string, string> = {
  repository: "代码仓库", prediction_file: "预测结果", report: "技术报告", model_link: "模型地址",
  agent_package: "Agent 包", policy_package: "策略包", demo_url: "在线演示", demo_video: "演示视频", readme: "补充 README",
};

function isUrl(value: string) {
  return /^https?:\/\//i.test(value);
}

export default function WorkPage() {
  const params = useParams<{ id: string }>();
  const { user } = useSession();
  const resource = params.id ? `/works/${params.id}` : null;
  const { data, loading, error, reload, setData } = useApiResource<PublicWork>(resource);
  useEffect(() => {
    if (data?.evaluation?.status !== "queued" && data?.evaluation?.status !== "running") return;
    const timer = window.setInterval(() => {
      if (resource) void api<PublicWork>(resource).then(setData).catch(() => undefined);
    }, 10000);
    return () => window.clearInterval(timer);
  }, [data?.evaluation?.status, resource, setData]);
  const canEdit = Boolean(user && data?.team_members.some((member) => member.id === user.id));
  return <AppShell variant="detail" title={data?.title ?? "作品"} eyebrow="WORK ARCHIVE" actions={<><Link className="outline-button" href="/works"><ArrowLeft size={15} />返回作品档案</Link>{canEdit && data && <Link className="primary-button" href={`/submit/${data.problem_slug}?submission=${data.id}`}><PencilLine size={15} />继续编辑</Link>}</>}>
    {loading ? <PageLoading /> : error || !data || !data.version ? <PageError message={error || "作品尚未公开。"} retry={reload} /> : <div className="work-layout">
      <div className="work-main"><header className="work-header"><div><StatusPill tone="live">正式提交</StatusPill><p>{data.team_name} · {data.problem_code} · {data.track_name}</p></div><span><GitBranch size={14} />{formatBeijing(data.version.created_at, { dateStyle: "long", timeStyle: "short" })}</span></header><Markdown>{data.version.readme_md}</Markdown></div>
      <aside className="work-aside"><section><span>作品信息</span><dl><div><dt>赛事</dt><dd>{data.competition.name}</dd></div><div><dt>赛道</dt><dd>{data.track.name}</dd></div><div><dt>赛题</dt><dd><Link href={`/competitions/${data.competition.slug}/tracks/${data.track.slug}/problems/${data.problem.slug}`}>{data.problem.code} · {data.problem.title}</Link></dd></div><div><dt>队伍</dt><dd>{data.team_name}</dd></div><div><dt>成员</dt><dd><UserLinks users={data.team_members} /></dd></div></dl></section><section><span>提交字段</span>{Object.entries(data.version.fields ?? {}).length ? <dl>{Object.entries(data.version.fields).map(([key, rawValue]) => { const value = String(rawValue); return <div key={key}><dt>{fieldLabels[key] ?? key}</dt><dd>{isUrl(value) ? <a href={value} target="_blank" rel="noreferrer">{value}<ExternalLink size={12} /></a> : value}</dd></div>; })}</dl> : <p>没有补充字段。</p>}</section><section><span>作品附件</span>{data.assets?.length ? <div className="asset-list">{data.assets.map((asset) => <a href={assetUrl(asset.id)} key={asset.id}><FileText size={15} /><span><strong>{asset.original_name}</strong><small>{formatBytes(asset.size)}</small></span><Download size={14} /></a>)}</div> : <p>没有提交附件。</p>}</section></aside>
    </div>}{!loading && data?.evaluation && <EvaluationMetrics config={data.evaluation_config} run={data.evaluation} />}
  </AppShell>;
}
