"use client";

import Link from "next/link";
import { FormEvent, useState } from "react";
import { RefreshCw, Save, Download, Upload, CheckCircle2, CircleAlert } from "lucide-react";
import { api, API_BASE, formatApiError, jsonBody } from "@/app/lib/api";
import { useApiResource } from "@/app/lib/useApiResource";
import type { EvaluationConfig, Problem } from "@/app/lib/domain";
import type { AIChannel, AIQuotaConfig } from "@/app/lib/ai-platform";
import { EvaluationConfigEditor } from "./EvaluationConfigEditor";
import { FieldError, PageError, PageLoading } from "./ui";
import { useToast } from "./ToastProvider";
import "./problem-setup.css";
import { JudgePoolEditor, type JudgePoolStatus } from "./JudgePoolEditor";

type Scene = { id: string; label: string; difficulty: string; task_id: string; world_seed: number | string; max_steps: number; goals: string[] };
type RobotScene = { id: string; label: string; difficulty: string; task: "lift" | "place" | "stack"; seed: number; max_steps: number; hold_steps: number; target?: number[] };
type LiberoScene = { id: string; label: string; difficulty: string; suite: string; task_id: number; task_name: string; init_state_id: number; seed: number; max_steps: number };
type Runtime = { image: string; agent_image: string; scenarios: unknown[]; execution?: "docker" | "autodl-native" };
type Compute = { provider_id: string; enabled: boolean; max_gpu_seconds: number; max_cost_millis: number | null };
type Provider = { id: string; name: string; enabled: boolean; gpu_label: string; gpu_count: number; hourly_price_millis: number };
type RuntimeOption = { problem_id: string; name: string; adapter: string; image: string; agent_image: string; execution?: "docker" | "autodl-native" };
type Config = { evaluation_config: EvaluationConfig; runtime: Runtime | null; ai: AIQuotaConfig | null; compute: Compute | null };
type Check = { key: string; label: string; state: "ready" | "blocked" | "warning" | "unused"; detail: string };
type Setup = { config: Config; checks: Check[]; ready: boolean; judge?: JudgePoolStatus | null };
const defaultAI: AIQuotaConfig = { enabled: true, allowed_models: [], allowed_channels: [], max_calls: 1000, max_tokens: 1000000, max_cost_micros: null, max_output_tokens: 4096, requests_per_minute: 60, max_concurrent: 2 };

export function ProblemSetupEditor({ problemId, saved }: { problemId: string; saved: () => Promise<void> }) {
  const resource = useApiResource<Setup>(`/manage/problems/${problemId}/setup`);
  if (!resource.data && resource.loading) return <PageLoading />;
  if (resource.error) return <PageError message={resource.error} retry={resource.reload} />;
  if (!resource.data) return null;
  return <SetupForm key={JSON.stringify(resource.data.config)} problemId={problemId} initial={resource.data} refresh={resource.reload} saved={saved} />;
}

function SetupForm({ problemId, initial, refresh, saved }: { problemId: string; initial: Setup; refresh: () => Promise<void>; saved: () => Promise<void> }) {
  const [config, setConfig] = useState<Config>(initial.config);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [sceneError, setSceneError] = useState("");
  const [advanced, setAdvanced] = useState(false);
  const channels = useApiResource<AIChannel[]>("/ai/manage/channels");
  const providers = useApiResource<Provider[]>("/compute/manage/providers");
  const runtimes = useApiResource<RuntimeOption[]>("/manage/problem-runtimes");
  const toast = useToast();
  const active = config.evaluation_config.adapter ? config.evaluation_config as Extract<EvaluationConfig, { adapter: string }> : null;
  const minecraft = active?.adapter === "minecraft-agent-v1" && active.task === "open-world";
  const robot = active?.adapter === "robot-arm-agent-v1";
  const libero = active?.adapter === "libero-agent-v1";
  const allowedChannels = channels.data?.filter(c => c.enabled && (!config.ai?.allowed_channels.length || config.ai.allowed_channels.includes(c.id))) ?? [];
  const models = [...new Set([...allowedChannels.flatMap(c => Object.keys(c.models).filter(m => !c.disabled_models.includes(m))), ...(config.ai?.allowed_models ?? [])])].sort();
  function setRuntime(change: Partial<Runtime>) { setConfig(c => ({ ...c, runtime: { image: "", agent_image: "", scenarios: [], ...c.runtime, ...change } })); }
  function setAI(change: Partial<AIQuotaConfig>) { setConfig(c => ({ ...c, ai: { ...defaultAI, ...c.ai, ...change } })); }
  function setCompute(change: Partial<Compute>) { setConfig(c => ({ ...c, compute: { provider_id: "", enabled: true, max_gpu_seconds: 36000, max_cost_millis: null, ...c.compute, ...change } })); }
  function setScene(index: number, change: Partial<Scene>) {
    setRuntime({ scenarios: config.runtime!.scenarios.map((s, i) => i === index ? { ...s as Scene, ...change } : s) });
  }
  function setRobotScene(index: number, change: Partial<RobotScene>) {
    setRuntime({ scenarios: config.runtime!.scenarios.map((s, i) => i === index ? { ...s as RobotScene, ...change } : s) });
  }
  function setLiberoScene(index: number, change: Partial<LiberoScene>) {
    setRuntime({ scenarios: config.runtime!.scenarios.map((s, i) => i === index ? { ...s as LiberoScene, ...change } : s) });
  }
  function replaceRobotScenes(scenarios: RobotScene[]) {
    setConfig(c => ({ ...c, runtime: { image: "", agent_image: "", ...c.runtime, scenarios }, evaluation_config: active ? { ...active, resources: { ...active.resources, episodes: scenarios.length || 1 } } : c.evaluation_config }));
  }
  async function submit(e: FormEvent) {
    e.preventDefault(); setError(""); setBusy(true);
    try {
      await api(`/manage/problems/${problemId}/setup`, { method: "PUT", ...jsonBody({ evaluation_config: config.evaluation_config, runtime: config.runtime, ...(config.ai ? { ai: config.ai } : {}), ...(config.compute ? { compute: config.compute } : {}) }) });
      await refresh(); await saved(); toast.success("本题环境、测试规则与统一额度已保存");
    } catch (e) { setError(formatApiError(e)); } finally { setBusy(false); }
  }
  async function loadScenes(file?: File) {
    if (!file) return;
    setSceneError("");
    try {
      if (file.size > 1024 * 1024) throw new Error("场景文件不能超过 1 MB。");
      const data: unknown = JSON.parse(await file.text());
      if (!Array.isArray(data)) throw new Error("场景文件须为 JSON 列表。");
      setRuntime({ scenarios: data });
    } catch (e) { setSceneError(formatApiError(e)); }
  }
  return <div className="problem-setup">
    <section className="setup-check-panel"><div className="ai-section-title"><div><h2>环境与资源</h2><p>为本题统一设置运行环境和每队额度，已有用量保留，新队伍自动适用。</p></div><button type="button" className="outline-button" onClick={() => void refresh()} disabled={busy}><RefreshCw size={14} />检查状态</button></div>
      <div className="setup-checks">{initial.checks.map(check => <div key={check.key} className={`setup-check ${check.state}`}><span>{check.state === "ready" ? <CheckCircle2 size={16} /> : <CircleAlert size={16} />}{check.label}<small>{({ ready: "已就绪", blocked: "待配置", warning: "需确认", unused: "未启用" })[check.state]}</small></span><p>{check.detail}</p></div>)}</div>
      <p className="evaluation-note"><strong>{initial.ready ? "当前配置检查通过。" : "尚有未完成项，请补齐后开放正式测试。"}</strong> 状态反映已保存配置；接口连通与真实数据基线仍需实际验收。队伍自用 AutoDL 与平台指标测试分别计量。</p>
    </section>
    {initial.config.runtime?.execution === "autodl-native" && <JudgePoolEditor key={JSON.stringify(initial.judge)} problemId={problemId} initial={initial.judge ?? null} refresh={refresh} />}
    <form className="manage-form setup-form" onSubmit={submit}>
      <EvaluationConfigEditor value={config.evaluation_config} onChange={evaluation_config => setConfig(c => ({ ...c, evaluation_config, runtime: evaluation_config.adapter === c.evaluation_config.adapter ? c.runtime : null }))} />
      {active && <fieldset className="evaluation-editor"><legend>指标测试环境</legend><p className="evaluation-note">依赖与评测程序放在固定镜像中，私有场景只发给平台测评端。保存后自动用于本题的后续测试。</p>
        {active.adapter === "classification-v1" && <label className="form-field"><span>运行方式</span><select value={config.runtime?.execution ?? "docker"} onChange={e => setRuntime({ execution: e.target.value as Runtime["execution"], image: "", agent_image: "", scenarios: [] })}><option value="docker">独立 Docker 测评节点</option><option value="autodl-native">组织方 AutoDL 专用测评实例</option></select><small>AutoDL 测评实例由组织方管理，选手的训练实例单独配置。</small></label>}
        <label className="form-field"><span>复用已配置环境</span><select value="" onChange={e => {
          const item = runtimes.data?.find(r => r.problem_id === e.target.value);
          if (item) setRuntime({ image: item.image, agent_image: item.agent_image, execution: item.execution ?? "docker" });
        }}><option value="">选择已有题目的可信镜像…</option>{runtimes.data?.filter(r => r.adapter === active.adapter).map(r => <option key={r.problem_id} value={r.problem_id}>{r.name}</option>)}</select><small>仅复用镜像，本题测试场景独立设置。</small></label>
        <button type="button" className="text-button" onClick={() => setAdvanced(!advanced)}>{advanced ? "收起镜像设置" : "设置镜像 / 查看当前镜像"}</button>
        {advanced && <div className="form-grid"><label className="form-field"><span>{config.runtime?.execution === "autodl-native" ? "AutoDL 私有测评镜像编号" : minecraft ? "可信 MC 控制器镜像" : robot || libero ? "可信机械臂仿真镜像" : "可信评测器镜像"}</span><input value={config.runtime?.image ?? ""} onChange={e => setRuntime({ image: e.target.value.trim() })} placeholder={config.runtime?.execution === "autodl-native" ? "image-…" : "镜像名@sha256:… 或 sha256:…"} /><small>管理员可登记新镜像，组织者可复用已登记镜像。</small></label>{(minecraft || robot || libero) && <label className="form-field"><span>独立智能体镜像</span><input value={config.runtime?.agent_image ?? ""} onChange={e => setRuntime({ agent_image: e.target.value.trim() })} placeholder="镜像名@sha256:…" /></label>}</div>}
        {config.runtime?.execution === "autodl-native" && <p className="evaluation-note">导入数据集配置 JSON，每项填写 dataset 与 manifest_sha256。正式深度数据和隐藏标签存放在测评实例的私有目录，网页不保存标签。</p>}
        <div className="setup-scene-heading"><strong>私有测试场景 · {config.runtime?.scenarios.length ?? 0} 个</strong><label className="outline-button setup-file"><Upload size={14} />导入场景 JSON<input type="file" accept=".json,application/json" onChange={e => void loadScenes(e.target.files?.[0])} /></label></div>
        {minecraft && <div className="setup-scenes">{config.runtime?.scenarios.map((raw, i) => {
          const scene = raw as Partial<Scene>;
          if (!raw || typeof raw !== "object" || !Array.isArray(scene.goals)) return <p key={i}>场景 {i + 1} 格式不正确，请重新导入。</p>;
          return <div className="setup-scene" key={i}><div className="form-grid"><label className="form-field"><span>场景名称</span><input value={scene.label ?? ""} onChange={e => setScene(i, { label: e.target.value })} /></label><label className="form-field"><span>难度</span><select value={scene.difficulty ?? "beginner"} onChange={e => setScene(i, { difficulty: e.target.value })}><option value="beginner">入门</option><option value="intermediate">进阶</option><option value="challenge">挑战</option></select></label><label className="form-field"><span>世界种子</span><input value={scene.world_seed ?? ""} onChange={e => setScene(i, { world_seed: e.target.value })} /></label><label className="form-field"><span>最大行动步数</span><input type="number" min={1} max={3000} value={scene.max_steps ?? 600} onChange={e => setScene(i, { max_steps: Number(e.target.value) })} /></label></div><label className="form-field"><span>收集目标 · 用逗号分隔</span><input defaultValue={scene.goals.join(", ")} onBlur={e => setScene(i, { goals: e.target.value.split(/[,，]/).map(s => s.trim()).filter(Boolean) })} placeholder="log, crafting_table, wooden_pickaxe" /></label><button type="button" className="text-button" onClick={() => setRuntime({ scenarios: config.runtime!.scenarios.filter((_, n) => n !== i) })}>移除场景</button></div>;
        })}<button type="button" className="outline-button" disabled={(config.runtime?.scenarios.length ?? 0) >= 30} onClick={() => setRuntime({ scenarios: [...(config.runtime?.scenarios ?? []), { id: `scene-${crypto.randomUUID().slice(0, 8)}`, label: `场景 ${(config.runtime?.scenarios.length ?? 0) + 1}`, difficulty: "beginner", task_id: "open-ended", world_seed: 42, max_steps: 600, goals: ["log"] }] })}>添加场景</button><small>场景数量须与上面的“每次测试的场景数量”一致。</small></div>}
        <FieldError>{sceneError}</FieldError>
        {robot && <div className="setup-scenes"><p className="evaluation-note">Panda 机械臂 · 20 Hz 物理控制 · 相机观察。私有种子与物体状态只保留在仿真端；连续满足目标才计为成功。</p>{config.runtime?.scenarios.map((raw, i) => {
          const scene = raw as Partial<RobotScene>;
          if (!raw || typeof raw !== "object") return <p key={i}>场景格式不正确，请重新导入。</p>;
          return <div className="setup-scene" key={i}><div className="form-grid"><label className="form-field"><span>场景名称</span><input value={scene.label ?? ""} onChange={e => setRobotScene(i, { label: e.target.value })} /></label><label className="form-field"><span>任务</span><select value={scene.task ?? "lift"} onChange={e => {
            const task = e.target.value as RobotScene["task"];
            const next = { ...scene, task } as RobotScene;
            if (task === "place") next.target = [0.1, 0.1]; else delete next.target;
            setRuntime({ scenarios: config.runtime!.scenarios.map((s, n) => n === i ? next : s) });
          }}><option value="lift">抓取并抬升</option><option value="place">指定位置放置</option><option value="stack">红色积木堆叠到绿色积木</option></select></label><label className="form-field"><span>布局种子</span><input type="number" min={0} max={2147483647} value={scene.seed ?? 42} onChange={e => setRobotScene(i, { seed: Number(e.target.value) })} /></label><label className="form-field"><span>最大物理步数</span><input type="number" min={10} max={3000} value={scene.max_steps ?? 600} onChange={e => setRobotScene(i, { max_steps: Number(e.target.value) })} /></label><label className="form-field"><span>目标连续稳定步数</span><input type="number" min={5} max={100} value={scene.hold_steps ?? 10} onChange={e => setRobotScene(i, { hold_steps: Number(e.target.value) })} /></label><label className="form-field"><span>难度</span><select value={scene.difficulty ?? "beginner"} onChange={e => setRobotScene(i, { difficulty: e.target.value })}><option value="beginner">入门</option><option value="intermediate">进阶</option><option value="challenge">挑战</option></select></label>{scene.task === "place" && [0, 1].map(axis => <label className="form-field" key={axis}><span>放置目标 {axis ? "Y" : "X"} · 米</span><input type="number" min={-0.25} max={0.25} step="0.01" value={scene.target?.[axis] ?? 0.1} onChange={e => setRobotScene(i, { target: [0, 1].map(n => n === axis ? Number(e.target.value) : scene.target?.[n] ?? 0.1) })} /></label>)}</div><button type="button" className="text-button" onClick={() => replaceRobotScenes(config.runtime!.scenarios.filter((_, n) => n !== i) as RobotScene[])}>移除场景</button></div>;
        })}<button type="button" className="outline-button" disabled={(config.runtime?.scenarios.length ?? 0) >= 30} onClick={() => replaceRobotScenes([...(config.runtime?.scenarios ?? []) as RobotScene[], { id: `arm-${crypto.randomUUID().slice(0, 8)}`, label: `机械臂场景 ${(config.runtime?.scenarios.length ?? 0) + 1}`, difficulty: "beginner", task: "lift", seed: 42, max_steps: 600, hold_steps: 10 }])}>添加机械臂场景</button></div>}
        {libero && <div className="setup-scenes">{config.runtime?.scenarios.map((raw, i) => {
          const scene = raw as Partial<LiberoScene>;
          if (!raw || typeof raw !== "object" || !scene.suite || !scene.task_name) return <p key={i}>场景 {i + 1} 格式不正确，请导入 LIBERO 场景配置。</p>;
          return <div className="setup-scene" key={scene.id ?? i}><strong>{scene.label}</strong><p>{scene.suite} / {scene.task_id} · {scene.task_name.replaceAll("_", " ")}</p><div className="form-grid"><label className="form-field"><span>官方初始状态编号</span><input type="number" min={0} max={49} value={scene.init_state_id ?? 0} onChange={e => setLiberoScene(i, { init_state_id: Number(e.target.value) })} /></label><label className="form-field"><span>环境种子</span><input type="number" min={0} max={2147483647} value={scene.seed ?? 42} onChange={e => setLiberoScene(i, { seed: Number(e.target.value) })} /></label><label className="form-field"><span>最大控制步数</span><input type="number" min={1} max={2000} value={scene.max_steps ?? 600} onChange={e => setLiberoScene(i, { max_steps: Number(e.target.value) })} /></label></div></div>;
        })}<p className="evaluation-note">任务必须来自已登记的 LIBERO 范围，成功由原生判定器判断；任务、初始状态和种子只发给可信控制器。首次测试后不可更改场景。</p></div>}
        {!minecraft && !robot && !libero && config.runtime?.scenarios.length ? <p className="evaluation-note">场景作为 /input/scenarios.json 交给可信评测器，评测器需自行隔离队伍程序。</p> : null}
        {!config.runtime && <p className="evaluation-note">未指定题目环境，继续使用测评端原有镜像配置。</p>}
      </fieldset>}
      <fieldset className="evaluation-editor"><legend>模型 API · 每队统一额度</legend><label className="evaluation-toggle"><input type="checkbox" checked={config.ai?.enabled ?? false} onChange={e => e.target.checked || initial.config.ai ? setAI({ enabled: e.target.checked }) : setConfig(c => ({ ...c, ai: null }))} />提供模型 API</label>
        {config.ai && <><label className="form-field"><span>模型渠道</span><select value={config.ai.allowed_channels.length === 1 ? config.ai.allowed_channels[0] : ""} onChange={e => setAI({ allowed_channels: e.target.value ? [e.target.value] : [] })}><option value="">全部启用渠道</option>{channels.data?.map(c => <option key={c.id} value={c.id}>{c.name}{c.enabled ? "" : " · 已停用"}</option>)}</select>{config.ai.allowed_channels.length > 1 && <small>当前保留 {config.ai.allowed_channels.length} 个渠道；重新选择可改为单个渠道。</small>}</label>
          <div className="evaluation-metric-picker"><strong>允许模型</strong><div>{models.map(model => <label key={model}><input type="checkbox" checked={config.ai!.allowed_models.includes(model)} onChange={e => setAI({ allowed_models: e.target.checked ? [...config.ai!.allowed_models, model] : config.ai!.allowed_models.filter(m => m !== model) })} /><span>{model}</span></label>)}</div>{!models.length && <p>暂无模型，请先在 API 平台配置渠道。</p>}</div>
          <div className="form-grid">{([ ["max_tokens", "每队 Token 总额", 0], ["max_calls", "每队总调用次数", 0], ["max_output_tokens", "单次最大输出 Token", 1], ["requests_per_minute", "每分钟调用上限", 1], ["max_concurrent", "同时调用上限", 1] ] as const).map(([key, label, min]) => <label className="form-field" key={key}><span>{label}</span><input type="number" min={min} step={1} required value={config.ai![key]} onChange={e => setAI({ [key]: Number(e.target.value) })} /></label>)}<label className="form-field"><span>每队费用额度 · 点（可选）</span><input type="number" min={0} step="any" value={config.ai.max_cost_micros == null ? "" : config.ai.max_cost_micros / 1e6} onChange={e => setAI({ max_cost_micros: e.target.value === "" ? null : Math.round(Number(e.target.value) * 1e6) })} placeholder="留空不限制" /></label></div>
        </>}
      </fieldset>
      <fieldset className="evaluation-editor"><legend>队伍自用算力 · AutoDL 与 SSH</legend><label className="evaluation-toggle"><input type="checkbox" checked={config.compute?.enabled ?? false} onChange={e => e.target.checked || initial.config.compute ? setCompute({ enabled: e.target.checked }) : setConfig(c => ({ ...c, compute: null }))} />提供独立 AutoDL 实例</label>
        {config.compute && <><label className="form-field"><span>算力渠道与类型</span><select required value={config.compute.provider_id} onChange={e => setCompute({ provider_id: e.target.value })}><option value="">选择算力渠道…</option>{providers.data?.map(p => <option key={p.id} value={p.id}>{p.name} · {p.gpu_label} × {p.gpu_count}{p.enabled ? "" : " · 已停用"}</option>)}</select></label><div className="form-grid"><label className="form-field"><span>每队 GPU 卡时</span><input type="number" min={0} step="any" required value={config.compute.max_gpu_seconds / 3600} onChange={e => setCompute({ max_gpu_seconds: Math.round(Number(e.target.value) * 3600) })} /></label><label className="form-field"><span>每队参考运行费用 · 元（可选）</span><input type="number" min={0} step="any" value={config.compute.max_cost_millis == null ? "" : config.compute.max_cost_millis / 1000} onChange={e => setCompute({ max_cost_millis: e.target.value === "" ? null : Math.round(Number(e.target.value) * 1000) })} placeholder="留空不限制" /></label></div><p className="evaluation-note">队伍在 API 平台开机并获取 SSH、JupyterLab 等工具。本页保存配置不会创建或启动收费实例。</p></>}
      </fieldset>
      {channels.error && <PageError message={channels.error} retry={channels.reload} />}{providers.error && <PageError message={providers.error} retry={providers.reload} />}{runtimes.error && <PageError message={runtimes.error} retry={runtimes.reload} />}
      <FieldError>{error}</FieldError>
      <div className="setup-actions"><button className="primary-button" disabled={busy || channels.loading || providers.loading || runtimes.loading || Boolean(channels.error || providers.error || runtimes.error || sceneError)}><Save size={14} />{busy ? "正在保存…" : "保存全部环境与资源"}</button><a className="outline-button" href={`${API_BASE}/manage/problems/${problemId}/package`}><Download size={14} />导出赛题包</a><Link className="text-button" href="/api-platform">管理平台渠道</Link></div>
      <p className="evaluation-note">赛题包包含题面、提交要求、评测规则、固定镜像与私有场景；不包含渠道密钥、SSH 密码或队伍用量。导出的私有场景请仅供组织方使用。</p>
    </form>
  </div>;
}

type Preview = { problem: Problem; setup: Config; scene_count: number; message: string };
export function ProblemPackageImport({ trackId, onCreated }: { trackId: string; onCreated: (id: string) => Promise<void> }) {
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [providerId, setProviderId] = useState("");
  const [channelId, setChannelId] = useState("");
  const channels = useApiResource<AIChannel[]>("/ai/manage/channels");
  const providers = useApiResource<Provider[]>("/compute/manage/providers");
  const toast = useToast();
  async function run(importing: boolean) {
    if (!file || busy) return;
    setBusy(true); setError("");
    const data = new FormData(); data.set("file", file);
    data.set("bindings", JSON.stringify({ ...(providerId ? { provider_id: providerId } : {}), ...(channelId ? { allowed_channels: [channelId] } : {}) }));
    try {
      if (importing) {
        const created = await api<Problem>(`/manage/tracks/${trackId}/problem-packages`, { method: "POST", body: data });
        toast.success("赛题包已导入为草稿"); await onCreated(created.id);
      } else setPreview(await api<Preview>("/manage/problem-packages/preview", { method: "POST", body: data }));
    } catch (e) { setError(formatApiError(e)); } finally { setBusy(false); }
  }
  return <section className="evaluation-editor setup-import"><h2>从完整赛题包导入</h2><p>将题面、Docker 环境、私有场景、API 与算力额度一次带入。先检查预览，再创建草稿。</p><label className="form-field"><span>选择赛题 ZIP 包</span><input type="file" accept=".zip,application/zip" disabled={busy} onChange={e => { setFile(e.target.files?.[0] ?? null); setPreview(null); setError(""); }} /></label>
    <details><summary>重新绑定平台渠道（跨平台导入时使用）</summary><div className="form-grid"><label className="form-field"><span>模型渠道</span><select value={channelId} disabled={busy} onChange={e => { setChannelId(e.target.value); setPreview(null); }}><option value="">按包内名称匹配</option>{channels.data?.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}</select></label><label className="form-field"><span>算力渠道</span><select value={providerId} disabled={busy} onChange={e => { setProviderId(e.target.value); setPreview(null); }}><option value="">按包内名称匹配</option>{providers.data?.map(p => <option key={p.id} value={p.id}>{p.name} · {p.gpu_label}</option>)}</select></label></div></details>
    {preview && <div className="ai-notice"><strong>{preview.problem.code} · {preview.problem.title}</strong><p>{preview.problem.summary}</p><p>{preview.scene_count} 个私有场景 · {preview.setup.evaluation_config.adapter ? "自动指标测试" : "材料评审"} · {preview.setup.ai ? "含 API 额度" : "未分配 API"} · {preview.setup.compute ? "含 AutoDL 额度" : "未分配 AutoDL"}</p><p>{preview.message}</p></div>}
    <FieldError>{error}</FieldError><div className="setup-actions"><button type="button" className="outline-button" disabled={!file || busy} onClick={() => void run(false)}>{busy ? "正在处理…" : "检查并预览"}</button>{preview && <button type="button" className="primary-button" disabled={busy} onClick={() => void run(true)}>导入为赛题草稿</button>}</div>
  </section>;
}
