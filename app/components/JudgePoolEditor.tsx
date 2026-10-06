"use client";
import { useState } from "react";
import { api, formatApiError, jsonBody } from "@/app/lib/api";
import { useApiResource } from "@/app/lib/useApiResource";
import { useSession } from "./SessionProvider";

export type JudgePoolStatus = { provider_id: string; remote_id: string; worker_id: string; enabled: boolean;
  idle_seconds: number; boot_seconds: number; state: string; provider_status: string; error: string | null };
const labels: Record<string, string> = { off: "待机 · 有任务自动开机", starting: "启动中", ready: "已就绪", draining: "完成当前测试后重启", stopping: "关机中", error: "等待恢复" };

export function JudgePoolEditor({ problemId, initial, refresh }: { problemId: string; initial: JudgePoolStatus | null; refresh: () => Promise<void> }) {
  const { user } = useSession();
  const providers = useApiResource<{ id: string; name: string }[]>("/compute/manage/providers");
  const [value, setValue] = useState(initial ?? { provider_id: "", remote_id: "", worker_id: "", enabled: true, idle_seconds: 120, boot_seconds: 600 });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [editing, setEditing] = useState(!initial);
  async function save() {
    setBusy(true); setError("");
    try {
      const { provider_id, remote_id, worker_id, enabled, idle_seconds, boot_seconds } = value;
      await api(`/manage/problems/${problemId}/judge`, { method: "PUT", ...jsonBody({ provider_id, remote_id, worker_id, enabled, idle_seconds, boot_seconds }) });
      await refresh(); setEditing(false);
    } catch (e) { setError(formatApiError(e)); } finally { setBusy(false); }
  }
  async function unbind() {
    setBusy(true); setError("");
    try {
      await api(`/manage/problems/${problemId}/judge`, { method: "DELETE" });
      await refresh();
    } catch (e) { setError(formatApiError(e)); } finally { setBusy(false); }
  }
  return <section className="setup-check-panel"><h3>组织方 GPU 自动测评</h3><p>复用已安装私有数据和测评程序的专用实例。队伍发起测试后自动开机，依次运行任务，空闲后关机；独立于队伍训练额度。</p>
    {initial && <p role="status"><strong>{initial.enabled ? labels[initial.state] ?? initial.state : "自动调度已暂停"}</strong> · AutoDL：{initial.provider_status}{initial.error && ` · ${initial.error}`}</p>}
    {initial && !initial.enabled && user?.role === "admin" && <button type="button" className="text-button" disabled={busy} onClick={() => void unbind()}>解除测评绑定（保留实例和历史结果）</button>}
    {user?.role === "admin" && <button type="button" className="text-button" onClick={() => setEditing(!editing)}>{editing ? "收起设置" : "设置自动启停"}</button>}
    {editing && user?.role === "admin" && <div className="form-grid">
      <label className="form-field"><span>算力渠道</span><select value={value.provider_id} onChange={e => setValue({ ...value, provider_id: e.target.value })}><option value="">选择渠道</option>{providers.data?.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}</select></label>
      <label className="form-field"><span>组织方专用实例</span><input value={value.remote_id} placeholder="pro-…" onChange={e => setValue({ ...value, remote_id: e.target.value.trim() })} /></label>
      <label className="form-field"><span>测评端标识</span><input value={value.worker_id} onChange={e => setValue({ ...value, worker_id: e.target.value.trim() })} /></label>
      <label className="form-field"><span>空闲多久关机 · 秒</span><input type="number" min={30} max={600} value={value.idle_seconds} onChange={e => setValue({ ...value, idle_seconds: Number(e.target.value) })} /></label>
      <label className="form-field"><span>最长开机等待 · 秒</span><input type="number" min={60} max={1800} value={value.boot_seconds} onChange={e => setValue({ ...value, boot_seconds: Number(e.target.value) })} /></label>
      <label className="evaluation-toggle"><input type="checkbox" checked={value.enabled} onChange={e => setValue({ ...value, enabled: e.target.checked })} />启用自动调度</label>
      <p>需提前在此实例安装专用测评镜像、私有数据及测评认证文件。平台不会创建新实例。</p><button type="button" className="outline-button" disabled={busy} onClick={() => void save()}>{busy ? "保存中…" : "保存自动启停"}</button>
    </div>}{error && <p role="alert">{error}</p>}
  </section>;
}
