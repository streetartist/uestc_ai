"use client";

import Link from "next/link";
import { FormEvent, useState } from "react";
import { api, formatApiError, jsonBody } from "@/app/lib/api";
import { useApiResource } from "@/app/lib/useApiResource";
import { useToast } from "@/app/components/ToastProvider";
import { EmptyState, FieldError, PageError, PageLoading } from "@/app/components/ui";
import type { AIChannel, AIGrant, AIProblem, AIProblemQuota, AIQuotaConfig, AITeam } from "@/app/lib/ai-platform";
import { points } from "@/app/lib/ai-platform";

const num = (value: number) => value.toLocaleString("zh-CN");
const split = (value: string) => value.split(/[,，\n]/).map(v => v.trim()).filter(Boolean);
type QuotaProblem = Pick<AIProblem, "id" | "title" | "competition_id">;

function useAction() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const toast = useToast();
  async function run(action: () => Promise<void>, message: string) {
    if (busy) return;
    setBusy(true); setError("");
    try { await action(); toast.success(message); }
    catch (e) { setError(formatApiError(e)); }
    finally { setBusy(false); }
  }
  return { busy, error, run };
}

export function ProblemAIQuotas({ problem }: { problem: QuotaProblem }) {
  const endpoint = `/ai/manage/problems/${problem.id}/quota`;
  const policy = useApiResource<AIProblemQuota>(endpoint);
  if (policy.loading && !policy.data) return <PageLoading />;
  if (policy.error) return <PageError message={policy.error} retry={policy.reload} />;
  return <section className="ai-platform" aria-label="本题统一 API 额度">
    <div className="ai-section-title"><div><h2>本题统一 API 额度</h2><p>统一设置每支队伍可用的模型与额度。各队独立计量，后续新队伍自动适用。</p></div><span>{policy.data?.team_count ?? 0} 支队伍</span></div>
    <div className="ai-notice">{policy.data?.config ? "本题已设置统一额度，修改后应用于所有队伍并保留已用量。" : "尚未设置本题额度，可在队伍报名之前先保存配置。保存后将统一现有队伍的本题额度，保留各队已用量。"}</div>
    <QuotaForm key={JSON.stringify(policy.data?.config ?? null)} title={problem.title} uniform config={policy.data?.config ?? null} endpoint={endpoint} saved={policy.reload} />
  </section>;
}

export function AIQuotaEditor({ grants, onChange }: {
  grants: AIGrant[];
  onChange: () => Promise<void>;
}) {
  const { data: teams, loading, error, reload } = useApiResource<AITeam[]>("/ai/manage/teams");
  const [teamId, setTeamId] = useState("");
  const selected = teams?.find(t => t.id === teamId) ?? teams?.[0];
  const grant = grants.find(g => g.team_id === selected?.id && g.problem_id === null);
  return <section className="ai-platform" aria-label="队伍通用额度">
    <div className="ai-section-title"><div><h2>队伍通用额度</h2><p>通用额度用于未配置统一额度的题目。本题统一额度请在赛题管理中设置。</p></div><Link className="outline-button" href="/manage/competitions">前往赛题管理</Link></div>
    {loading ? <PageLoading /> : error ? <PageError message={error} retry={reload} /> : <>
      <label className="form-field ai-team-select"><span>选择队伍</span><select value={selected?.id ?? ""} disabled={!selected} onChange={e => setTeamId(e.target.value)}>{!selected && <option value="">暂无参赛队伍</option>}{teams?.map(t => <option key={t.id} value={t.id}>{t.name} · {t.competition_name}</option>)}</select></label>
      {selected ? <QuotaForm key={`${selected.id}-${grant?.id ?? "new"}-${grant?.max_tokens}`} title={selected.name} config={grant ?? null} grant={grant} endpoint={`/ai/manage/grants/${selected.id}`} saved={onChange} /> : <EmptyState title="还没有参赛队伍" description="比赛有参赛队伍后，可为队伍分配通用额度。" />}
    </>}
  </section>;
}

function QuotaForm({ title, config, grant, endpoint, saved, uniform = false }: {
  title: string;
  config: AIQuotaConfig | null;
  grant?: AIGrant;
  endpoint: string;
  saved: () => Promise<void>;
  uniform?: boolean;
}) {
  const action = useAction();
  const { data: channels, loading: channelsLoading, error: channelsError, reload: reloadChannels } = useApiResource<AIChannel[]>("/ai/manage/channels");
  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault(); const fields = new FormData(e.currentTarget);
    const body = { allowed_models: split(String(fields.get("models"))), allowed_channels: fields.getAll("channels"), enabled: fields.get("enabled") === "on",
      max_calls: Number(fields.get("max_calls")), max_tokens: Number(fields.get("max_tokens")),
      max_output_tokens: Number(fields.get("max_output_tokens")), requests_per_minute: Number(fields.get("requests_per_minute")), max_concurrent: Number(fields.get("max_concurrent")),
      max_cost_micros: String(fields.get("budget")).trim() ? Math.round(Number(fields.get("budget")) * 1e6) : null };
    await action.run(async () => { await api(endpoint, { method: "PUT", ...jsonBody(body) }); await saved(); }, uniform ? "本题统一额度已保存" : "队伍通用额度已保存");
  }
  return <form className="ai-form-panel" onSubmit={submit}><div className="ai-section-title"><div><h3>{title}</h3><p className="ai-grant-scope">{uniform ? "每队相同额度 · 各队独立使用" : "队伍通用额度"}</p></div><label className="ai-check"><input name="enabled" type="checkbox" defaultChecked={config?.enabled ?? true} />{uniform ? "启用本题 API" : "启用队伍 API"}</label></div>{grant && <div className="ai-notice">已使用 {num(grant.calls_used)} 次调用、{num(grant.tokens_used)} token、{points(grant.cost_used_micros)} 点。</div>}<div className="ai-form-grid"><label className="form-field ai-wide"><span>授权模型 · 每行一个或用逗号分隔</span><textarea name="models" rows={3} required defaultValue={config?.allowed_models.join("\n")} placeholder="填写模型广场中的模型名称" /><small>已配置：{Array.from(new Set(channels?.flatMap(c => Object.keys(c.models)))).join("、") || "暂无模型"}</small></label>{[{ name: "max_calls", label: uniform ? "每队总调用次数" : "总调用次数", value: config?.max_calls ?? 1000, min: 0 }, { name: "max_tokens", label: uniform ? "每队 Token 配额" : "总 Token 配额", value: config?.max_tokens ?? 1000000, min: 0 }, { name: "max_output_tokens", label: "单次最大输出 Token", value: config?.max_output_tokens ?? 4096, min: 1 }, { name: "requests_per_minute", label: "每分钟调用上限", value: config?.requests_per_minute ?? 60, min: 1 }, { name: "max_concurrent", label: "同时调用上限", value: config?.max_concurrent ?? 2, min: 1 }].map(field => <label className="form-field" key={field.name}><span>{field.label}</span><input name={field.name} type="number" min={field.min} step={1} defaultValue={field.value} required /></label>)}<label className="form-field"><span>{uniform ? "每队费用配额 · 点（可选）" : "费用配额 · 点（可选）"}</span><input name="budget" type="number" min={0} step="any" defaultValue={config?.max_cost_micros == null ? "" : config.max_cost_micros / 1e6} placeholder="留空不限制费用" /></label><fieldset className="ai-channel-checks ai-wide"><legend>允许渠道 · 不勾选则使用全部启用渠道</legend>{channels?.map(c => <label className="ai-check" key={c.id}><input name="channels" value={c.id} type="checkbox" defaultChecked={config?.allowed_channels.includes(c.id)} />{c.name}</label>)}</fieldset></div><FieldError>{action.error}</FieldError>{channelsError && <PageError message={channelsError} retry={reloadChannels} />}<button className="primary-button" disabled={action.busy || channelsLoading || Boolean(channelsError)}>{action.busy ? "正在保存…" : uniform ? "保存本题统一额度" : "保存配额"}</button></form>;
}
