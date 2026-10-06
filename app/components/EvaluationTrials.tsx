"use client";

import { useEffect, useState } from "react";
import { Check, ChevronDown, Play, RefreshCw } from "lucide-react";
import { EvaluationMetrics } from "@/app/components/EvaluationMetrics";
import { api } from "@/app/lib/api";
import type { EvaluationBudget, EvaluationConfig, EvaluationRun } from "@/app/lib/domain";
import { formatBeijing } from "@/app/lib/time";
import { useApiResource } from "@/app/lib/useApiResource";

const labels: Record<EvaluationRun["status"], string> = { queued: "排队中", running: "测试中", completed: "已完成", failed: "失败 / 超时", superseded: "旧提交记录" };

export function EvaluationTrials({ problemId, teamId, config, budget, busy, disabled, selectedId, onSelect, onStart, onBudgetRefresh, refreshKey }: {
  problemId: string; teamId: string; config: EvaluationConfig; budget: EvaluationBudget | null;
  busy: boolean; disabled: boolean; selectedId: string; onSelect: (id: string) => void;
  onStart: () => void; onBudgetRefresh: () => Promise<void>; refreshKey: number;
}) {
  const path = `/problems/${problemId}/evaluation-runs?team_id=${encodeURIComponent(teamId)}`;
  const { data, loading, error, reload, setData } = useApiResource<EvaluationRun[]>(path);
  const [expandedId, setExpandedId] = useState("");
  const active = data?.some((run) => run.status === "queued" || run.status === "running");
  useEffect(() => { if (refreshKey) void reload(); }, [refreshKey, reload]);
  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(() => { void api<EvaluationRun[]>(path).then(setData).catch(() => undefined); }, 5000);
    return () => window.clearInterval(timer);
  }, [active, path, setData]);
  useEffect(() => {
    if (data) void onBudgetRefresh();
  }, [data, onBudgetRefresh]);
  const displayBudget = budget;
  return <section className="trial-panel" aria-label="自主测试">
    <header><div><span className="eyebrow">TEAM TESTING</span><h3>自主测试</h3></div><button type="button" className="primary-button" disabled={disabled || busy || active || !displayBudget || displayBudget.remaining_runs === 0 || loading || Boolean(error)} onClick={onStart}><Play size={16} />{busy ? "正在创建测试" : active ? "已有测试进行中" : "开始测试"}</button></header>
    <p>上传程序包后即可测试，无需先正式提交。测试记录仅供本队查看，不会覆盖已交给评委的正式稿。</p>
    <div className="trial-budget">{displayBudget ? <><span>单次最长 <strong>{displayBudget.time_seconds} 秒</strong></span><span>已用 <strong>{displayBudget.used_runs} 次</strong></span><span>{displayBudget.remaining_runs === null ? "次数不限" : <>剩余 <strong>{displayBudget.remaining_runs} 次</strong></>}</span></> : <span>正在读取额度</span>}</div>
    <small>开始测试占用一次机会，排队不会额外扣次；运行时限从领取后计算。程序错误和超时计入次数，推理前的基础设施故障会退回机会。保存草稿、正式提交均不扣次数。</small>
    <div className="trial-history-heading"><h4>本队测试记录</h4><button type="button" className="text-button" onClick={() => void reload()} disabled={loading}><RefreshCw size={14} />刷新记录</button></div>
    {error ? <p role="alert">{error}</p> : loading && !data ? <p>正在读取记录…</p> : !data?.length ? <div className="trial-empty">还没有测试记录。选择上方附件，开始第一次测试。</div> : <div className="trial-history">{data.map((run, index) => <article className={selectedId === run.id ? "selected" : ""} key={run.id}>
      <div className="trial-record"><button type="button" className="trial-record-info" aria-expanded={expandedId === run.id} onClick={() => setExpandedId(expandedId === run.id ? "" : run.id)}><strong>测试 {data.length - index}<span className={`trial-status ${run.status}`}>{labels[run.status]}</span></strong><span>{run.package_name || "程序包"} · {formatBeijing(run.created_at, { dateStyle: "short", timeStyle: "short" })}</span><ChevronDown size={16} /></button>{run.purpose === "trial" && run.status === "completed" && <button type="button" className="outline-button" disabled={disabled || busy} aria-pressed={selectedId === run.id} onClick={() => onSelect(run.id)}>{selectedId === run.id && <Check size={14} />}{selectedId === run.id ? "已选为正式结果" : "用于正式提交"}</button>}</div>
      {run.dispatch && <p role="status"><strong>{run.dispatch.label}</strong>{run.dispatch.detail ? ` · ${run.dispatch.detail}` : ""}</p>}
      {run.quota_refunded && <p>本次未执行，测试机会已退回。</p>}
      {expandedId === run.id && <EvaluationMetrics config={config} run={run} />}
    </article>)}</div>}
    <p className="trial-selection-note">{selectedId ? "已选择测评结果。正式提交时请保留与该次测试完全相同的程序包；报告和说明可以完善。" : "测试完成后，选择一条成功生成指标的记录用于正式提交。"}</p>
  </section>;
}
