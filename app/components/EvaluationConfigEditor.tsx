"use client";

import type { EvaluationAdapter, EvaluationConfig } from "@/app/lib/domain";
import { useApiResource } from "@/app/lib/useApiResource";

function initialConfig(adapter: EvaluationAdapter): EvaluationConfig {
  return {
    adapter: adapter.id, task: adapter.tasks[0],
    resources: { cpus: 2, memory_mb: 4096, gpu: false, time_seconds: 900, episodes: 1 },
    api: { enabled: false, max_calls: 0 }, metrics: adapter.metrics.map((metric) => metric.key),
  };
}

export function EvaluationConfigEditor({ value, onChange }: { value: EvaluationConfig; onChange: (config: EvaluationConfig) => void }) {
  const { data: adapters, error } = useApiResource<EvaluationAdapter[]>("/evaluation-adapters");
  const chosen = adapters?.find((adapter) => adapter.id === value.adapter);
  const active = value.adapter ? value as Extract<EvaluationConfig, { adapter: string }> : null;
  return <fieldset className="evaluation-editor">
    <legend>程序运行与观测指标</legend>
    <label className="form-field"><span>评测器</span><select value={value.adapter ?? ""} onChange={(event) => {
      const adapter = adapters?.find((item) => item.id === event.target.value);
      onChange(adapter ? initialConfig(adapter) : {});
    }}><option value="">不运行提交程序</option>{adapters?.map((adapter) => <option key={adapter.id} value={adapter.id}>{adapter.name}{adapter.available ? "" : " · 尚未接入运行端"}</option>)}</select></label>
    {error && <p role="alert">评测器列表暂时不可用：{error}</p>}
    {chosen && active && <>
      {!chosen.available && <p className="evaluation-note">运行端尚未接入。可以保存题目配置，接入前无法正式提交此题。</p>}
      <div className="form-grid"><label className="form-field"><span>任务类型</span><select value={active.task} onChange={(event) => onChange({ ...active, task: event.target.value, metrics: event.target.value === "open-world" && active.adapter === "minecraft-agent-v1" ? active.metrics.filter((key) => key !== "api_calls") : active.metrics })}>{chosen.tasks.map((task) => <option key={task} value={task}>{task}</option>)}</select></label>
      <div className="form-field"><span>提交格式</span><p>{chosen.submission.label}</p></div></div>
      <div className="evaluation-resource-grid">
        {([ ["cpus", "CPU 核数", 1, 16], ["memory_mb", "内存 MB", 512, 65536], ["time_seconds", "最长运行秒数", 30, 14400], ["episodes", "运行次数", 1, 30] ] as const).map(([key, label, min, max]) => <label className="form-field" key={key}><span>{label}</span><input type="number" min={min} max={max} value={active.resources[key]} onChange={(event) => onChange({ ...active, resources: { ...active.resources, [key]: Number(event.target.value) } })} /></label>)}
      </div>
      <label className="evaluation-toggle"><input type="checkbox" checked={active.resources.gpu} onChange={(event) => onChange({ ...active, resources: { ...active.resources, gpu: event.target.checked } })} />分配 GPU</label>
      <label className="evaluation-toggle"><input type="checkbox" checked={active.api.enabled} onChange={(event) => onChange({ ...active, api: { enabled: event.target.checked, max_calls: event.target.checked ? 100 : 0 } })} />允许受控 API 调用</label>
      {active.api.enabled && <label className="form-field"><span>API 调用上限</span><input type="number" min="0" max="100000" value={active.api.max_calls} onChange={(event) => onChange({ ...active, api: { ...active.api, max_calls: Number(event.target.value) } })} /></label>}
      <div className="evaluation-metric-picker"><strong>展示指标</strong><div>{chosen.metrics.filter((metric) => !(active.adapter === "minecraft-agent-v1" && active.task === "open-world" && metric.key === "api_calls")).map((metric) => <label key={metric.key}><input type="checkbox" checked={active.metrics.includes(metric.key)} onChange={(event) => onChange({ ...active, metrics: event.target.checked ? [...active.metrics, metric.key] : active.metrics.filter((key) => key !== metric.key) })} /><span>{metric.label} <small>{metric.unit}</small></span></label>)}</div></div>
      <p className="evaluation-note">指标由独立运行端提供，仅用于作品展示和评委参考，不计入外部评分。</p>
    </>}
  </fieldset>;
}
