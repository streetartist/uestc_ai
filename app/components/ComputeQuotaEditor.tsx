"use client";
import { FormEvent, useState } from "react";
import Link from "next/link";
import { api, formatApiError, jsonBody } from "@/app/lib/api";
import { useApiResource } from "@/app/lib/useApiResource";
import { useToast } from "./ToastProvider";
import { FieldError, PageError, PageLoading } from "./ui";
import type { ComputeConfig, ComputeProvider } from "@/app/lib/compute";

export function ComputeQuotaEditor({ problemId }: { problemId: string }) {
  const endpoint = `/compute/manage/problems/${problemId}/quota`;
  const policy = useApiResource<{ config: ComputeConfig | null; team_count: number }>(endpoint);
  const channels = useApiResource<ComputeProvider[]>("/compute/manage/providers");
  if ((policy.loading && !policy.data) || (channels.loading && !channels.data)) return <PageLoading />;
  if (policy.error || channels.error) return <PageError message={policy.error || channels.error} retry={async () => { await policy.reload(); await channels.reload(); }} />;
  return <section className="ai-platform" aria-label="本题统一算力额度">
    <div className="ai-section-title"><div><h2>队伍自用算力额度</h2><p>供队伍开发、调试和自主运行使用。本题各队预算统一，独立计量；自动指标测试的时长和次数在题目设置中配置。</p></div><span>{policy.data?.team_count ?? 0} 支队伍</span></div>
    {channels.data?.length ? <ComputeQuotaForm key={JSON.stringify(policy.data?.config)} config={policy.data?.config ?? null} channels={channels.data} endpoint={endpoint} reload={policy.reload} /> : <div className="ai-notice">尚未配置算力渠道，请管理员在 <Link href="/api-platform">API 平台 → 算力渠道</Link> 添加 AutoDL 容器实例 Pro。</div>}
  </section>;
}

function ComputeQuotaForm({ config, channels, endpoint, reload }: { config: ComputeConfig | null; channels: ComputeProvider[]; endpoint: string; reload: () => Promise<void> }) {
  const [busy, setBusy] = useState(false), [error, setError] = useState("");
  const toast = useToast();
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const form = new FormData(event.currentTarget);
    setBusy(true); setError("");
    try {
      await api(endpoint, { method: "PUT", ...jsonBody({ provider_id: form.get("provider"), enabled: form.get("enabled") === "on", max_gpu_seconds: Math.round(Number(form.get("hours")) * 3600), max_cost_millis: String(form.get("cost")).trim() ? Math.round(Number(form.get("cost")) * 1000) : null }) });
      await reload(); toast.success("本题统一算力额度已保存");
    } catch (e) { setError(formatApiError(e)); } finally { setBusy(false); }
  }
  return <form className="ai-form-panel" onSubmit={submit}>
    <div className="ai-section-title"><h3>每队算力预算</h3><label className="ai-check"><input name="enabled" type="checkbox" defaultChecked={config?.enabled ?? true} />启用本题算力</label></div>
    <div className="ai-form-grid">
      <label className="form-field ai-wide"><span>算力渠道</span><select name="provider" defaultValue={config?.provider_id ?? channels[0]?.id}>{channels.map(p => <option key={p.id} value={p.id}>{p.name} · {p.gpu_label} × {p.gpu_count}{p.enabled ? "" : "（已停用）"}</option>)}</select></label>
      <label className="form-field"><span>每队 GPU 卡时</span><input name="hours" type="number" required min={0} step="any" defaultValue={config ? config.max_gpu_seconds / 3600 : 10} /></label>
      <label className="form-field"><span>每队参考运行费用上限 · 元（可选）</span><input name="cost" type="number" min={0} step="0.001" defaultValue={config?.max_cost_millis == null ? "" : config.max_cost_millis / 1000} placeholder="留空仅限制卡时" /></label>
    </div>
    <div className="ai-notice">卡时按分配的 GPU 数量 × 发起开机至确认关机的时长计量，不按 GPU 利用率计算。开机前预占本次预算与 30 秒关机余量；到时自动关机。运行费用为参考估算，不含关机后的存储费用，实际账单以 AutoDL 为准。</div>
    <FieldError>{error}</FieldError><button className="primary-button" disabled={busy}>{busy ? "正在保存…" : "保存本题算力额度"}</button>
  </form>;
}
