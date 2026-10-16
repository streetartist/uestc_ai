"use client";

import Link from "next/link";
import { FormEvent, useState } from "react";
import { AppShell } from "@/app/components/AppShell";
import { Markdown } from "@/app/components/Markdown";
import { useSession } from "@/app/components/SessionProvider";
import { useToast } from "@/app/components/ToastProvider";
import { EmptyState, FieldError, PageError, PageLoading } from "@/app/components/ui";
import { api, formatApiError, jsonBody } from "@/app/lib/api";
import type { Competition, ManagedSubmission, SubmissionAsset, WorkPublication } from "@/app/lib/domain";
import { validateForm } from "@/app/lib/formValidation";
import { useApiResource } from "@/app/lib/useApiResource";

type Settings = { version_id: string; version_created_at: string; publication: WorkPublication; assets: SubmissionAsset[]; competition_ended: boolean };

function PublicationForm({ work, settings, reload }: { work: ManagedSubmission; settings: Settings; reload: () => Promise<void> }) {
  const toast = useToast();
  const [title, setTitle] = useState(settings.publication.title ?? work.title);
  const [description, setDescription] = useState(settings.publication.description_md ?? "");
  const [assets, setAssets] = useState(settings.publication.asset_ids ?? []);
  const [metrics, setMetrics] = useState(settings.publication.metrics_public ?? false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function save(published: boolean) {
    if (busy) return;
    setBusy(true); setError("");
    try {
      await api(`/manage/submissions/${work.id}/publication`, { method: "PATCH", ...jsonBody({
        published, version_id: settings.version_id, version_created_at: settings.version_created_at, title, description_md: description, asset_ids: assets, metrics_public: metrics,
      }) });
      await reload(); toast.success(published ? "作品展示已发布" : "作品展示已撤回");
    } catch (failure) { setError(formatApiError(failure)); }
    finally { setBusy(false); }
  }
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!validateForm(event.currentTarget)) void save(true);
  }
  return <form className="form-panel showcase-form" onSubmit={submit} noValidate>
    <h2>{work.title}</h2><p>{work.team_name} · {work.problem_code} · <Link href={`/works/${work.id}`} target="_blank">查看评审材料</Link></p>
    <p>请单独编写公开介绍。原投稿正文、提交字段、运行日志和证据不会自动公开。</p>
    {!settings.competition_ended && <p className="inline-alert">比赛尚未结束，暂不能发布公开展示。</p>}
    <fieldset className="showcase-fields" disabled={busy}>
      <label className="form-field"><span>公开标题</span><input value={title} onChange={event => setTitle(event.target.value)} maxLength={180} required /></label>
      <label className="form-field"><span>公开作品介绍（Markdown）</span><textarea value={description} onChange={event => setDescription(event.target.value)} rows={10} maxLength={200000} required placeholder="填写可公开的成果介绍、图片或演示链接" /></label>
      {description && <section aria-label="公开介绍预览"><h3>公开介绍预览</h3><Markdown>{description}</Markdown></section>}
      <section><h3>公开附件</h3><p>默认不公开代码或附件，只勾选允许公众下载的文件。</p>{settings.assets.length ? settings.assets.map(asset => <label className="form-field" key={asset.id}><span><input type="checkbox" checked={assets.includes(asset.id)} onChange={event => setAssets(current => event.target.checked ? [...current, asset.id] : current.filter(id => id !== asset.id))} /> {asset.original_name}</span></label>) : <p>没有附件。</p>}</section>
      <label className="form-field"><span><input type="checkbox" checked={metrics} onChange={event => setMetrics(event.target.checked)} /> 公开详细指标</span><small>公开所选正式结果的指标数值；日志、轨迹、私有场景和原始证据仍受权限保护。</small></label>
      <FieldError>{error}</FieldError>
      <div className="button-row"><button className="primary-button" disabled={!settings.competition_ended || busy}>{busy ? "正在保存" : settings.publication.published ? "更新公开展示" : "发布公开展示"}</button>{settings.publication.published && <button type="button" className="outline-button" disabled={busy} onClick={() => void save(false)}>撤回公开展示</button>}</div>
    </fieldset>
  </form>;
}

export default function ManageWorksPage() {
  const { user, loading: sessionLoading } = useSession();
  const allowed = Boolean(user && ["admin", "organizer"].includes(user.role));
  const catalog = useApiResource<Competition[]>(allowed ? "/manage/catalog" : null);
  const [selectedCompetition, setCompetition] = useState("");
  const competitionId = selectedCompetition || catalog.data?.[0]?.id || "";
  const works = useApiResource<ManagedSubmission[]>(allowed && competitionId ? `/manage/submissions?competition_id=${competitionId}` : null);
  const [selectedWork, setWork] = useState("");
  const work = works.data?.find(item => item.id === selectedWork) ?? works.data?.[0];
  const settings = useApiResource<Settings>(allowed && work ? `/manage/submissions/${work.id}/publication` : null);
  async function reload() { await Promise.all([works.reload(), settings.reload()]); }
  return <AppShell title="作品展示" eyebrow="POST-COMPETITION SHOWCASE">
    {sessionLoading ? <PageLoading /> : !allowed ? <PageError message="当前账户没有作品发布权限。" /> : <>
      <div className="page-intro"><p>比赛结束后明确发布作品介绍。附件与指标分别选择是否公开，可随时撤回。</p></div>
      <div className="form-panel"><label className="form-field"><span>赛事</span><select value={competitionId} onChange={event => { setCompetition(event.target.value); setWork(""); }}>{catalog.data?.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label><label className="form-field"><span>正式作品</span><select value={work?.id ?? ""} onChange={event => setWork(event.target.value)}>{works.data?.map(item => <option key={item.id} value={item.id}>{item.title} · {item.team_name}{item.publication?.published ? "（已公开）" : ""}</option>)}</select></label></div>
      {catalog.error || works.error || settings.error ? <PageError message={catalog.error || works.error || settings.error || "读取失败"} retry={() => void reload()} /> : catalog.loading || works.loading || settings.loading ? <PageLoading /> : !work || !settings.data ? <EmptyState title="还没有正式提交的作品" description="参赛作品正式提交后会出现在这里。" /> : <PublicationForm key={`${work.id}-${JSON.stringify(settings.data)}`} work={work} settings={settings.data} reload={reload} />}
    </>}
  </AppShell>;
}
