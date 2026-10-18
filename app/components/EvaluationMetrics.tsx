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
  const development = run?.result_scope === "development";
  const final = run?.purpose === "final";
  const libero = config.adapter === "libero-agent-v1";
  const weightedLibero = libero && run?.performance?.preset === "wujie-libero-v2";
  const visibleMetrics = config.metrics.filter((key) => !weightedLibero ||
    !["completion_score", "efficiency_score", "automatic_score"].includes(key));
  const completedScenes = run?.episodes?.filter((episode) =>
    (isScenarioEpisode(episode) ? episode.metrics : episode).task_success === 100).length ?? 0;
  const partial = libero && run?.status === "failed" ? run.partial_results : null;
  const definitions = Object.fromEntries((run?.metric_definitions ?? []).map((metric) => [metric.key, metric]));
  const publicPoints = development && libero && run?.performance?.preset === "wujie-libero-v2"
    ? run.episodes.reduce((sum, e) => sum + (isScenarioEpisode(e) ? e.metrics.automatic_score ?? 0 : 0) / 6, 0)
    : undefined;
  return <section className="evaluation-results" aria-label="运行指标">
    <header><strong>{final ? "最终评测" : development ? "开发自测" : "运行指标"}</strong><span>{run ? evaluationStatus(run) : "等待提交"}</span></header>
    {run && showTiming && <EvaluationTiming run={run} />}
    {run?.status === "completed" ? <>
      {run.budget_termination_reason && <p>{run.budget_termination_reason === "api_call_limit" ? "本轮API调用次数已用尽。" : "本轮运行时间已用尽。"} 已按最后可信状态结算，未运行题目计零。</p>}
      {run.performance && <p><strong>{publicPoints === undefined ? `自动表现分：${run.performance.score.toFixed(2)} / 100` : `公开题得分：${publicPoints.toFixed(2)} / 50`}</strong> · {development ? "供开发与分析使用。正式成绩由独立最终评测产生。" : "按题面公式计算，用于第一阶段评分。"}</p>}
      {libero && run.episodes?.length ? <p>完成场景：{completedScenes} / {run.episodes.length}</p> : null}
      <h4>跨场景平均</h4>
      <dl>{visibleMetrics.map((key) => <div key={key}><dt>{definitions[key]?.label ?? key}</dt><dd>{metricValue(run.metrics[key], definitions[key]?.unit)}</dd></div>)}</dl>
      {run.episodes?.length ? <div className="evaluation-episodes">
        {run.episodes.map((episode, index) => {
          const wrapped = isScenarioEpisode(episode);
          const scenario = wrapped ? episode.scenario : { id: "episode-" + (index + 1), label: "运行 " + (index + 1), difficulty: "challenge" as const };
          const metrics = wrapped ? episode.metrics : episode;
          const weight = libero && run.performance?.preset === "wujie-libero-v2"
            ? final && run.episodes.length === 5 ? [10, 10, 10, 15, 15][index]
              : ["libero-1", "libero-2", "libero-3"].includes(scenario.id) ? 10 : undefined
            : undefined;
          return <article key={scenario.id}>
            <header><strong>{scenario.label}</strong>{!libero && <span>{difficultyLabels[scenario.difficulty]}</span>}</header>
            {weight !== undefined && <p>本题得分：{((metrics.automatic_score ?? 0) * weight / 60).toFixed(2)} / {(weight * 5 / 3).toFixed(2)}
              （完成 {((metrics.completion_score ?? 0) * weight / 60).toFixed(2)} ＋效率 {((metrics.efficiency_score ?? 0) * weight / 60).toFixed(2)}）</p>}
            {wrapped && episode.objectives?.length ? <ul className="evaluation-objectives">
              {episode.objectives.map((objective) => <li className={objective.completed ? "completed" : ""} key={objective.id}>
                {objective.completed ? <Check aria-hidden="true" size={15} /> : <Circle aria-hidden="true" size={15} />}
                <span>{objective.label}</span><code>{objective.id}</code>
              </li>)}
            </ul> : null}
            <dl>{visibleMetrics.map((key) => <div key={key}><dt>{definitions[key]?.label ?? key}</dt><dd>{metricValue(metrics[key], definitions[key]?.unit)}</dd></div>)}</dl>
          </article>;
        })}
      </div> : null}
    </> : partial ? <>
      <p><strong>本轮评测中断，未生成整轮成绩。</strong> {partial.reason === "evaluation_time_limit" ? "运行时间已用尽。" : "执行出现异常。"}</p>
      <p>已结束场景：{partial.episodes.length} / {partial.planned_episodes} · 成功：{partial.episodes.filter((episode) => episode.metrics.task_success === 100).length}</p>
      {partial.interrupted_scene && <p>中断场景：{partial.interrupted_scene.label}</p>}
      {partial.reason === "execution_error" && run?.error && <p>运行记录：{run.error}</p>}
      {partial.episodes.length > 0 && <div className="evaluation-episodes"><h4>已结束场景记录</h4>{partial.episodes.map((episode) => <article key={episode.scenario.id}>
        <header><strong>{episode.scenario.label}</strong></header>
        <dl>{config.metrics.map((key) => <div key={key}><dt>{definitions[key]?.label ?? key}</dt><dd>{metricValue(episode.metrics[key], definitions[key]?.unit)}</dd></div>)}</dl>
      </article>)}</div>}
      <small>已保存的结果、回放和轨迹用于分析中断前的运行情况。</small>
    </> :
      <p>{run?.status === "failed" ? run.error === "evaluation time limit exceeded" ? "本次测试已超时，程序已终止，未能生成完整指标。" : "本次运行未能生成指标。" : "提交的程序运行后，这里会展示观测结果。"}</p>}
    {config.api?.enabled && run && <p>本次运行 API 调用：{run.api_calls_used ?? 0} / {run.api_calls_limit ?? config.api.max_calls}</p>}
    {final && run?.scene_set_version && <p>场景版本：{run.scene_set_version}</p>}
    {libero && run?.artifacts?.some((artifact) => artifact.name.endsWith("replay.mp4")) ? <div className="evaluation-evidence">
      <h4>双视角回放</h4>
      {run.artifacts.filter((artifact) => artifact.name.endsWith("replay.mp4")).map((artifact) => <figure key={artifact.id}>
        <video controls preload="metadata" playsInline src={resolveMarkdownUrl(artifact.url)} style={{ width: "100%", maxWidth: 768 }} aria-label={`场景 ${artifact.name.split("-")[1]} 双视角回放`} />
        <figcaption>场景 {artifact.name.split("-")[1]} · 外部相机与腕部相机</figcaption>
      </figure>)}
      <small>视频按仿真时间播放；模型等待时长见动作轨迹。</small>
    </div> : null}
    {run?.artifacts?.length ? <div className="evaluation-evidence"><h4>运行记录</h4>{run.artifacts.map((artifact) => <a key={artifact.id} href={resolveMarkdownUrl(artifact.url)} target="_blank" rel="noreferrer">{artifact.name.endsWith("replay.mp4") ? "打开视频回放" : artifact.name.endsWith("replay.gif") ? "下载场景回放" : artifact.name.endsWith("classification.json") ? "下载各类表现与混淆矩阵" : "下载动作轨迹"} · {artifact.name.split("-")[1]}</a>)}</div> : null}
    {run && showLogs && <EvaluationLogs run={run} />}
    <small>{partial ? "整轮评测完成后再按题面规则计分。" : development ? "开发记录与正式作品关联，代码和技术路线由评委审阅。" : run?.performance ? run.performance.preset === "wujie-libero-v2" ? "五题完成分10/10/10/15/15，效率分按相同比例分配40分；代码审阅通过或不通过，答辩单独评分。" : "方案说明和答辩由评委独立评分；自动表现分由平台计算。" : "指标供评委参考；如本题配置自动表现分，将按题面规则计分。"}</small>
  </section>;
}
