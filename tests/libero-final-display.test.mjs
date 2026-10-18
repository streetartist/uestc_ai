import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { createRequire } from "node:module";
import test from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";

const root = new URL("../", import.meta.url);
const require = createRequire(import.meta.url);
const modules = new Map();
function load(path) {
  if (modules.has(path)) return modules.get(path);
  const source = readFileSync(new URL(path, root), "utf8");
  const { outputText } = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } });
  const compiled = { exports: {} };
  const localRequire = (name) => {
    if (name === "@/app/lib/api") return { resolveMarkdownUrl: (url) => url };
    if (name === "@/app/components/EvaluationLogs") return { EvaluationLogs: () => null };
    if (!name.startsWith("@/")) return require(name);
    const base = name.slice(2);
    return load(base + (existsSync(new URL(base + ".ts", root)) ? ".ts" : ".tsx"));
  };
  new Function("require", "module", "exports", outputText)(localRequire, compiled, compiled.exports);
  modules.set(path, compiled.exports);
  return compiled.exports;
}
const { EvaluationMetrics } = load("app/components/EvaluationMetrics.tsx");
const config = { adapter: "libero-agent-v1", metrics: ["task_success"], api: { enabled: false } };
const run = {
  id: "example", purpose: "trial", status: "completed", attempts: 1,
  metric_definitions: [{ key: "task_success", label: "任务成功率", unit: "%" }],
  metrics: { task_success: 100 }, performance: { score: 100 },
  episodes: [{ scenario: { id: "bowl", label: "黑碗放置", difficulty: "challenge" }, metrics: { task_success: 100 } }],
};
const render = (changes = {}, configChanges = {}) => renderToStaticMarkup(React.createElement(EvaluationMetrics, {
  config: { ...config, ...configChanges }, run: { ...run, ...changes }, showTiming: false, showLogs: false,
}));

test("LIBERO counts successful scenes without giving partial goals a scene pass", () => {
  const html = render({ episodes: [
    { scenario: { id: "one", label: "one", difficulty: "beginner" }, metrics: { task_success: 100 } },
    { scenario: { id: "two", label: "two", difficulty: "beginner" }, metrics: { task_success: 0 }, objectives: [{ id: "a", label: "a", completed: true }] },
    { scenario: { id: "three", label: "three", difficulty: "beginner" }, metrics: { task_success: 100 } },
  ] });
  assert.match(html, /完成场景：2 \/ 3/);
});

test("failed runs and other adapters do not show the LIBERO success count", () => {
  assert.doesNotMatch(render({ status: "failed" }), /完成场景：/);
  assert.doesNotMatch(render({}, { adapter: "minecraft-agent-v1" }), /完成场景：/);
});

test("LIBERO videos are playable and distinguished from action trajectories", () => {
  const html = render({ artifacts: [
    { id: "video", name: "scene-1-replay.mp4", url: "/api/evaluation-artifacts/video" },
    { id: "trajectory", name: "scene-1-trajectory.json", url: "/api/evaluation-artifacts/trajectory" },
  ] });
  assert.match(html, /<video[^>]*controls/);
  assert.match(html, /src="\/api\/evaluation-artifacts\/video"/);
  assert.match(html, /外部相机与腕部相机/);
  assert.match(html, /模型等待时长见动作轨迹/);
  assert.match(html, /下载动作轨迹/);
});

test("development results do not claim to count as formal scores", () => {
  const html = render({ result_scope: "development" });
  assert.match(html, /开发自测/);
  assert.match(html, /正式成绩由独立最终评测产生/);
  assert.doesNotMatch(html, /用于第一阶段评分/);
});

test("final results show their scope and scene-set version", () => {
  const html = render({ purpose: "final", result_scope: "final", scene_set_version: "candidate-v1" });
  assert.match(html, /最终评测/);
  assert.match(html, /candidate-v1/);
  assert.match(html, /用于第一阶段评分/);
  assert.doesNotMatch(html, /开发自测/);
});

test("LIBERO has task labels without difficulty tiers", () => {
  const html = render();
  assert.match(html, /黑碗放置/);
  assert.doesNotMatch(html, /入门|进阶|挑战/);
});

test("existing non-LIBERO results keep their scoring and difficulty display", () => {
  const html = render({}, { adapter: "minecraft-agent-v1" });
  assert.match(html, /用于第一阶段评分/);
  assert.match(html, /挑战/);
  assert.doesNotMatch(html, /独立最终评测/);
});

const { FinalEvaluationStatus } = load("app/components/FinalEvaluationStatus.tsx");
test("completed but unpublished finals have an accurate waiting state", () => {
  const html = renderToStaticMarkup(React.createElement(FinalEvaluationStatus, { status: "awaiting_publication" }));
  assert.match(html, /已完成，结果待统一发布/);
  assert.doesNotMatch(html, /正在运行|等待.*冻结/);
});

test("final results use their frozen API budget", () => {
  const html = render({ purpose: "final", result_scope: "final", api_calls_used: 47, api_calls_limit: 900 }, { api: { enabled: true, max_calls: 300 } });
  assert.match(html, /47.*900/);
  assert.doesNotMatch(html, /47.*300/);
});

test("weighted LIBERO task points are not mixed with raw normalized scores", () => {
  const html = render({ result_scope: "development", performance: { preset: "wujie-libero-v2", score: 100 },
    metrics: { task_success: 100, completion_score: 60, efficiency_score: 40, automatic_score: 100 },
    episodes: [{ scenario: { id: "libero-1", label: "黑碗放置", difficulty: "beginner" },
      metrics: { task_success: 100, completion_score: 60, efficiency_score: 40, automatic_score: 100 } }],
    metric_definitions: [{ key: "completion_score", label: "完成分", unit: "分" }],
  }, { metrics: ["task_success", "completion_score", "efficiency_score", "automatic_score"] });
  assert.match(html, /公开题得分：16.67 \/ 50/);
  assert.match(html, /本题得分：16.67 \/ 16.67/);
  assert.match(html, /完成 10.00.*效率 6.67/);
  assert.doesNotMatch(html, /<dt>完成分<\/dt>|<dt>efficiency_score<\/dt>|<dt>automatic_score<\/dt>/);
});

test("interrupted LIBERO runs preserve finished scenes without issuing a score", () => {
  const html = render({ status: "failed", error: "evaluation time limit exceeded", metrics: {}, performance: null, partial_results: {
    episodes: run.episodes, planned_episodes: 3,
    interrupted_scene: { id: "soup", label: "指定物品入篮", difficulty: "intermediate" },
    reason: "evaluation_time_limit",
  } });
  assert.match(html, /本轮评测中断，未生成整轮成绩/);
  assert.match(html, /已结束场景：1.*3/);
  assert.match(html, /成功：1/);
  assert.match(html, /中断场景：指定物品入篮/);
  assert.match(html, /黑碗放置/);
  assert.doesNotMatch(html, /自动表现分|跨场景平均|100.00/);
});
test("execution errors before any completed scene remain diagnostic records", () => {
  const html = render({ status: "failed", performance: null, partial_results: {
    episodes: [], planned_episodes: 3, interrupted_scene: null, reason: "execution_error",
  } });
  assert.match(html, /执行出现异常/);
  assert.match(html, /已结束场景：0.*3/);
  assert.doesNotMatch(html, /自动表现分|跨场景平均|已结束场景记录/);
});
