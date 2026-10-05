"use client";

import { FormEvent, useRef, useState } from "react";
import { Ban, Copy, Download, KeyRound, LoaderCircle, Pencil, Plus, Send, Square, X } from "lucide-react";
import { api, API_BASE, formatApiError, jsonBody } from "@/app/lib/api";
import { useApiResource } from "@/app/lib/useApiResource";
import { copyText } from "@/app/lib/clipboard";
import { formatBeijing } from "@/app/lib/time";
import { useToast } from "@/app/components/ToastProvider";
import { EmptyState, FieldError, PageError, PageLoading } from "@/app/components/ui";
import type { AIChannel, AIGrant, AIKey, AIModel, AIUsagePage } from "@/app/lib/ai-platform";
import { grantLabel, points } from "@/app/lib/ai-platform";

const num = (value: number) => value.toLocaleString("zh-CN");
const split = (value: string) => value.split(/[,，\n]/).map(v => v.trim()).filter(Boolean);
const date = (value: string) => formatBeijing(value, { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
const statuses: Record<string, string> = { completed: "已完成", failed: "已失败", pending: "待结算", uncertain: "用量待确认" };

function useAction() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const toast = useToast();
  async function run(action: () => Promise<void>, message?: string) {
    if (busy) return;
    setBusy(true); setError("");
    try { await action(); if (message) toast.success(message); }
    catch (e) { setError(formatApiError(e)); }
    finally { setBusy(false); }
  }
  return { busy, error, run };
}

export function AIKeys({ grants, onChange }: { grants: AIGrant[]; onChange: () => Promise<void> }) {
  const { data, loading, error, reload } = useApiResource<AIKey[]>("/ai/keys");
  const action = useAction();
  const [secret, setSecret] = useState("");
  const [target, setTarget] = useState("");
  const toast = useToast();
  async function create(e: FormEvent<HTMLFormElement>) {
    e.preventDefault(); const form = e.currentTarget; const values = new FormData(form);
    await action.run(async () => { const created = await api<AIKey & { token: string }>("/ai/keys", { method: "POST", ...jsonBody({ name: values.get("name"), grant_id: values.get("grant") }) }); setSecret(created.token); form.reset(); await reload(); await onChange(); }, "API 密钥已创建");
  }
  async function copy() { try { await copyText(secret); toast.success("密钥已复制"); } catch { toast.error("复制失败，请手动复制"); } }
  return <><div className="ai-section-title"><div><h2>API 密钥</h2><p>密钥绑定所选题目与队伍，同一额度下的多个密钥共享配额。完整密钥仅在创建时显示。</p></div></div>
    {secret && <div className="ai-secret" role="status"><div><strong>请保存这枚密钥</strong><span>关闭后无法再次查看，可停用后重新创建。</span></div><code>{secret}</code><button className="outline-button" onClick={copy}><Copy size={14} />复制</button><button className="icon-button" aria-label="关闭密钥" onClick={() => setSecret("")}><X size={17} /></button></div>}
    <form className="ai-inline-form" onSubmit={create}><label className="form-field"><span>所属题目与队伍</span><select name="grant" required disabled={!grants.some(g => g.enabled)}>{grants.filter(g => g.enabled).map(g => <option key={g.id} value={g.id}>{grantLabel(g)} · {g.competition_name}</option>)}</select></label><label className="form-field"><span>密钥名称</span><input name="name" required maxLength={80} placeholder="例如：开发调试、正式智能体" /></label><button className="primary-button" disabled={action.busy || !grants.some(g => g.enabled)}><Plus size={15} />创建密钥</button></form><FieldError>{action.error}</FieldError>
    {loading ? <PageLoading /> : error ? <PageError message={error} retry={reload} /> : data?.length ? <div className="ai-table-wrap"><table className="ai-table"><thead><tr><th>密钥</th><th>所属队伍</th><th>状态</th><th>创建时间</th><th>操作</th></tr></thead><tbody>{data.map(key => <tr key={key.id}><td><strong>{key.name}</strong><code>{key.prefix}…</code></td><td>{grants.find(g => g.id === key.grant_id)?.team_name}<small>{grants.find(g => g.id === key.grant_id)?.problem_title ?? "队伍通用额度"}</small></td><td><span className={`ai-badge ${key.enabled ? "ok" : "off"}`}>{key.enabled ? "已启用" : "已停用"}</span></td><td>{date(key.created_at)}</td><td>{key.enabled && (target === key.id ? <div className="ai-button-row"><button className="text-button" disabled={action.busy} onClick={() => action.run(async () => { await api(`/ai/keys/${key.id}`, { method: "DELETE" }); setTarget(""); await reload(); }, "密钥已停用")}>确认停用</button><button className="text-button" onClick={() => setTarget("")}>取消</button></div> : <button className="text-button" onClick={() => setTarget(key.id)}><Ban size={14} />停用</button>)}</td></tr>)}</tbody></table></div> : <EmptyState title="还没有 API 密钥" description="获得队伍配额后，可以在这里创建密钥。" />}
    <section className="ai-code-example"><h3>快速接入</h3><p>将 API 地址和密钥填入兼容 OpenAI 的工具，模型名从「模型广场」选择。</p><pre>{`curl ${API_BASE}/ai/v1/chat/completions \\\n  -H "Authorization: Bearer YOUR_API_KEY" \\\n  -H "Content-Type: application/json" \\\n  -d '{"model":"YOUR_MODEL","messages":[{"role":"user","content":"你好"}],"max_tokens":256}'`}</pre></section></>;
}

export function AIUsageLog({ grants }: { grants: AIGrant[] }) {
  const [filters, setFilters] = useState({ grant: "", model: "", status: "", from: "", to: "", page: 1 });
  const query = new URLSearchParams({ page: String(filters.page), limit: "25" });
  if (filters.grant) query.set("grant_id", filters.grant);
  if (filters.model) query.set("model", filters.model);
  if (filters.status) query.set("status", filters.status);
  if (filters.from) query.set("from", `${filters.from}T00:00:00+08:00`);
  if (filters.to) query.set("to", `${filters.to}T23:59:59.999999+08:00`);
  const { data, loading, error, reload } = useApiResource<AIUsagePage>(`/ai/usage?${query}`);
  const toast = useToast();
  const action = useAction();
  function change(name: keyof typeof filters, value: string) { setFilters(v => ({ ...v, [name]: value, page: 1 })); }
  async function download() {
    await action.run(async () => {
      const all = []; let page = 1; let total = 0;
      do { const exportQuery = new URLSearchParams(query); exportQuery.set("limit", "200"); exportQuery.set("page", String(page++)); const result = await api<AIUsagePage>(`/ai/usage?${exportQuery}`); all.push(...result.items); total = result.total; } while (all.length < total);
      const blob = new Blob([JSON.stringify(all, null, 2)], { type: "application/json" }); const url = URL.createObjectURL(blob); const link = document.createElement("a"); link.href = url; link.download = "ai-usage.json"; link.click(); URL.revokeObjectURL(url); toast.success("调用记录已导出");
    });
  }
  return <><div className="ai-section-title"><div><h2>调用记录</h2><p>不保存对话内容与上游密钥。预占用量与已确认的输入、输出用量分开显示。</p></div><div className="ai-button-row"><button className="outline-button" onClick={reload}>刷新</button><button className="outline-button" onClick={download} disabled={action.busy}><Download size={14} />导出</button></div></div>
    <div className="ai-filters"><select aria-label="筛选队伍" value={filters.grant} onChange={e => change("grant", e.target.value)}><option value="">所有队伍</option>{grants.map(g => <option key={g.id} value={g.id}>{grantLabel(g)}</option>)}</select><input aria-label="筛选模型" placeholder="模型名称" value={filters.model} onChange={e => change("model", e.target.value)} /><select aria-label="筛选状态" value={filters.status} onChange={e => change("status", e.target.value)}><option value="">所有状态</option>{Object.entries(statuses).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select><input aria-label="开始日期" type="date" value={filters.from} onChange={e => change("from", e.target.value)} /><input aria-label="结束日期" type="date" value={filters.to} onChange={e => change("to", e.target.value)} /></div><FieldError>{action.error}</FieldError>
    {loading ? <PageLoading /> : error ? <PageError message={error} retry={reload} /> : data?.items.length ? <><div className="ai-table-wrap"><table className="ai-table"><thead><tr><th>时间 / 队伍</th><th>模型</th><th>状态</th><th>输入 / 输出</th><th>计入配额</th><th>费用 / 延迟</th></tr></thead><tbody>{data.items.map(u => <tr key={u.id}><td>{date(u.created_at)}<small>{u.team_name}</small><small>{u.problem_title ?? "队伍通用额度"}</small></td><td><strong>{u.model}</strong><small>{u.evaluation_run_id ? "自动评测" : "API 调用"}</small></td><td><span className={`ai-badge ${u.status === "completed" ? "ok" : "off"}`}>{statuses[u.status]}</span>{u.error_code && <small>{u.error_code}</small>}{u.attempts.length > 1 && <small>已尝试 {u.attempts.length} 个渠道密钥</small>}</td><td>{u.input_tokens === null ? "—" : num(u.input_tokens)} / {u.output_tokens === null ? "—" : num(u.output_tokens)}{!!u.cached_tokens && <small>缓存 {num(u.cached_tokens)}</small>}</td><td>{num(u.charged_tokens)} token</td><td>{points(u.charged_cost_micros)} 点<small>{u.latency_ms === null ? "—" : `${(u.latency_ms / 1000).toFixed(2)}s`}</small></td></tr>)}</tbody></table></div><div className="ai-pagination"><span>共 {num(data.total)} 条 · 第 {data.page} 页</span><button className="outline-button" disabled={data.page === 1} onClick={() => setFilters(v => ({ ...v, page: v.page - 1 }))}>上一页</button><button className="outline-button" disabled={data.page * data.limit >= data.total} onClick={() => setFilters(v => ({ ...v, page: v.page + 1 }))}>下一页</button></div></> : <EmptyState title="暂无匹配的调用" description="使用模型后，这里会记录每次调用的用量与状态。" />}</>;
}

export function AIChannels({ editable }: { editable: boolean }) {
  const { data, loading, error, reload } = useApiResource<AIChannel[]>("/ai/manage/channels");
  const [edit, setEdit] = useState<AIChannel | "new" | null>(null);
  const action = useAction();
  return <><div className="ai-section-title"><div><h2>渠道管理</h2><p>配置上游服务，多密钥轮换，按优先级与权重分配请求。只有管理员能修改渠道。</p></div>{editable && <button className="primary-button" onClick={() => setEdit("new")}><Plus size={15} />新增渠道</button>}</div>
    {edit && <ChannelForm key={edit === "new" ? "new" : edit.id} channel={edit === "new" ? null : edit} close={() => setEdit(null)} saved={async () => { setEdit(null); await reload(); }} />}
    <FieldError>{action.error}</FieldError>{loading ? <PageLoading /> : error ? <PageError message={error} retry={reload} /> : !data?.length ? <EmptyState title="尚未配置渠道" description="新增上游地址与密钥后，即可为参赛队伍提供模型服务。" /> : <div className="ai-channel-list">{data.map(c => <article className="ai-channel-card" key={c.id}><div className="ai-card-heading"><div><h3>{c.name}</h3><small>{c.base_url}</small></div><span className={`ai-badge ${c.enabled ? "ok" : "off"}`}>{c.enabled ? "已启用" : "已停用"}</span></div><div className="ai-channel-meta"><span>{c.protocol.toUpperCase()}</span><span>{c.key_count} 个上游密钥</span><span>优先级 {c.priority} · 权重 {c.weight}</span><span>{c.last_test ? (c.last_test.ok ? "最近检测连接正常" : "最近检测连接失败") : "尚未检测"}</span></div><div className="ai-tags">{Object.keys(c.models).map(model => <button key={model} disabled={!editable || action.busy} className={c.disabled_models.includes(model) ? "disabled" : ""} title="点击启用或停用模型" onClick={() => action.run(async () => { await api(`/ai/manage/channels/${c.id}`, { method: "PATCH", ...jsonBody({ disabled_models: c.disabled_models.includes(model) ? c.disabled_models.filter(m => m !== model) : [...c.disabled_models, model] }) }); await reload(); })}>{model}{c.disabled_models.includes(model) ? " · 停用" : ""}</button>)}</div>{editable && <div className="ai-channel-actions"><button className="text-button" onClick={() => setEdit(c)}><Pencil size={13} />编辑</button><button className="text-button" disabled={action.busy} onClick={() => action.run(async () => { await api(`/ai/manage/channels/${c.id}/test`, { method: "POST" }); await reload(); })}>检测连接</button><button className="text-button" disabled={action.busy} onClick={() => action.run(async () => { await api(`/ai/manage/channels/${c.id}`, { method: "PATCH", ...jsonBody({ enabled: !c.enabled }) }); await reload(); })}>{c.enabled ? "停用渠道" : "启用渠道"}</button>{!!c.last_test?.models.length && <button className="text-button" disabled={action.busy} onClick={() => action.run(async () => { const models = { ...c.models }; for (const m of c.last_test!.models) if (!models[m]) models[m] = m; await api(`/ai/manage/channels/${c.id}`, { method: "PATCH", ...jsonBody({ models }) }); await reload(); }, "发现的模型已导入")}>导入发现的 {c.last_test.models.length} 个模型</button>}</div>}</article>)}</div>}</>;
}

function ChannelForm({ channel, close, saved }: { channel: AIChannel | null; close: () => void; saved: () => Promise<void> }) {
  const action = useAction();
  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault(); const fields = new FormData(e.currentTarget);
    const models: Record<string, string> = {};
    for (const line of split(String(fields.get("models")))) { const index = line.indexOf("="); const alias = index < 0 ? line : line.slice(0, index).trim(); models[alias] = index < 0 ? line : line.slice(index + 1).trim(); }
    const keys = String(fields.get("keys")).split("\n").map(v => v.trim()).filter(Boolean);
    const body = { name: fields.get("name"), base_url: fields.get("url"), protocol: fields.get("protocol"), models,
      disabled_models: (channel?.disabled_models ?? []).filter(m => m in models),
      weight: Number(fields.get("weight")), priority: Number(fields.get("priority")),
      input_price: Number(fields.get("input_price")), output_price: Number(fields.get("output_price")), ...(keys.length ? { api_keys: keys } : {}) };
    await action.run(async () => { await api(`/ai/manage/channels${channel ? "/" + channel.id : ""}`, { method: channel ? "PATCH" : "POST", ...jsonBody(body) }); await saved(); }, "渠道已保存");
  }
  return <form className="ai-form-panel" onSubmit={submit}><div className="ai-section-title"><h3>{channel ? "编辑渠道" : "新增渠道"}</h3><button className="icon-button" type="button" aria-label="关闭渠道表单" onClick={close}><X size={17} /></button></div><div className="ai-form-grid"><label className="form-field"><span>渠道名称</span><input name="name" defaultValue={channel?.name} required maxLength={80} placeholder="例如：比赛模型服务" /></label><label className="form-field"><span>接口协议</span><select name="protocol" defaultValue={channel?.protocol ?? "openai"}><option value="openai">OpenAI 兼容</option><option value="anthropic">Anthropic Messages</option><option value="custom">自定义完整地址</option></select></label><label className="form-field ai-wide"><span>上游地址</span><input name="url" type="url" required defaultValue={channel?.base_url} placeholder="https://模型服务地址/v1" /><small>填写包含 /v1 的 API 基址；自定义协议填写完整请求地址。</small></label><label className="form-field"><span>上游密钥 · 每行一个</span><textarea name="keys" rows={4} required={!channel} autoComplete="off" spellCheck={false} placeholder={channel ? "留空保留现有密钥；填写则全部替换" : "填写服务商 API 密钥"} /></label><label className="form-field"><span>可用模型 · 每行一个</span><textarea name="models" rows={4} defaultValue={Object.entries(channel?.models ?? {}).map(([alias, model]) => alias === model ? model : `${alias}=${model}`).join("\n")} placeholder={"model-name\n比赛模型=upstream-model"} /><small>可用「展示名称=上游模型名称」设置模型别名。</small></label>{[{ name: "priority", label: "优先级", value: channel?.priority ?? 0 }, { name: "weight", label: "路由权重", value: channel?.weight ?? 1 }, { name: "input_price", label: "输入单价 · 点 / 百万 token", value: channel?.input_price ?? 0 }, { name: "output_price", label: "输出单价 · 点 / 百万 token", value: channel?.output_price ?? 0 }].map(field => <label className="form-field" key={field.name}><span>{field.label}</span><input type="number" name={field.name} min={field.name === "weight" ? 1 : 0} step={field.name.includes("price") ? "any" : 1} defaultValue={field.value} required /></label>)}</div><FieldError>{action.error}</FieldError><div className="ai-button-row"><button className="primary-button" disabled={action.busy}>保存渠道</button><button className="outline-button" type="button" onClick={close}>取消</button></div></form>;
}

export function AIPlayground({ models, onChange }: { models: AIModel[]; onChange: () => Promise<void> }) {
  const [model, setModel] = useState(""); const [token, setToken] = useState(""); const [prompt, setPrompt] = useState("");
  const [output, setOutput] = useState(""); const [busy, setBusy] = useState(false); const [error, setError] = useState("");
  const abort = useRef<AbortController | null>(null);
  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault(); if (busy) return;
    setBusy(true); setError(""); setOutput(""); const controller = new AbortController(); abort.current = controller;
    try {
      const response = await fetch(`${API_BASE}/ai/v1/chat/completions`, { method: "POST", headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` }, body: JSON.stringify({ model: model || models[0]?.id, messages: [{ role: "user", content: prompt }], max_tokens: 512, stream: true }), signal: controller.signal });
      if (!response.ok) { const result = await response.json(); throw new Error(result.error?.message || "模型调用失败"); }
      const reader = response.body?.getReader(); if (!reader) throw new Error("未收到流式响应");
      const decoder = new TextDecoder(); let buffer = "";
      while (true) { const part = await reader.read(); if (part.done) break; buffer += decoder.decode(part.value, { stream: true }); let index;
        while ((index = buffer.indexOf("\n")) !== -1) { const line = buffer.slice(0, index).trim(); buffer = buffer.slice(index + 1); if (!line.startsWith("data:")) continue; const raw = line.slice(5).trim(); if (!raw || raw === "[DONE]") continue; const event = JSON.parse(raw); if (event.error) throw new Error(event.error.message || "模型调用中断"); const text = event.choices?.[0]?.delta?.content; if (typeof text === "string") setOutput(value => value + text); }
      }
    } catch (e) { if (e instanceof Error && e.name === "AbortError") setError("已停止接收。未完成结算的调用会保留预占额度。"); else setError(formatApiError(e)); }
    finally { abort.current = null; setBusy(false); await onChange(); }
  }
  return <><div className="ai-section-title"><div><h2>对话测试</h2><p>使用真实队伍密钥测试模型，调用计入队伍配额。密钥只保留在当前页面。</p></div></div><div className="ai-playground"><form className="ai-form-panel" onSubmit={submit}><label className="form-field"><span>模型</span><select value={model || models[0]?.id || ""} onChange={e => setModel(e.target.value)} required>{models.filter(m => m.protocols.includes("chat/completions")).map(m => <option key={m.id}>{m.id}</option>)}</select></label><label className="form-field"><span>API 密钥</span><input type="password" value={token} onChange={e => setToken(e.target.value)} autoComplete="off" required placeholder="粘贴 sk-contest-… 密钥" /></label><label className="form-field"><span>发送内容</span><textarea rows={8} value={prompt} onChange={e => setPrompt(e.target.value)} required placeholder="向模型提出一个问题…" /></label><FieldError>{error}</FieldError><div className="ai-button-row"><button className="primary-button" disabled={busy || !models.length}>{busy ? <LoaderCircle className="spin" size={14} /> : <Send size={14} />}{busy ? "正在生成" : "发送测试"}</button>{busy && <button className="outline-button" type="button" onClick={() => abort.current?.abort()}><Square size={13} />停止</button>}</div></form><section className="ai-response" aria-live="polite"><span className="eyebrow">MODEL RESPONSE</span>{output ? <div>{output}</div> : <div className="ai-response-empty"><KeyRound size={28} /><h3>从一次对话开始。</h3><p>选择模型并发送内容，回答会在这里逐步显示。</p></div>}</section></div></>;
}
