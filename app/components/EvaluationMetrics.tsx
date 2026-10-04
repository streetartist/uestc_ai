import type { EvaluationConfig, EvaluationRun } from "@/app/lib/domain";

const statusLabels: Record<EvaluationRun["status"], string> = {
  queued: "等待运行", running: "正在运行", completed: "运行完成", failed: "运行失败", superseded: "已更新提交",
};

const difficultyLabels = { beginner: "入门", intermediate: "进阶", challenge: "挑战" } as const;
type ScenarioEpisode = Extract<EvaluationRun["episodes"][number], { scenario: object }>;

function isScenarioEpisode(episode: EvaluationRun["episodes"][number]): episode is ScenarioEpisode {
  return "scenario" in episode && typeof episode.scenario === "object" && episode.scenario !== null
    && "metrics" in episode && typeof episode.metrics === "object" && episode.metrics !== null;
}

function metricValue(value: number | undefined, unit?: string) {
  return <>{value?.toLocaleString("zh-CN", { maximumFractionDigits: 2 }) ?? "-"}{unit ? <small> {unit}</small> : null}</>;
}

export function EvaluationMetrics({ config, run }: { config?: EvaluationConfig; run?: EvaluationRun | null }) {
  if (!config?.adapter) return null;
  const definitions = Object.fromEntries((run?.metric_definitions ?? []).map((metric) => [metric.key, metric]));
  return <section className="evaluation-results" aria-label="运行指标">
    <header><strong>运行指标</strong><span>{run ? statusLabels[run.status] : "等待提交"}</span></header>
    {run?.status === "completed" ? <>
      <h4>跨场景平均</h4>
      <dl>{config.metrics.map((key) => <div key={key}><dt>{definitions[key]?.label ?? key}</dt><dd>{metricValue(run.metrics[key], definitions[key]?.unit)}</dd></div>)}</dl>
      {run.episodes?.length ? <div className="evaluation-episodes">
        {run.episodes.map((episode, index) => {
          const wrapped = isScenarioEpisode(episode);
          const scenario = wrapped ? episode.scenario : { id: "episode-" + (index + 1), label: "运行 " + (index + 1), difficulty: "challenge" as const };
          const metrics = wrapped ? episode.metrics : episode;
          return <article key={scenario.id}>
            <header><strong>{scenario.label}</strong><span>{difficultyLabels[scenario.difficulty]}</span></header>
            <dl>{config.metrics.map((key) => <div key={key}><dt>{definitions[key]?.label ?? key}</dt><dd>{metricValue(metrics[key], definitions[key]?.unit)}</dd></div>)}</dl>
          </article>;
        })}
      </div> : null}
    </> :
      <p>{run?.status === "failed" ? "本次运行未能生成指标。" : "提交的程序运行后，这里会展示观测结果。"}</p>}
    {config.api?.enabled && run && <p>本次运行 API 调用：{run.api_calls_used ?? 0} / {config.api.max_calls}</p>}
    <small>指标供作品展示与评委参考，不自动计入评审成绩。</small>
  </section>;
}
