"use client";
import { UserLinks } from "@/app/components/UserLink";

import { FormEvent, useMemo, useState } from "react";
import { Check, ClipboardList, Download, ExternalLink, FileText, LockKeyhole, Save } from "lucide-react";
import { AppShell } from "@/app/components/AppShell";
import { EvaluationMetrics } from "@/app/components/EvaluationMetrics";
import { useSession } from "@/app/components/SessionProvider";
import { useToast } from "@/app/components/ToastProvider";
import { Markdown } from "@/app/components/Markdown";
import { EmptyState, FieldError, PageError, PageLoading, StatusPill } from "@/app/components/ui";
import { api, assetUrl, formatApiError, jsonBody } from "@/app/lib/api";
import type { ReviewQueueItem } from "@/app/lib/domain";
import { formatBeijing } from "@/app/lib/time";
import { useApiResource } from "@/app/lib/useApiResource";

const fieldLabels: Record<string, string> = {
  repository: "代码仓库",
  prediction_file: "预测结果",
  report: "技术报告",
  model_link: "模型地址",
  agent_package: "Agent 包",
  policy_package: "策略包",
  demo_url: "在线演示",
  demo_video: "演示视频",
  readme: "补充 README",
};

type SavedReview = NonNullable<ReviewQueueItem["my_review"]>;

function formatBytes(value: number) {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}

function isUrl(value: string) {
  return /^https?:\/\//i.test(value);
}

function ReviewForm({ item, onSaved }: { item: ReviewQueueItem; onSaved: (review: SavedReview) => void }) {
  const toast = useToast();
  const rubric = useMemo(() => item.problem.judging_schema.rubric ?? {}, [item.problem.judging_schema.rubric]);
  const performance = item.evaluation?.performance;
  const [scores, setScores] = useState<Record<string, number>>(
    { ...(item.my_review?.scores ?? Object.fromEntries(Object.keys(rubric).map((key) => [key, 0]))), ...(performance ? { [performance.criterion]: performance.score } : {}) },
  );
  const [feedback, setFeedback] = useState(item.my_review?.feedback_md ?? "");
  const [formError, setFormError] = useState("");
  const [savedAt, setSavedAt] = useState<Date | null>(null);
  const [saving, setSaving] = useState(false);
  const weighted = item.problem.scoring_config.review_score_mode === "weighted";
  const total = useMemo(() => Object.entries(scores).reduce((sum, [key, value]) => sum + Number(value || 0) * (weighted ? rubric[key] ?? 0 : 1), 0), [scores, weighted, rubric]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setFormError(""); setSavedAt(null); setSaving(true);
    try {
      const result = await api<{ id: string; status: string; total_score: number; scores: Record<string, number> }>("/reviews", { method: "POST", ...jsonBody({ submission_version_id: item.id, scores, total_score: total, feedback_md: feedback, status: "submitted" }) });
      const review = { id: result.id, scores: result.scores, total_score: result.total_score, feedback_md: feedback, status: result.status };
      setSavedAt(new Date());
      onSaved(review);
      toast.success(`评审已保存：${item.submission.title}`);
    } catch (requestError) { setFormError(formatApiError(requestError)); }
    finally { setSaving(false); }
  }

  if (item.problem.evaluation_config?.adapter && item.evaluation?.status !== "completed") {
    return <section className="review-form"><header><span>评分表</span><strong>等待指标</strong></header><p role="status">{item.evaluation?.status === "failed" ? "评测失败，本次没有有效指标。请由组织者核查运行环境或参赛者重新提交，完成评测后再评分。" : "自动评测尚未完成。指标生成后，再结合研究报告与代码提交评审。"}</p><small>可先阅读材料，点击工作台“刷新评测结果”查看最新状态。</small></section>;
  }

  return <form className={`review-form ${item.review_locked ? "is-locked" : ""}`} onSubmit={submit} noValidate><header><span>评分表</span><strong>{total.toFixed(1)}</strong></header>{item.review_locked && <div className="review-locked-note" role="status"><LockKeyhole size={15} /><span><strong>在线评分已锁定</strong><small>当前记录仅供查看，组织者解锁后才能修改。</small></span></div>}{Object.entries(rubric).map(([key, weight]) => <label className="score-field" key={key}><span><strong>{key}</strong><small>{weighted ? "计分权重" : "建议权重"} {Math.round(weight * 100)}%{performance?.criterion === key ? " · 平台自动计分" : ""}</small></span><input type="number" min="0" max="100" step="0.5" disabled={item.review_locked || performance?.criterion === key} value={scores[key] ?? 0} onChange={(event) => { setSavedAt(null); setScores((current) => ({ ...current, [key]: Number(event.target.value) })); }} /></label>)}<label className="form-field"><span>评审意见</span><textarea value={feedback} disabled={item.review_locked} onChange={(event) => { setSavedAt(null); setFeedback(event.target.value); }} rows={7} /></label><FieldError>{formError}</FieldError>{savedAt && <div className="review-save-success" role="status"><Check size={15} /><span><strong>评审已保存</strong><small>{formatBeijing(savedAt, { hour: "2-digit", minute: "2-digit" })} · 已同步到评审记录</small></span></div>}<button className="primary-button form-submit" disabled={saving || item.review_locked}>{item.review_locked ? <LockKeyhole size={15} /> : <Save size={15} />}{item.review_locked ? "评分已锁定" : saving ? "正在保存" : "保存评审"}</button></form>;
}

function ReviewMaterials({ item }: { item: ReviewQueueItem }) {
  const fields = Object.entries(item.fields ?? {}).filter(([, value]) => String(value).trim());
  const assets = item.assets ?? [];
  return <><EvaluationMetrics config={item.problem.evaluation_config} run={item.evaluation} />{(fields.length > 0 || assets.length > 0) && <section className="review-materials"><header><span>SUBMITTED MATERIALS</span><strong>作品材料</strong></header>{fields.length > 0 && <dl>{fields.map(([key, rawValue]) => { const value = String(rawValue); return <div key={key}><dt>{fieldLabels[key] ?? key}</dt><dd>{isUrl(value) ? <a href={value} target="_blank" rel="noreferrer"><span>{value}</span><ExternalLink size={14} /></a> : value}</dd></div>; })}</dl>}{assets.length > 0 && <div className="review-assets">{assets.map((asset) => <a href={assetUrl(asset.id)} key={asset.id}><FileText size={15} /><span><strong>{asset.original_name}</strong><small>{formatBytes(asset.size)}</small></span><Download size={14} /></a>)}</div>}</section>}</>;
}

export default function ReviewPage() {
  const { user, loading: sessionLoading } = useSession();
  const allowed = user && ["reviewer", "organizer", "admin"].includes(user.role);
  const { data, loading, error, reload, setData } = useApiResource<ReviewQueueItem[]>(allowed ? "/review-queue" : null);
  const [selectedId, setSelectedId] = useState("");
  const selected = data?.find((item) => item.id === selectedId) ?? data?.[0];

  function updateSavedReview(versionId: string, review: SavedReview) {
    setData((current) => current?.map((item) => item.id === versionId ? { ...item, my_review: review } : item) ?? current);
  }

  if (sessionLoading) return <AppShell title="评审" eyebrow="REVIEW"><PageLoading /></AppShell>;
  if (!allowed) return <AppShell title="评审" eyebrow="REVIEW"><PageError message="当前账户没有评审权限。" /></AppShell>;
  return <AppShell title="评审工作台" eyebrow="REVIEW QUEUE" actions={<button className="outline-button" type="button" disabled={loading} onClick={() => void reload()}>刷新评测结果</button>}>
    {loading ? <PageLoading /> : error ? <PageError message={error} retry={reload} /> : !data?.length ? <EmptyState title="评审队列为空" description="正式提交的版本会出现在这里。" /> : <div className="review-layout">
      <aside className="review-queue"><span>待评作品 · {data.length}</span>{data.map((item) => <button className={selected?.id === item.id ? "selected" : ""} onClick={() => setSelectedId(item.id)} key={item.id}><ClipboardList size={16} /><div><strong>{item.submission.title}</strong><span>{item.team.name} · {item.problem.code} · v{item.version}</span></div>{item.my_review ? <Check size={15} /> : <i />}</button>)}</aside>
      {selected && <div className="review-work"><header><div><div className="review-status-line"><StatusPill tone={selected.my_review ? "live" : "warm"}>{selected.my_review ? "已评" : "待评"}</StatusPill>{selected.review_locked && <StatusPill tone="muted">评分已锁定</StatusPill>}{selected.reviewer_weight_percent != null && <span>本场评委权重 {selected.reviewer_weight_percent.toFixed(2).replace(/\.00$/, "")}%</span>}</div><h2>{selected.submission.title}</h2><p>{selected.team.name} · {selected.track.name} · 版本 {selected.version}</p></div></header><p>队伍成员：<UserLinks users={selected.team.members ?? []} /></p><ReviewMaterials item={selected} /><div className="review-readme-label"><span>README</span><strong>作品说明</strong></div><Markdown>{selected.readme_md}</Markdown></div>}
      {selected && <ReviewForm key={selected.id} item={selected} onSaved={(review) => updateSavedReview(selected.id, review)} />}
    </div>}
  </AppShell>;
}
