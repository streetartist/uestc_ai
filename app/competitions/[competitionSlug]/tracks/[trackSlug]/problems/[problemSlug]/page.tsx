"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { ArrowLeft, ArrowRight, Cpu, ExternalLink, FileCheck2, Gauge, Scale } from "lucide-react";
import { AppShell } from "@/app/components/AppShell";
import { Markdown } from "@/app/components/Markdown";
import { PageError, PageLoading, StatusPill } from "@/app/components/ui";
import type { ProblemDetail } from "@/app/lib/domain";
import { stripRepeatedLead } from "@/app/lib/markdownDisplay";
import { useApiResource } from "@/app/lib/useApiResource";

const fieldLabels: Record<string, string> = {
  repository: "代码仓库", prediction_file: "预测结果", report: "技术报告", model_link: "模型地址",
  agent_package: "Agent 包", policy_package: "策略包", demo_url: "在线演示", demo_video: "演示视频", readme: "README",
};

export default function ProblemPage() {
  const params = useParams<{ competitionSlug: string; trackSlug: string; problemSlug: string }>();
  const { competitionSlug, trackSlug, problemSlug } = params;
  const { data, loading, error, reload } = useApiResource<ProblemDetail>(problemSlug ? `/problems/${problemSlug}` : null);
  return (
    <AppShell variant="detail" title={data?.title ?? "题目详情"} eyebrow={data?.code ?? "PROBLEM"} actions={<Link className="outline-button" href={`/competitions/${competitionSlug}/tracks/${trackSlug}`}><ArrowLeft size={15} />返回赛道</Link>}>
      {loading ? <PageLoading /> : error || !data ? <PageError message={error || "题目不存在。"} retry={reload} /> : (
        <div className="problem-layout">
          <div className="problem-content">
            <div className="problem-summary"><StatusPill tone={data.status === "published" ? "live" : "warm"}>{data.status === "published" ? "正式赛题" : "候选题目"}</StatusPill><p>{data.summary}</p></div>
            <Markdown>{stripRepeatedLead(data.statement_md ?? "题面正在整理。", data.title, data.summary)}</Markdown>
            <section className="detail-section"><h2>提交材料</h2><div className="requirement-list">{(data.submission_schema.fields ?? []).map((field) => { const spec = data.submission_schema.field_definitions?.[field]; return <div key={field}><FileCheck2 size={16} /><span><strong>{spec?.label || fieldLabels[field] || field}{spec?.required ? "（必填）" : ""}</strong><small>{spec?.help || "在作品表单中填写"}</small></span></div>; })}{data.submission_schema.attachments?.map((item) => <div key={`attachment-${item.key}`}><FileCheck2 size={16} /><span><strong>{item.label}{item.min_count > 0 ? "（必交）" : "（可选）"}</strong><small>{item.extensions.map((ext) => `.${ext}`).join(" / ")}；{item.min_count}—{item.max_count} 个附件</small></span></div>)}</div></section>
            {data.evaluation_config?.adapter && <section className="detail-section"><h2>程序运行</h2><p>请随作品上传一个 .zip 程序包。运行任务：{data.evaluation_config.task}；运行 {data.evaluation_config.resources?.episodes} 次。运行指标会显示在作品页，供评委参考。</p></section>}
            <section className="detail-section"><h2>评分构成</h2><div className="rubric-list">{Object.entries(data.judging_schema.rubric ?? {}).map(([name, weight]) => <div key={name}><span>{fieldLabels[name] ?? name}</span><i /><strong>{Math.round(weight * 100)}%</strong></div>)}</div></section>
          </div>
          <aside className="problem-aside">
            <dl className="facts-list"><div><dt><Gauge size={15} />难度</dt><dd>{data.difficulty}/5</dd></div><div><dt><Cpu size={15} />算力建议</dt><dd>{data.compute_note || "按题目说明"}</dd></div><div><dt><Scale size={15} />运行方式</dt><dd>{data.evaluation_config?.adapter ? "独立环境运行" : "上传材料"}</dd></div></dl>
            {data.source_url && <a className="source-link" href={data.source_url} target="_blank" rel="noreferrer">查看外部资料 <ExternalLink size={14} /></a>}
            <div className="submit-callout"><span>准备好提交？</span><p>先登录并为队伍报名当前赛道。</p><Link className="primary-button" href={`/submit/${data.slug}`}>提交这个题目 <ArrowRight size={16} /></Link></div>
          </aside>
        </div>
      )}
    </AppShell>
  );
}
