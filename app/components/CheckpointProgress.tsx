"use client";

import { FormEvent, useState } from "react";
import { api, formatApiError, jsonBody, resolveMarkdownUrl } from "@/app/lib/api";
import { useApiResource } from "@/app/lib/useApiResource";
import { formatBeijing } from "@/app/lib/time";
import { Markdown } from "./Markdown";
import { FieldError, PageError } from "./ui";
import type { CheckpointEntry, Competition, StagedSubmissionAsset } from "@/app/lib/domain";

type Definition = NonNullable<Competition["config"]["checkpoints"]>[number];

function ProgressForm({ definition, entry, problemId, teamId, reload }: {
  definition: Definition; entry?: CheckpointEntry; problemId: string; teamId: string; reload: () => Promise<void>;
}) {
  const [content, setContent] = useState(entry?.content_md ?? "## 当前版本与效果\n\n## 技术路线与主要改动\n\n## 问题与希望得到的反馈\n");
  const [repository, setRepository] = useState(entry?.repository ?? "");
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [openedAt] = useState(() => Date.now());
  const closed = Boolean(definition.due_at && new Date(definition.due_at).getTime() <= openedAt);
  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true); setError("");
    try {
      let stagedId: string | undefined;
      if (file) {
        if (file.size > 20 * 1024 * 1024) throw new Error("代码ZIP不能超过20MB。");
        const body = new FormData(); body.set("file", file); body.set("problem_id", problemId);
        stagedId = (await api<StagedSubmissionAsset>("/submission-assets/stage", { method: "POST", body })).id;
      }
      await api(`/problems/${problemId}/checkpoints/${definition.id}`, { method: "POST", ...jsonBody({
        team_id: teamId, content_md: content, repository, staged_asset_id: stagedId,
      }) });
      await reload();
    } catch (cause) { setError(formatApiError(cause)); }
    finally { setBusy(false); }
  }
  return <form className="checkpoint-form" onSubmit={save}>
    <header><h3>{definition.label}</h3><span>{definition.due_at ? `截止 ${formatBeijing(definition.due_at, { dateStyle: "medium", timeStyle: "short" })}` : "日期待公布"}</span></header>
    <p>记录代码版本、练习结果和问题。不会运行测评，不消耗测试机会；正式提交仍需在上方完成。</p>
    <label className="form-field"><span>固定代码版本地址（或上传 ZIP）</span><input type="url" placeholder="HTTPS 仓库 commit / release 地址" value={repository} onChange={(event) => setRepository(event.target.value)} disabled={busy || closed} /></label>
    <label className="form-field"><span>进度代码 ZIP · 最多20MB</span><input type="file" accept=".zip" disabled={busy || closed} onChange={(event) => setFile(event.target.files?.[0] ?? null)} /></label>
    {entry?.package_url && <a href={resolveMarkdownUrl(entry.package_url)}>下载已记录版本：{entry.package_name}</a>}
    <label className="form-field"><span>当前效果、技术路线与问题</span><textarea rows={8} maxLength={20000} required value={content} onChange={(event) => setContent(event.target.value)} disabled={busy || closed} /></label>
    <div className="checkpoint-footer"><span>{entry ? `已记录第${entry.revision}版 · ${formatBeijing(entry.updated_at, { dateStyle: "medium", timeStyle: "short" })}` : "尚未记录"}</span><button className="primary-button compact-button" disabled={busy || closed}>{closed ? "已截止" : busy ? "正在记录…" : entry ? "更新进度" : "提交进度"}</button></div>
    <FieldError>{error}</FieldError>
    {entry?.feedback_md && <aside className="checkpoint-feedback"><strong>组织方反馈</strong><Markdown>{entry.feedback_md}</Markdown></aside>}
  </form>;
}

export function CheckpointProgress({ problemId, teamId }: { problemId: string; teamId: string }) {
  const { data, error, reload } = useApiResource<{ definitions: Definition[]; entries: CheckpointEntry[] }>(`/problems/${problemId}/checkpoints?team_id=${encodeURIComponent(teamId)}`);
  const [selected, setSelected] = useState<string | null>(null);
  if (error) return <PageError message={error} retry={reload} />;
  if (!data?.definitions.length) return null;
  const definition = data.definitions.find((item) => item.id === selected) ?? data.definitions[0];
  const entry = data.entries.find((item) => item.checkpoint_id === definition.id);
  return <section className="checkpoint-progress" aria-label="Checkpoint 进度记录">
    <header><span className="eyebrow">PROGRESS</span><h2>开发进度</h2><p>三个交流记录点，与正式测评和最终提交分开。</p></header>
    <div className="checkpoint-tabs">{data.definitions.map((item, index) => <button type="button" key={item.id} className={definition.id === item.id ? "active" : ""} onClick={() => setSelected(item.id)}>Checkpoint {index + 1} · {item.label}</button>)}</div>
    <ProgressForm key={`${teamId}:${definition.id}:${entry?.revision ?? 0}`} definition={definition} entry={entry} problemId={problemId} teamId={teamId} reload={reload} />
  </section>;
}
