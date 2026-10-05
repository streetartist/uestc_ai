"use client";

import { useState } from "react";
import Link from "next/link";
import { Activity, ArrowUpRight, Cable, Check, Copy, KeyRound, Layers, ListFilter, MessageSquare, RefreshCw, Settings2, ShieldCheck } from "lucide-react";
import { AppShell } from "@/app/components/AppShell";
import { useSession } from "@/app/components/SessionProvider";
import { PageError, PageLoading, EmptyState } from "@/app/components/ui";
import { useApiResource } from "@/app/lib/useApiResource";
import { API_BASE } from "@/app/lib/api";
import { copyText } from "@/app/lib/clipboard";
import { useToast } from "@/app/components/ToastProvider";
import type { AIOverview, AIModel, AIGrant } from "@/app/lib/ai-platform";
import { points } from "@/app/lib/ai-platform";
import { AIKeys, AIUsageLog, AIChannels, AIPlayground } from "./panels";
import { AIQuotaEditor } from "@/app/components/AIQuotaEditor";
import { ComputeProviders, ComputeQuotaCard, ComputeResources } from "./compute-panels";
import type { ComputeOverview } from "@/app/lib/compute";
import { computeCost, gpuHours } from "@/app/lib/compute";
import "./platform.css";

const tabs = [
  { id: "overview", label: "用量概览", icon: Activity }, { id: "models", label: "模型广场", icon: Layers },
  { id: "keys", label: "API 密钥", icon: KeyRound }, { id: "usage", label: "调用记录", icon: ListFilter },
  { id: "playground", label: "对话测试", icon: MessageSquare },
] as const;
const number = (value: number) => value.toLocaleString("zh-CN");

export function QuotaCard({ grant }: { grant: AIGrant }) {
  return <article className="ai-quota-card"><div className="ai-card-heading"><div><small>{grant.competition_name}</small><h3>{grant.team_name}</h3></div><span className={`ai-badge ${grant.enabled ? "ok" : "off"}`}>{grant.enabled ? "可使用" : "已停用"}</span></div>
    <p className="ai-grant-scope">{grant.problem_title ?? "队伍通用额度"}</p>
    {([{ label: "Token 配额", used: grant.tokens_used, max: grant.max_tokens }, { label: "调用次数", used: grant.calls_used, max: grant.max_calls }]).map(item => <div className="ai-quota-meter" key={item.label}><div><span>{item.label}</span><strong>{number(item.used)} <small>/ {number(item.max)}</small></strong></div><progress value={Math.min(item.used, item.max)} max={item.max || 1} /></div>)}
    <div className="ai-card-foot"><span>{grant.allowed_models.length} 个授权模型</span><span>{grant.problem_id ? "本题独立额度" : "队伍共享额度"}</span></div>
  </article>;
}

export default function AIPlatformPage() {
  const { user, loading: sessionLoading } = useSession();
  const { data, loading, error, reload } = useApiResource<AIOverview>(user ? "/ai/overview" : null);
  const [tab, setTab] = useState("overview");
  const compute = useApiResource<ComputeOverview>(user ? "/compute/overview" : null);
  const manage = !!user && ["admin", "organizer"].includes(user.role);
  const reviewer = user?.role === "reviewer";
  const toast = useToast();
  const base = `${API_BASE}/ai/v1`;
  async function copyBase() { try { await copyText(base.startsWith("http") ? base : window.location.origin + base); toast.success("API 地址已复制"); } catch { toast.error("复制失败，请手动复制"); } }
  if (sessionLoading) return <AppShell title="API 平台"><PageLoading /></AppShell>;
  if (!user) return <AppShell title="API 平台" eyebrow="BUILD WITH AI"><div className="ai-welcome"><Cable size={32} /><h2>让创作与模型相连。</h2><p>统一模型入口、队伍 API 密钥、清晰可查的比赛用量。</p><Link className="primary-button" href="/login">登录 API 平台 <ArrowUpRight size={16} /></Link></div></AppShell>;
  const visibleTabs = [...tabs.filter(item => !(reviewer && ["keys", "playground"].includes(item.id))), { id: "compute", label: "算力实例", icon: Activity }, ...(manage ? [{ id: "channels", label: "渠道管理", icon: Cable }, { id: "compute-providers", label: "算力渠道", icon: Cable }, { id: "quotas", label: "通用额度", icon: Settings2 }] : [])];
  return <AppShell title="API 平台" eyebrow="MODEL ACCESS" actions={<button className="outline-button" onClick={() => { void reload(); void compute.reload(); }} disabled={loading}><RefreshCw size={14} />刷新</button>}>
    <div className="ai-platform"><section className="ai-hero"><div><span className="eyebrow"><i />UESTC AI / API</span><h2>一个入口，连接你的 AI。</h2><p>从对话到智能体，使用同一套模型接口。每一次调用，都有迹可循。</p></div><div className="ai-endpoint"><span>OPENAI 兼容地址</span><button onClick={copyBase} title="复制 API 地址"><code>{base}</code><Copy size={15} /></button><small><ShieldCheck size={13} />队伍独立授权 · 密钥隔离 · 用量可查</small></div></section>
    <nav className="ai-tabs" aria-label="API 平台功能">{visibleTabs.map(({ id, label, icon: Icon }) => <button key={id} className={tab === id ? "active" : ""} onClick={() => setTab(id)}><Icon size={15} />{label}</button>)}</nav>
    {error ? <PageError message={error} retry={reload} /> : !data ? <PageLoading /> : <>
      {tab === "overview" && <><div className="ai-stats">{[{ label: "累计调用", value: number(data.summary.requests), note: `${number(data.summary.completed)} 次完成` }, { label: "Token 用量", value: number(data.summary.charged_tokens), note: `输入 ${number(data.summary.input_tokens)} / 输出 ${number(data.summary.output_tokens)}` }, { label: "参考费用", value: points(data.summary.cost_micros), note: "点数 · 按组织方定价统计" }, { label: "平均响应时间", value: `${(data.summary.average_latency_ms / 1000).toFixed(2)}s`, note: "仅统计延迟，不限制使用时长" }].map(item => <article key={item.label}><span>{item.label}</span><strong>{item.value}</strong><small>{item.note}</small></article>)}</div>
      {!!(data.summary.uncertain || data.summary.pending) && <div className="ai-notice">{data.summary.pending} 条调用待结算，{data.summary.uncertain} 条调用未获得完整用量；预占额度计入用量，详情可在调用记录中查看。</div>}
      <div className="ai-section-title"><h2>{reviewer || manage ? "队伍资源" : "我的队伍配额"}</h2><span>{data.grants.length} 项配额 · {new Set(data.grants.map(g => g.team_id)).size} 支队伍</span></div>
      {data.grants.length ? <div className="ai-card-grid">{data.grants.map(g => <QuotaCard key={g.id} grant={g} />)}</div> : <EmptyState title="还没有分配 API 配额" description="加入比赛队伍后，由组织方为队伍分配模型与额度。" />}
      <div className="ai-section-title"><h2>近期调用趋势</h2><span>最近 14 个有调用的日期</span></div><div className="ai-trend">{data.daily.length ? data.daily.map(day => <div key={day.date}><strong>{number(day.requests)}</strong><div className="ai-trend-track"><i style={{ height: `${Math.max(3, day.requests / Math.max(...data.daily.map(d => d.requests), 1) * 100)}%` }} /></div><small>{day.date.slice(5)}</small></div>) : <p>开始使用模型后，这里会展示每天的调用量。</p>}</div></>}
      {tab === "models" && <ModelGallery models={data.models} />}
      {tab === "keys" && <AIKeys grants={data.grants.filter(g => data.key_grant_ids.includes(g.id))} onChange={reload} />}
      {tab === "usage" && <AIUsageLog grants={data.grants} />}
      {tab === "playground" && <AIPlayground models={data.models} onChange={reload} />}
      {tab === "channels" && manage && <AIChannels editable={user.role === "admin"} />}
      {tab === "quotas" && manage && <AIQuotaEditor grants={data.grants} onChange={reload} />}
      {tab === "compute" && <ComputeResources reviewer={reviewer} allowSSH={user.role !== "organizer"} />}
      {tab === "compute-providers" && manage && <ComputeProviders editable={user.role === "admin"} />}
      {tab === "overview" && <><div className="ai-section-title"><div><h2>算力额度</h2><p>已用 GPU 卡时 {gpuHours(compute.data?.summary.gpu_seconds ?? 0)} · 参考运行费用 ¥{computeCost(compute.data?.summary.cost_millis ?? 0)} · 含预占额度</p></div><button className="outline-button" onClick={() => setTab("compute")}>查看算力实例</button></div>{compute.error ? <PageError message={compute.error} retry={compute.reload} /> : compute.data?.grants.length ? <div className="ai-card-grid">{compute.data.grants.map(grant => <ComputeQuotaCard key={grant.id} grant={grant} />)}</div> : <EmptyState title="暂无算力额度" description="组织方可在题目资源额度中统一设置每队算力预算。" />}</>}
    </>}
    </div>
  </AppShell>;
}

function ModelGallery({ models }: { models: AIModel[] }) {
  const [search, setSearch] = useState("");
  return <><div className="ai-section-title"><div><h2>模型广场</h2><p>展示已为队伍授权、且有可用渠道的模型。</p></div><input className="ai-search" placeholder="搜索模型" aria-label="搜索模型" value={search} onChange={e => setSearch(e.target.value)} /></div>
    <div className="ai-card-grid">{models.filter(m => m.id.toLowerCase().includes(search.toLowerCase())).map(m => <article className="ai-model-card" key={m.id}><Layers size={22} /><h3>{m.id}</h3><div className="ai-tags">{m.protocols.map(p => <span key={p}>{p}</span>)}</div><dl><div><dt>输入 / 百万 token</dt><dd>{m.input_price} 点</dd></div><div><dt>输出 / 百万 token</dt><dd>{m.output_price} 点</dd></div></dl><small><Check size={12} />模型名可直接用于 API 请求</small></article>)}</div>
    {!models.length && <EmptyState title="暂无可用模型" description="请等待组织方配置渠道并为队伍授权模型。" />}</>;
}
