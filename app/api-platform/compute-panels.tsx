"use client";
import { FormEvent, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { Cpu, Power, Square, Terminal, RefreshCw } from "lucide-react";
import { api, formatApiError, jsonBody } from "@/app/lib/api";
import { useApiResource } from "@/app/lib/useApiResource";
import { copyText } from "@/app/lib/clipboard";
import { useToast } from "@/app/components/ToastProvider";
import { EmptyState, FieldError, PageError, PageLoading } from "@/app/components/ui";
import { activeCompute, computeCost, computeState, gpuHours } from "@/app/lib/compute";
import type { ComputeGrant, ComputeOverview, ComputeProvider } from "@/app/lib/compute";
import { ComputeProviderForm } from "./ComputeProviderForm";
import { ComputeInstanceTools } from "./ComputeInstanceTools";

const computeErrors: Record<string, string> = {
  price_above_ceiling: "实际价格超过渠道预算单价，已请求关机。",
  price_unknown: "尚未确认运行价格，已请求关机。",
  startup_pending: "正在核对开机结果，请勿重复开机。",
  create_result_unknown: "尚未确认创建结果，正在核对已有实例。",
  gateway_not_configured: "渠道凭据无法读取，请联系管理员核对加密配置。",
};

export function ComputeQuotaCard({ grant }: { grant: ComputeGrant }) {
  const meters = [{ label: "GPU 卡时", used: grant.gpu_seconds_used, max: grant.max_gpu_seconds, format: gpuHours }, ...(grant.max_cost_millis === null ? [] : [{ label: "参考运行费用 · 元", used: grant.cost_used_millis, max: grant.max_cost_millis, format: computeCost }])];
  return <article className="ai-quota-card"><div className="ai-card-heading"><div><small>{grant.competition_name}</small><h3>{grant.team_name}</h3></div><span className={`ai-badge ${grant.enabled ? "ok" : "off"}`}>{grant.enabled ? "已授权" : "已停用"}</span></div><p className="ai-grant-scope">{grant.problem_title}</p>
    {meters.map(m => <div className="ai-quota-meter" key={m.label}><div><span>{m.label}</span><strong>{m.format(m.used)} <small>/ {m.format(m.max)}</small></strong></div><progress value={Math.min(m.used, m.max)} max={m.max || 1} /></div>)}
    <div className="ai-card-foot"><span>{grant.gpu_label} × {grant.gpu_count}</span><span>已用量含预占</span></div>
  </article>;
}

export function ComputeResources({ reviewer, allowSSH }: { reviewer: boolean; allowSSH: boolean }) {
  const resource = useApiResource<ComputeOverview>("/compute/overview");
  const [busy, setBusy] = useState(""), [error, setError] = useState("");
  const [ssh, setSSH] = useState<{ instance: string; command: string; password: string } | null>(null);
  const requestIds = useRef<Record<string, { seconds: number; id: string }>>({});
  const toast = useToast();
  const reload = resource.reload;
  useEffect(() => { const timer = setInterval(() => { void reload(); }, 10000); return () => clearInterval(timer); }, [reload]);
  useEffect(() => { if (!ssh) return; const timer = setTimeout(() => setSSH(null), 60000); return () => clearTimeout(timer); }, [ssh]);
  async function action(id: string, fn: () => Promise<void>) {
    if (busy) return; setBusy(id); setError("");
    try { await fn(); await resource.reload(); } catch (e) { setError(formatApiError(e)); } finally { setBusy(""); }
  }
  async function start(event: FormEvent<HTMLFormElement>, grant: ComputeGrant) {
    event.preventDefault(); const seconds = Math.round(Number(new FormData(event.currentTarget).get("minutes")) * 60);
    let req = requestIds.current[grant.id];
    if (!req || req.seconds !== seconds) req = requestIds.current[grant.id] = { seconds, id: crypto.randomUUID() };
    await action(grant.id, async () => { await api(`/compute/grants/${grant.id}/start`, { method: "POST", ...jsonBody({ request_id: req.id, duration_seconds: seconds }) }); delete requestIds.current[grant.id]; toast.success("已提交开机请求"); });
  }
  if (resource.loading && !resource.data) return <PageLoading />;
  if (resource.error && !resource.data) return <PageError message={resource.error} retry={resource.reload} />;
  const data = resource.data;
  if (!data) return null;
  return <section className="ai-platform"><div className="ai-section-title"><div><h2>算力实例</h2><p>每队每题独立实例，关机后保留文件，再次开机继续使用。</p></div><button className="outline-button" onClick={resource.reload}><RefreshCw size={14} />刷新状态</button></div>
    {!data.worker_online && <div className="ai-notice">算力运行端离线，暂时不能开机。已有实例的关机请求会保留，运行端恢复后继续处理。</div>}
    <div className="ai-notice">请先选择本次使用时长；平台预占预算，到时自动关机。用量含预占，关机确认后结算。关机保留的磁盘可能继续产生 AutoDL 存储费用。</div>
    <FieldError>{error || resource.error}</FieldError>
    {data.grants.length ? <div className="ai-card-grid">{data.grants.map(grant => {
      const instance = data.instances.find(i => i.grant_id === grant.id);
      const session = data.active_sessions.find(s => s.grant_id === grant.id) ?? data.sessions.find(s => s.grant_id === grant.id);
      const active = session && activeCompute(session.state);
      return <div className="compute-resource" key={grant.id}><ComputeQuotaCard grant={grant} />
        <div className="compute-controls"><div className="ai-card-heading"><span className={`ai-badge ${session?.state === "running" ? "ok" : "off"}`}>{session ? computeState(session.state) : "尚未开机"}</span><small>{grant.provider_name}</small></div>
          {session?.deadline && active && <p>计划关机：{new Date(session.deadline).toLocaleString("zh-CN")}</p>}
          {session?.error_code && <p className="field-error">{computeErrors[session.error_code] ?? "上游操作结果待确认，平台保留预占额度并继续核对。"}</p>}
          {!reviewer && (active && instance ? <div className="ai-button-row"><button className="outline-button" disabled={Boolean(busy) || session.stop_requested} onClick={() => { setSSH(null); void action(grant.id, async () => { await api(`/compute/instances/${instance.id}/stop`, { method: "POST" }); toast.success("已提交关机请求"); }); }}><Square size={14} />{session.stop_requested ? "等待确认关机" : "关机"}</button>
            {session.state === "running" && !session.stop_requested && allowSSH && <button className="outline-button" disabled={Boolean(busy)} onClick={() => void action(instance.id, async () => { const info = await api<{ command: string; password: string }>(`/compute/instances/${instance.id}/ssh`, { method: "POST", cache: "no-store" }); setSSH({ instance: instance.id, ...info }); })}><Terminal size={14} />SSH 连接</button>}</div> : <form className="compute-start" onSubmit={e => void start(e, grant)}><label className="form-field"><span>本次使用时长 · 分钟</span><input name="minutes" type="number" required min={1} max={10080} step={1} defaultValue={30} /></label><button className="primary-button" disabled={Boolean(busy) || !data.worker_online || !grant.enabled}><Power size={14} />{busy === grant.id ? "提交中…" : instance?.remote_id ? "开机" : "创建并开机"}</button></form>)}
          {instance && ssh?.instance === instance.id && <div className="compute-ssh"><strong>SSH 连接信息</strong><code>{ssh.command}</code><label className="form-field"><span>root 密码</span><input type="password" readOnly value={ssh.password} autoComplete="off" /></label><div className="ai-button-row"><button className="outline-button" onClick={async () => { try { await copyText(ssh.password); toast.success("SSH 密码已复制"); } catch { toast.error("复制失败，请稍后再试"); } }}>复制密码</button><button className="outline-button" onClick={() => setSSH(null)}>隐藏连接信息</button></div><small>连接信息一分钟后自动隐藏。</small></div>}
          {instance && !reviewer && allowSSH && <ComputeInstanceTools instanceId={instance.id} enabled={session?.state === "running" && !session.stop_requested && grant.enabled} />}
        </div></div>;
    })}</div> : <EmptyState title="还没有分配算力额度" description="组织方在题目资源额度中设置预算后，各队可在这里使用 AutoDL 算力。" />}
    <div className="ai-section-title"><h2>算力使用记录</h2><span>最近 100 次</span></div>
    <div className="ai-table-wrap"><table className="ai-table"><thead><tr>{["队伍 / 题目", "状态", "计划时长", "GPU 卡时", "参考运行费用", "开机时间"].map(label => <th key={label}>{label}</th>)}</tr></thead><tbody>{data.sessions.map(s => <tr key={s.id}><td>{s.team_name}<small>{s.problem_title}</small></td><td>{computeState(s.state)}</td><td>{Math.round(s.duration_seconds / 60)} 分钟</td><td>{gpuHours(s.charged_gpu_seconds ?? s.reserved_gpu_seconds)}{s.charged_gpu_seconds === null && <small>预占</small>}</td><td>¥{computeCost(s.charged_cost_millis ?? s.reserved_cost_millis)}</td><td>{new Date(s.dispatched_at ?? s.created_at).toLocaleString("zh-CN")}</td></tr>)}{!data.sessions.length && <tr><td colSpan={6}>暂无算力使用记录。</td></tr>}</tbody></table></div>
  </section>;
}

export function ComputeProviders({ editable }: { editable: boolean }) {
  const resource = useApiResource<ComputeProvider[]>("/compute/manage/providers");
  const [edit, setEdit] = useState<ComputeProvider | "new" | null>(null), [error, setError] = useState(""), [busy, setBusy] = useState(false);
  const toast = useToast();
  async function test(id: string) { setBusy(true); setError(""); try { await api(`/compute/manage/providers/${id}/test`, { method: "POST" }); toast.success("AutoDL 连接正常，未创建实例"); } catch (e) { setError(formatApiError(e)); } finally { setBusy(false); } }
  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const form = new FormData(event.currentTarget);
    setBusy(true); setError("");
    const body = { name: form.get("name"), token: form.get("token"), image_uuid: form.get("image_uuid"), gpu_spec_uuid: form.get("gpu_spec_uuid"), gpu_label: form.get("gpu_label"), gpu_count: Number(form.get("gpu_count")), hourly_price_millis: Math.round(Number(form.get("price")) * 1000), cuda_v_from: Number(form.get("cuda_v_from")), data_centers: form.getAll("centers"), enabled: form.get("enabled") === "on" };
    try { await api(`/compute/manage/providers${edit && edit !== "new" ? `/${edit.id}` : ""}`, { method: edit === "new" ? "POST" : "PUT", ...jsonBody(body) }); setEdit(null); await resource.reload(); toast.success("算力渠道已保存"); } catch (e) { setError(formatApiError(e)); } finally { setBusy(false); }
  }
  if (resource.loading && !resource.data) return <PageLoading />;
  if (resource.error) return <PageError message={resource.error} retry={resource.reload} />;
  const provider = edit && edit !== "new" ? edit : null;
  return <section className="ai-platform"><div className="ai-section-title"><div><h2>算力渠道</h2><p>AutoDL 容器实例 Pro · 凭据加密保存 · 每队独立实例</p></div>{editable && <button className="primary-button" onClick={() => { setEdit("new"); setError(""); }}><Cpu size={15} />添加算力渠道</button>}</div>
    <div className="ai-notice">先配置 AutoDL 渠道，再到 <Link href="/manage/competitions">赛题管理 → 资源额度</Link> 设置每队相同的 GPU 卡时和参考费用预算。</div><FieldError>{error}</FieldError>
    {edit && <ComputeProviderForm key={provider?.id ?? "new"} provider={provider} busy={busy} onSubmit={save} onCancel={() => setEdit(null)} />}
    <div className="ai-card-grid">{resource.data?.map(p => <article className="ai-channel-card" key={p.id}><div className="ai-card-heading"><div><small>AUTODL / CONTAINER PRO</small><h3>{p.name}</h3></div><span className={`ai-badge ${p.enabled ? "ok" : "off"}`}>{p.enabled ? "已启用" : "已停用"}</span></div><div className="ai-channel-meta"><span>{p.gpu_label} × {p.gpu_count}</span><span>预算单价 ¥{computeCost(p.hourly_price_millis)} / 小时</span><span>凭据已加密</span></div>{editable && <div className="ai-channel-actions" style={{ marginTop: 20 }}><button className="outline-button" disabled={busy} onClick={() => { setEdit(p); setError(""); }}>编辑</button><button className="outline-button" disabled={busy} onClick={() => void test(p.id)}>测试连接</button></div>}</article>)}</div>
    {!resource.data?.length && !edit && <EmptyState title="尚未配置算力渠道" description="添加 AutoDL Pro 渠道后，在题目上统一分配各队额度。" />}
  </section>;
}
