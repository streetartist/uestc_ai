"use client";

import type { EvaluationAdapter, EvaluationConfig } from "@/app/lib/domain";
import { useApiResource } from "@/app/lib/useApiResource";

function initialConfig(adapter: EvaluationAdapter): EvaluationConfig {
  const minecraft = adapter.id === "minecraft-agent-v1";
  return {
    adapter: adapter.id, task: minecraft ? "open-world" : adapter.tasks[0],
    resources: { cpus: minecraft ? 4 : 2, memory_mb: minecraft ? 6144 : 4096, gpu: false, time_seconds: 900, episodes: 1 },
    api: { enabled: false, max_calls: 0 }, metrics: adapter.metrics.filter(metric => !minecraft || !["api_calls", "api_cost", "survival_seconds"].includes(metric.key)).map((metric) => metric.key), max_team_runs: 3,
  };
}

export function EvaluationConfigEditor({ value, onChange }: { value: EvaluationConfig; onChange: (config: EvaluationConfig) => void }) {
  const { data: adapters, error } = useApiResource<EvaluationAdapter[]>("/evaluation-adapters");
  const chosen = adapters?.find((adapter) => adapter.id === value.adapter);
  const active = value.adapter ? value as Extract<EvaluationConfig, { adapter: string }> : null;
  return <fieldset className="evaluation-editor">
    <legend>指标测试与运行配置</legend>
    <p className="evaluation-note">平台自动运行队伍提交的程序并采集指标。以下限制对本题所有队伍统一生效，与队伍自用算力额度分开。</p>
    <label className="form-field"><span>评测器</span><select value={value.adapter ?? ""} onChange={(event) => {
      const adapter = adapters?.find((item) => item.id === event.target.value);
      onChange(adapter ? initialConfig(adapter) : {});
    }}><option value="">不运行提交程序</option>{adapters?.map((adapter) => <option key={adapter.id} value={adapter.id}>{adapter.name}{adapter.available ? "" : " · 尚未接入运行端"}</option>)}</select></label>
    {error && <p role="alert">评测器列表暂时不可用：{error}</p>}
    {chosen && active && <>
      {!chosen.available && <p className="evaluation-note">运行端尚未接入。可以保存题目配置，接入前无法正式提交此题。</p>}
      <div className="form-grid"><label className="form-field"><span>任务类型</span><select value={active.task} onChange={(event) => onChange({ ...active, task: event.target.value, metrics: event.target.value === "open-world" && active.adapter === "minecraft-agent-v1" ? active.metrics.filter((key) => !["api_calls", "api_cost", "survival_seconds"].includes(key)) : active.metrics })}>{chosen.tasks.map((task) => <option key={task} value={task}>{task}</option>)}</select></label>
      <div className="form-field"><span>提交格式</span><p>{chosen.submission.label}</p></div></div>
      <div className="evaluation-resource-grid">
        <label className="form-field"><span>每次测试最长时长（秒）</span><input type="number" min={30} max={14400} required value={active.resources.time_seconds} onChange={(event) => onChange({ ...active, resources: { ...active.resources, time_seconds: Number(event.target.value) } })} /><small>30—14400 秒；本次全部场景共用，到时终止运行。</small></label>
        <label className="form-field"><span>每队最多测试次数</span><input type="number" min={1} max={1000} placeholder="不限制" value={active.max_team_runs ?? ""} onChange={(event) => {
          const next = { ...active };
          if (event.target.value === "") delete next.max_team_runs;
          else next.max_team_runs = Number(event.target.value);
          onChange(next);
        }} /><small>1—1000 次；留空不限制，新配置默认 3 次。</small></label>
        {([ ["cpus", "CPU 核数", 1, 16], ["memory_mb", "内存 MB", 512, 65536], ["episodes", "每次测试的场景数量", 1, 30] ] as const).map(([key, label, min, max]) => <label className="form-field" key={key}><span>{label}</span><input type="number" min={min} max={max} required value={active.resources[key]} onChange={(event) => onChange({ ...active, resources: { ...active.resources, [key]: Number(event.target.value) } })} /></label>)}
      </div>
      <p className="evaluation-note">每次正式提交触发一次测试并扣一次额度，草稿不扣次数。失败、超时和替换提交仍计入，运行端故障重试不重复扣除。调整时长或次数只影响后续测试，已用次数保留。</p>
      <label className="evaluation-toggle"><input type="checkbox" disabled={active.adapter === "robot-arm-agent-v1"} checked={active.resources.gpu} onChange={(event) => onChange({ ...active, resources: { ...active.resources, gpu: event.target.checked } })} />{active.adapter === "robot-arm-agent-v1" ? "机械臂首版使用 CPU 仿真与渲染" : "分配 GPU"}</label>
      <label className="evaluation-toggle"><input type="checkbox" checked={active.api.enabled} onChange={(event) => onChange({ ...active, api: { enabled: event.target.checked, max_calls: event.target.checked ? 100 : 0 } })} />允许受控 API 调用</label>
      {active.api.enabled && <label className="form-field"><span>API 调用上限</span><input type="number" min="0" max="100000" value={active.api.max_calls} onChange={(event) => onChange({ ...active, api: { ...active.api, max_calls: Number(event.target.value) } })} /></label>}
      <div className="evaluation-metric-picker"><strong>展示指标</strong><div>{chosen.metrics.filter((metric) => !(active.adapter === "minecraft-agent-v1" && active.task === "open-world" && ["api_calls", "api_cost", "survival_seconds"].includes(metric.key))).map((metric) => <label key={metric.key}><input type="checkbox" checked={active.metrics.includes(metric.key)} onChange={(event) => onChange({ ...active, metrics: event.target.checked ? [...active.metrics, metric.key] : active.metrics.filter((key) => key !== metric.key) })} /><span>{metric.label} <small>{metric.unit}</small></span></label>)}</div></div>
      <p className="evaluation-note">指标由独立运行端提供，仅用于作品展示和评委参考，不计入外部评分。</p>
    </>}
  </fieldset>;
}
