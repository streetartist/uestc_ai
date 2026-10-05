"use client";

import { useEffect, useState } from "react";
import { Activity, ExternalLink, FolderOpen, Globe, NotebookPen, X } from "lucide-react";
import { api, API_BASE, formatApiError } from "@/app/lib/api";
import { FieldError } from "@/app/components/ui";
import type { ComputeTools } from "@/app/lib/compute";

const storage = (bytes: number | null) => bytes === null ? "—" : `${(bytes / 1024 ** 3).toFixed(2)} GB`;
const percent = (value: number | null) => value === null ? "—" : `${value.toFixed(1)}%`;

export function ComputeInstanceTools({ instanceId, enabled }: { instanceId: string; enabled: boolean }) {
  // Stopping removes previously displayed private addresses.
  return <InstanceTools key={`${instanceId}:${enabled}`} instanceId={instanceId} enabled={enabled} />;
}

function InstanceTools({ instanceId, enabled }: { instanceId: string; enabled: boolean }) {
  const [view, setView] = useState<"monitor" | "services" | null>(null);
  const [data, setData] = useState<ComputeTools | null>(null);
  const [busy, setBusy] = useState(false), [error, setError] = useState("");
  const endpoint = `${API_BASE}/compute/instances/${instanceId}/tools`;
  useEffect(() => { if (!view) return; const timeout = setTimeout(() => { setView(null); setData(null); }, 60000); return () => clearTimeout(timeout); }, [view, data]);
  async function inspect(selected: "monitor" | "services") {
    if (busy || !enabled) return;
    setView(selected); setData(null); setError(""); setBusy(true);
    try { setData(await api<ComputeTools>(`/compute/instances/${instanceId}/tools`, { method: "POST", cache: "no-store" })); }
    catch (error) { setError(formatApiError(error)); } finally { setBusy(false); }
  }
  const linkProps = { target: "_blank", rel: "noopener noreferrer", referrerPolicy: "no-referrer" as const };
  const monitor = data?.monitor;
  return <div className="compute-tools">
    <strong>快捷工具</strong>
    <div className="compute-tool-grid">
      {enabled ? <><a className="outline-button" href={`${endpoint}/jupyter`} {...linkProps}><NotebookPen size={14} />JupyterLab</a><a className="outline-button" href={`${endpoint}/autopanel`} {...linkProps}><FolderOpen size={14} />AutoPanel</a></> : <><button className="outline-button" disabled><NotebookPen size={14} />JupyterLab</button><button className="outline-button" disabled><FolderOpen size={14} />AutoPanel</button></>}
      <button className="outline-button" disabled={!enabled || busy} onClick={() => void inspect("monitor")}><Activity size={14} />实例监控</button>
      <button className="outline-button" disabled={!enabled || busy} onClick={() => void inspect("services")}><Globe size={14} />自定义服务</button>
    </div>
    {!enabled && <small>实例运行后可使用这些工具。</small>}
    {view && <div className="compute-tool-detail"><div className="ai-card-heading"><strong>{view === "monitor" ? "实例监控" : "自定义服务"}</strong><button className="text-button" aria-label="关闭工具详情" onClick={() => { setView(null); setData(null); setError(""); }}><X size={14} /></button></div>
      {busy && <p role="status">正在读取实例信息…</p>}<FieldError>{error}</FieldError>
      {monitor && view === "monitor" && <>
        {monitor.valid && monitor.stale && <p>最近一次监控快照，数据尚未更新。</p>}
        {monitor.valid ? <dl className="compute-monitor">{[
          ["CPU 用量", percent(monitor.cpu_usage_percent)], ["内存使用率", percent(monitor.mem_usage_percent)],
          ["内存", `${storage(monitor.mem_usage)} / ${storage(monitor.mem_limit)}`],
          ["系统盘", `${storage(monitor.root_fs_used_size)} / ${storage(monitor.root_fs_total_size)}`],
          ["数据盘", `${storage(monitor.data_disk_used_size)} / ${storage(monitor.data_disk_total_size)}`],
        ].map(([name, value]) => <div key={name}><dt>{name}</dt><dd>{value}</dd></div>)}</dl> : <p>监控数据尚未就绪，请稍后刷新。</p>}
        {monitor.valid_at && <small>数据时间：{new Date(monitor.valid_at).toLocaleString("zh-CN")}</small>}
        <div className="ai-button-row"><button className="outline-button" disabled={busy} onClick={() => void inspect("monitor")}>刷新监控</button>{data?.available.autopanel && <a className="outline-button" href={`${endpoint}/autopanel`} {...linkProps}>AutoPanel 完整监控<ExternalLink size={12} /></a>}</div>
      </>}
      {data && view === "services" && <><p>在实例内启动监听 6006 或 6008 端口的应用后，使用对应入口访问。映射地址不代表应用已经启动。</p>
        {data.services.length ? data.services.map(service => <div className="compute-service" key={service.port}><div className="ai-card-heading"><strong>端口 {service.port}</strong><small>{service.protocol.toUpperCase()}</small></div><code>{service.address}</code>{service.url ? <a className="outline-button" href={service.url} {...linkProps}>打开服务<ExternalLink size={12} /></a> : <small>TCP 服务，请在对应客户端中填写此地址。</small>}</div>) : <p>AutoDL 尚未返回可用的自定义服务地址。</p>}
      </>}
    </div>}
  </div>;
}
