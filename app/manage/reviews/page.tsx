"use client";

import Link from "next/link";
import { UserLink } from "@/app/components/UserLink";
import { useMemo, useState } from "react";
import { ArrowRight, Check, CircleGauge, LockKeyhole, Save, Scale, UnlockKeyhole } from "lucide-react";
import { AppShell } from "@/app/components/AppShell";
import { ConfirmDialog } from "@/app/components/ConfirmDialog";
import { useSession } from "@/app/components/SessionProvider";
import { useToast } from "@/app/components/ToastProvider";
import { EmptyState, FieldError, PageError, PageLoading, StatusPill } from "@/app/components/ui";
import { api, formatApiError, jsonBody } from "@/app/lib/api";
import type { Competition, ReviewerWeightConfiguration, User } from "@/app/lib/domain";
import { formatBeijing } from "@/app/lib/time";
import { useApiResource } from "@/app/lib/useApiResource";

const roleLabels: Record<User["role"], string> = {
  member: "普通成员", reviewer: "评委", editor: "内容编辑", organizer: "赛事组织者", admin: "管理员",
};

function percent(value: number) {
  return `${value.toFixed(2).replace(/\.00$/, "").replace(/(\.\d)0$/, "$1")}%`;
}

function WeightEditor({ data, reload }: { data: ReviewerWeightConfiguration; reload: () => Promise<void> }) {
  const toast = useToast();
  const [selectedIds, setSelectedIds] = useState(() => new Set(data.reviewers.filter((reviewer) => reviewer.selected).map((reviewer) => reviewer.id)));
  const [weights, setWeights] = useState<Record<string, string>>(() => Object.fromEntries(data.reviewers.map((reviewer) => [reviewer.id, reviewer.weight_percent == null ? "" : String(reviewer.weight_percent)])));
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState("");
  const [lockDialogOpen, setLockDialogOpen] = useState(false);
  const [lockBusy, setLockBusy] = useState(false);
  const [lockError, setLockError] = useState("");

  const selected = data.reviewers.filter((reviewer) => selectedIds.has(reviewer.id));
  const manualWeights = selected.map((reviewer) => weights[reviewer.id]?.trim() ?? "").filter(Boolean).map(Number);
  const manualTotal = manualWeights.reduce((sum, weight) => sum + (Number.isFinite(weight) ? weight : 0), 0);
  const automaticCount = selected.filter((reviewer) => !weights[reviewer.id]?.trim()).length;
  const remaining = Math.max(0, 100 - manualTotal);
  const automaticWeight = automaticCount ? remaining / automaticCount : 0;
  const invalidWeight = manualWeights.some((weight) => !Number.isFinite(weight) || weight <= 0 || weight > 100);
  const valid = selected.length === 0 || (!invalidWeight && manualTotal <= 100
    && (automaticCount > 0 ? manualTotal < 100 : Math.abs(manualTotal - 100) < 0.001));

  function toggleReviewer(id: string) {
    if (data.locked) return;
    setFormError("");
    setSelectedIds((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }

  async function toggleLock() {
    setLockBusy(true);
    setLockError("");
    try {
      await api(`/manage/competitions/${data.competition_id}/review-lock`, {
        method: "PATCH",
        ...jsonBody({ locked: !data.locked }),
      });
      setLockDialogOpen(false);
      await reload();
      toast.success(data.locked ? "在线评分已重新开放" : "在线评分已锁定");
    } catch (requestError) {
      setLockError(formatApiError(requestError));
    } finally {
      setLockBusy(false);
    }
  }

  async function save() {
    if (!valid) {
      setFormError(manualTotal > 100 ? "手动指定的在线权重不能超过 100%。" : automaticCount && manualTotal >= 100 ? "需要给自动分配的评委留下权重。" : "全部手动设置时，在线评委权重合计必须等于 100%。");
      return;
    }
    setSaving(true);
    setFormError("");
    try {
      await api(`/manage/competitions/${data.competition_id}/reviewers`, {
        method: "PUT",
        ...jsonBody({ reviewers: selected.map((reviewer) => ({ user_id: reviewer.id, weight_percent: weights[reviewer.id]?.trim() ? Number(weights[reviewer.id]) : null })) }),
      });
      await reload();
      toast.success("评委与权重已保存");
    } catch (requestError) {
      setFormError(formatApiError(requestError));
    } finally {
      setSaving(false);
    }
  }

  return <>
    <section className={`review-lock-panel ${data.locked ? "is-locked" : ""}`}>
      <span className="review-lock-icon">{data.locked ? <LockKeyhole size={19} /> : <UnlockKeyhole size={19} />}</span>
      <div><strong>{data.locked ? "在线评分已锁定" : "在线评分开放中"}</strong><p>{data.locked ? `评委只能查看已有评分${data.locked_at ? ` · 锁定于 ${formatBeijing(data.locked_at, { dateStyle: "medium", timeStyle: "short" })}` : ""}` : "评委可以新增或修改评分，评委名单与权重也可调整。"}</p></div>
      <button type="button" className={data.locked ? "outline-button" : "danger-button subtle"} onClick={() => { setLockError(""); setLockDialogOpen(true); }}>{data.locked ? <UnlockKeyhole size={15} /> : <LockKeyhole size={15} />}{data.locked ? "重新开放评分" : "锁定在线评分"}</button>
    </section>
    <div className="review-weight-layout">
      <section className="reviewer-weight-panel">
        <header><div><span className="eyebrow"><i />REVIEWERS</span><h2>评委分配</h2></div><div className="reviewer-bulk-actions"><button type="button" className="text-button" disabled={data.locked} onClick={() => setSelectedIds(new Set(data.reviewers.filter((reviewer) => reviewer.role === "reviewer").map((reviewer) => reviewer.id)))}>选择全部评委</button><button type="button" className="text-button" disabled={data.locked} onClick={() => setWeights(Object.fromEntries(data.reviewers.map((reviewer) => [reviewer.id, ""]))) }>全部等权</button></div></header>
        <div className="reviewer-weight-head"><span>参与</span><span>评委</span><span>指定权重</span><span>生效权重</span></div>
        <div className="reviewer-weight-list">{data.reviewers.map((reviewer) => {
          const isSelected = selectedIds.has(reviewer.id);
          const rawWeight = weights[reviewer.id]?.trim();
          const effective = isSelected ? rawWeight ? Number(rawWeight) : automaticWeight : null;
          return <div className={isSelected ? "selected" : ""} key={reviewer.id}>
            <label className="reviewer-check"><input type="checkbox" disabled={data.locked} checked={isSelected} onChange={() => toggleReviewer(reviewer.id)} /><span><Check size={13} /></span></label>
            <div className="reviewer-identity"><span className="account-avatar">{reviewer.name.slice(0, 1)}</span><span><strong><UserLink id={reviewer.id} name={reviewer.name} /></strong><small>{reviewer.email} · {roleLabels[reviewer.role]}</small></span></div>
            <label className="weight-input"><input type="number" min="0.01" max="100" step="0.01" disabled={!isSelected || data.locked} value={weights[reviewer.id] ?? ""} onChange={(event) => { setFormError(""); setWeights((current) => ({ ...current, [reviewer.id]: event.target.value })); }} placeholder="自动" /><span>%</span></label>
            <span className={`effective-weight ${rawWeight ? "manual" : "automatic"}`}>{isSelected && effective != null && Number.isFinite(effective) ? <><strong>{percent(effective)}</strong><small>{rawWeight ? "手动" : "自动"}</small></> : "-"}</span>
          </div>;
        })}</div>
        <footer><div><FieldError>{formError}</FieldError><p>{data.locked ? "解锁在线评分后才能调整评委与权重。" : "留空的评委会平均分配手动指定后剩余的在线评审权重；允许保存空名单。"}</p></div><button className="primary-button" type="button" disabled={saving || !valid || data.locked} onClick={() => void save()}><Save size={15} />{saving ? "正在保存" : "保存评委权重"}</button></footer>
      </section>

      <aside className="review-weight-note"><CircleGauge size={20} /><strong>分配规则</strong><p>这里仅配置本赛事的在线评委。只需填写需要特殊指定的人，其余参与评委会自动平分剩余权重。</p><dl><div><dt>例如</dt><dd>主评委指定 40%，另有两位留空</dd></div><div><dt>结果</dt><dd>主评委 40%，其余两位各 30%</dd></div></dl><p>是否采用外部评分及其占比，由每道赛题单独设置。所有配置来源完成后才产生最终分。</p></aside>
    </div>

    <section className="review-result-section"><header className="section-header"><div><span className="eyebrow"><i />WEIGHTED RESULTS</span><h2>加权评审进度</h2></div></header>{!data.review_summaries.length ? <EmptyState title="还没有正式提交" description="正式作品进入评审后会显示在这里。" /> : <div className="weighted-review-list"><div className="weighted-review-head"><span>作品</span><span>完成情况</span><span>覆盖权重</span><span>加权得分</span><span /></div>{data.review_summaries.map((item) => <Link href={`/works/${item.submission_id}`} key={item.submission_version_id}><div><strong>{item.title}</strong><span>{item.team_name} · {item.problem_code} · v{item.version}</span></div><StatusPill tone={item.final_score != null ? "live" : item.reviewed_count || item.external_score != null ? "warm" : "muted"}>{item.final_score != null ? "评审完成" : `${item.reviewed_count}/${item.reviewer_count} 已评${item.external_score != null ? " · 外部已到" : ""}`}</StatusPill><span>{percent(item.completed_weight_percent + (item.external_score != null ? item.external_weight_percent : 0))}</span><strong>{item.final_score != null ? item.final_score.toFixed(2) : item.provisional_score != null ? `${item.provisional_score.toFixed(2)} 暂算` : "-"}</strong><ArrowRight size={15} /></Link>)}</div>}</section>
    <ConfirmDialog open={lockDialogOpen} title={data.locked ? "重新开放在线评分？" : "锁定在线评分？"} description={data.locked ? "解锁后，已分配评委可以继续新增或修改评分，最终分可能随之变化。" : "锁定后，所有评委只能查看已有评分，评委名单、权重与在线评分规则也不能修改。外部评分批次不受影响。"} confirmLabel={data.locked ? "确认重新开放" : "确认锁定"} busy={lockBusy} error={lockError} onCancel={() => { if (!lockBusy) { setLockDialogOpen(false); setLockError(""); } }} onConfirm={() => void toggleLock()} />
  </>;
}

export default function ManageReviewsPage() {
  const { user, loading: sessionLoading } = useSession();
  const allowed = user && ["organizer", "admin"].includes(user.role);
  const { data: catalog, loading: catalogLoading, error: catalogError } = useApiResource<Competition[]>(allowed ? "/manage/catalog" : null);
  const [selectedCompetition, setSelectedCompetition] = useState<string | null>(null);
  const competitionId = selectedCompetition ?? catalog?.[0]?.id ?? "";
  const competition = useMemo(() => catalog?.find((item) => item.id === competitionId), [catalog, competitionId]);
  const { data, loading, error, reload } = useApiResource<ReviewerWeightConfiguration>(competitionId ? `/manage/reviewer-weights?competition_id=${competitionId}` : null);

  if (sessionLoading || catalogLoading) return <AppShell title="评审管理" eyebrow="WORKSPACE"><PageLoading /></AppShell>;
  if (!allowed) return <AppShell title="评审管理" eyebrow="WORKSPACE"><PageError message="当前账户没有评审管理权限。" /></AppShell>;
  if (catalogError) return <AppShell title="评审管理" eyebrow="WORKSPACE"><PageError message={catalogError} /></AppShell>;
  return <AppShell title="评审管理" eyebrow="REVIEW OPERATIONS" actions={<Link className="outline-button" href="/review"><Scale size={15} />进入评审工作台</Link>}>
    <div className="page-intro review-manage-intro"><div><p>指定本赛事实际参与的评委。手动权重之外的剩余比例会由未设置权重的评委平均分配。</p></div><label className="form-field competition-picker"><span>当前赛事</span><select value={competitionId} onChange={(event) => setSelectedCompetition(event.target.value)}>{catalog?.map((item) => <option value={item.id} key={item.id}>{item.name}</option>)}</select></label></div>
    {loading ? <PageLoading /> : error ? <PageError message={error} retry={reload} /> : !competition || !data ? <EmptyState title="没有可配置的赛事" description="创建赛事后即可设置评委。" /> : <WeightEditor key={`${competition.id}-${data.configured}-${data.locked}-${data.reviewers.map((reviewer) => `${reviewer.id}:${reviewer.selected}:${reviewer.weight_percent}`).join("|")}`} data={data} reload={reload} />}
  </AppShell>;
}
