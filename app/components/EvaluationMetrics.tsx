import type { EvaluationConfig, EvaluationRun } from "@/app/lib/domain";
import { resolveMarkdownUrl } from "@/app/lib/api";
import { Check, Circle } from "lucide-react";
import { EvaluationTiming } from "@/app/components/EvaluationTiming";
import { evaluationStatus } from "@/app/lib/evaluation-timing";
import { EvaluationLogs } from "@/app/components/EvaluationLogs";

const difficultyLabels = { beginner: "入门", intermediate: "进阶", challenge: "挑战" } as const;
type ScenarioEpisode = Extract<EvaluationRun["episodes"][number], { scenario: object }>;

function isScenarioEpisode(episode: EvaluationRun["episodes"][number]): episode is ScenarioEpisode {
  return "scenario" in episode && typeof episode.scenario === "object" && episode.scenario !== null
    && "metrics" in episode && typeof episode.metrics === "object" && episode.metrics !== null;
}

function metricValue(value: number | undefined, unit?: string) {
  return <>{value?.toLocaleString("zh-CN", { maximumFractionDigits: 2 }) ?? "-"}{unit ? <small> {unit}</small> : null}</>;
}

export function EvaluationMetrics({ config, run, showTiming = true, showLogs = true }: { config?: EvaluationConfig; run?: EvaluationRun | null; showTiming?: boolean; showLogs?: boolean }) {
  if (!config?.adapter) return null;
  const definitions = Object.fromEntries((run?.metric_definitions ?? []).map((metric) => [metric.key, metric]));
  return <section className="evaluation-results" aria-label="运行指标">
    <header><strong>运行指标</strong><span>{run ? evaluationStatus(run) : "等待提交"}</span></header>
    {run && showTiming && <EvaluationTiming run={run} />}
    {run?.status === "completed" ? <>
      {run.performance && <p><strong>{run.performance.preset === "wujie-depth-baseline-v2" ? "项目成果分" : "自动表现分"}：{run.performance.score.toFixed(2)} / 100</strong> · 按题面公式计算，正式提交后用于第一阶段评分。</p>}
      {run.performance && config.adapter === "classification-v1" && <p>计分版本：{run.performance.preset === "wujie-depth-baseline-v2" ? "v2 基准与提升" : run.performance.preset === "wujie-depth-v1" ? "v1 准确率加权" : run.performance.preset}</p>}
      {run.performance?.preset === "wujie-depth-baseline-v2" && run.performance.reference && <p>本次计分参考：B = {metricValue(run.performance.reference.baseline_accuracy)}%；G = {metricValue(run.performance.reference.gain_span_pp)} 个百分点；T = {metricValue(run.performance.reference.latency_ms)} ms／片段。</p>}
      {run.performance?.components && <dl aria-label="项目成果计分明细">
        <div><dt>基准分</dt><dd>{metricValue(run.performance.components.baseline)} / 60</dd></div>
        <div><dt>提升分</dt><dd>{metricValue(run.performance.components.improvement)} / 20</dd></div>
        <div><dt>类别表现</dt><dd>{metricValue(run.performance.components.macro_f1)} / 10</dd></div>
        <div><dt>推理效率</dt><dd>{metricValue(run.performance.components.efficiency)} / 10</dd></div>
      </dl>}
      <h4>{config.adapter === "classification-v1" ? "评测集整体结果" : "跨场景平均"}</h4>
      <dl>{config.metrics.map((key) => <div key={key}><dt>{definitions[key]?.label ?? key}</dt><dd>{metricValue(run.metrics[key], definitions[key]?.unit)}</dd></div>)}</dl>
      {run.episodes?.length ? <div className="evaluation-episodes">
        {run.episodes.map((episode, index) => {
          const wrapped = isScenarioEpisode(episode);
          const scenario = wrapped ? episode.scenario : { id: "episode-" + (index + 1), label: "运行 " + (index + 1), difficulty: "challenge" as const };
          const metrics = wrapped ? episode.metrics : episode;
          return <article key={scenario.id}>
            <header><strong>{scenario.label}</strong><span>{difficultyLabels[scenario.difficulty]}</span></header>
            {wrapped && episode.objectives?.length ? <ul className="evaluation-objectives">
              {episode.objectives.map((objective) => <li className={objective.completed ? "completed" : ""} key={objective.id}>
                {objective.completed ? <Check aria-hidden="true" size={15} /> : <Circle aria-hidden="true" size={15} />}
                <span>{objective.label}</span><code>{objective.id}</code>
              </li>)}
            </ul> : null}
            <dl>{config.metrics.map((key) => <div key={key}><dt>{definitions[key]?.label ?? key}</dt><dd>{metricValue(metrics[key], definitions[key]?.unit)}</dd></div>)}</dl>
          </article>;
        })}
      </div> : null}
    </> :
      <p>{run?.status === "failed" ? run.error === "evaluation time limit exceeded" ? "本次测试已超时，程序已终止，未能生成完整指标。" : "本次运行未能生成指标。" : "提交的程序运行后，这里会展示观测结果。"}</p>}
    {config.api?.enabled && run && <p>本次运行 API 调用：{run.api_calls_used ?? 0} / {config.api.max_calls}</p>}
    {run?.artifacts?.length ? <div className="evaluation-evidence"><h4>运行记录</h4>{run.artifacts.map((artifact) => <a key={artifact.id} href={resolveMarkdownUrl(artifact.url)} target="_blank" rel="noreferrer">{artifact.name.endsWith("replay.gif") ? "下载场景回放" : artifact.name.endsWith("classification.json") ? "下载各类表现与混淆矩阵" : "下载动作轨迹"} · {artifact.name.split("-")[1]}</a>)}</div> : null}
    {run && showLogs && <EvaluationLogs run={run} />}
    <small>{run?.performance ? "技术说明和答辩由评委评分；运行成绩由平台按题面公式计算。" : "指标供评委参考；如本题配置自动表现分，将按题面规则计分。"}</small>
  </section>;
}
