"use client";

import Link from "next/link";
import { FormEvent, useState } from "react";
import { AppShell } from "@/app/components/AppShell";
import { Markdown } from "@/app/components/Markdown";
import { useSession } from "@/app/components/SessionProvider";
import { FieldError, PageError, PageLoading, EmptyState } from "@/app/components/ui";
import { api, formatApiError, jsonBody, resolveMarkdownUrl } from "@/app/lib/api";
import { useApiResource } from "@/app/lib/useApiResource";
import { formatBeijing } from "@/app/lib/time";
import type { CheckpointEntry } from "@/app/lib/domain";

function Feedback({ entry, reload }: { entry: CheckpointEntry; reload: () => Promise<void> }) {
  const [value, setValue] = useState(entry.feedback_md);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError("");
    try {
      await api(`/manage/checkpoints/${entry.id}`, { method: "PATCH", ...jsonBody({ feedback_md: value, revision: entry.revision }) });
      await reload();
    } catch (cause) { setError(formatApiError(cause)); }
    finally { setBusy(false); }
  }
  return <article className="checkpoint-form">
    <header><div><span>{entry.checkpoint_id} · 第{entry.revision}版</span><h3>{entry.team_name}</h3><p>{entry.problem_title}</p></div><small>{formatBeijing(entry.updated_at, { dateStyle: "medium", timeStyle: "short" })}</small></header>
    <Markdown>{entry.content_md}</Markdown>
    <div className="checkpoint-links">{entry.repository && <a href={entry.repository} target="_blank" rel="noreferrer">查看代码版本</a>}{entry.package_url && <a href={resolveMarkdownUrl(entry.package_url)}>下载 {entry.package_name}</a>}</div>
    <form onSubmit={save}><label className="form-field"><span>组织方反馈（本队可见）</span><textarea rows={4} value={value} onChange={(event) => setValue(event.target.value)} maxLength={20000} /></label><button disabled={busy} className="primary-button compact-button">{busy ? "正在保存…" : "保存反馈"}</button><FieldError>{error}</FieldError></form>
  </article>;
}

export default function CheckpointsPage() {
  const { user, loading: sessionLoading } = useSession();
  const allowed = user && ["admin", "organizer"].includes(user.role);
  const { data, error, loading, reload } = useApiResource<CheckpointEntry[]>(allowed ? "/manage/checkpoints" : null);
  return <AppShell title="进度与反馈" eyebrow="CHECKPOINTS" actions={<Link className="outline-button compact-button" href="/manage/competitions">赛事管理</Link>}>
    {sessionLoading || loading ? <PageLoading /> : !allowed ? <PageError message="组织方登录后可查看队伍进度。" /> : error ? <PageError message={error} retry={reload} /> : <>
      <p>进度记录仅本队与组织方可见，不触发测评、不计入成绩。队伍更新版本后需要重新反馈。</p>
      {!data?.length ? <EmptyState title="暂无进度记录" description="队伍在题目提交页记录进展后，会出现在这里。" /> : <div className="checkpoint-review-list">{data.map((entry) => <Feedback key={`${entry.id}:${entry.revision}:${entry.updated_at}`} entry={entry} reload={reload} />)}</div>}
    </>}
  </AppShell>;
}
