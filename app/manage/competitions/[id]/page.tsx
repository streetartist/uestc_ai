"use client";

import Link from "next/link";
import { FormEvent, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { ArrowLeft, ArrowRight, FilePlus2, Plus, Save, Trash2 } from "lucide-react";
import { AppShell } from "@/app/components/AppShell";
import { ConfirmDialog } from "@/app/components/ConfirmDialog";
import { MarkdownEditor } from "@/app/components/MarkdownEditor";
import { useSession } from "@/app/components/SessionProvider";
import { useToast } from "@/app/components/ToastProvider";
import { EmptyState, FieldError, PageError, PageLoading, StatusPill } from "@/app/components/ui";
import { api, formatApiError, jsonBody } from "@/app/lib/api";
import type { Competition, Track } from "@/app/lib/domain";
import { validateForm } from "@/app/lib/formValidation";
import { beijingDateTimeLocal, beijingLocalToIso } from "@/app/lib/time";
import { useApiResource } from "@/app/lib/useApiResource";

function localDate(value?: string | null) {
  return beijingDateTimeLocal(value);
}

function isoDate(value: string) {
  return beijingLocalToIso(value);
}

function CompetitionEditor({ competition, reload, onDeleted }: {
  competition: Competition;
  reload: () => Promise<void>;
  onDeleted: () => void;
}) {
  const [name, setName] = useState(competition.name);
  const [slug, setSlug] = useState(competition.slug);
  const [summary, setSummary] = useState(competition.summary);
  const [overview, setOverview] = useState(competition.config.overview_md ?? "");
  const [status, setStatus] = useState(competition.status);
  const [registrationOpens, setRegistrationOpens] = useState(localDate(competition.registration_opens_at));
  const [registrationCloses, setRegistrationCloses] = useState(localDate(competition.registration_closes_at));
  const [starts, setStarts] = useState(localDate(competition.starts_at));
  const [ends, setEnds] = useState(localDate(competition.ends_at));
  const [teamMin, setTeamMin] = useState(competition.config.team_size?.min ?? 1);
  const [teamMax, setTeamMax] = useState(competition.config.team_size?.max ?? 4);
  const [submissionLimit, setSubmissionLimit] = useState(competition.config.submission_limit ?? 10);
  const [checkpoints, setCheckpoints] = useState(competition.config.checkpoints ?? []);
  const [error, setError] = useState("");
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState("");
  const toast = useToast();

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    const validationError = validateForm(event.currentTarget);
    if (validationError) return;
    try {
      await api(`/manage/competitions/${competition.id}`, {
        method: "PATCH",
        ...jsonBody({
          name,
          slug,
          summary,
          status,
          registration_opens_at: isoDate(registrationOpens),
          registration_closes_at: isoDate(registrationCloses),
          starts_at: isoDate(starts),
          ends_at: isoDate(ends),
          config: {
            ...competition.config,
            overview_md: overview,
            team_size: { min: teamMin, max: teamMax },
            submission_limit: submissionLimit,
            checkpoints,
          },
        }),
      });
      await reload();
      toast.success("赛事设置已保存");
    } catch (requestError) {
      setError(formatApiError(requestError));
    }
  }

  async function remove() {
    setDeleting(true);
    setDeleteError("");
    try {
      await api(`/manage/competitions/${competition.id}`, { method: "DELETE" });
      toast.success("赛事已删除");
      onDeleted();
    } catch (requestError) {
      setDeleteError(formatApiError(requestError));
    } finally {
      setDeleting(false);
    }
  }

  return <>
    <form className="manage-form" onSubmit={save} noValidate>
      <div className="manage-form-header">
        <div>
          <StatusPill tone={status === "published" ? "live" : status === "draft" ? "warm" : "muted"}>
            {status === "published" ? "已发布" : status === "draft" ? "草稿" : status}
          </StatusPill>
          <h2>赛事设置</h2>
        </div>
        <div className="manage-row-actions">
          <button type="button" className="danger-button subtle compact-button" onClick={() => setDeleteOpen(true)}><Trash2 size={14} />删除赛事</button>
          <button className="primary-button compact-button"><Save size={15} />保存赛事</button>
        </div>
      </div>
      <div className="form-grid">
        <label className="form-field"><span>赛事名称</span><input value={name} onChange={(event) => setName(event.target.value)} required /></label>
        <label className="form-field"><span>URL 标识</span><input value={slug} onChange={(event) => setSlug(event.target.value)} required /></label>
      </div>
      <label className="form-field"><span>赛事简介</span><textarea value={summary} onChange={(event) => setSummary(event.target.value)} rows={3} required /></label>
      <MarkdownEditor label="赛事介绍与细则" value={overview} onChange={setOverview} height={420} help="填写主承办单位、实施计划、评分规则、赛事流程与宣传安排；内容会显示在赛事详情页。" />
      <div className="form-grid four-fields">
        <label className="form-field"><span>报名开放</span><input type="datetime-local" value={registrationOpens} onChange={(event) => setRegistrationOpens(event.target.value)} /></label>
        <label className="form-field"><span>报名截止</span><input type="datetime-local" value={registrationCloses} onChange={(event) => setRegistrationCloses(event.target.value)} /></label>
        <label className="form-field"><span>比赛开始</span><input type="datetime-local" value={starts} onChange={(event) => setStarts(event.target.value)} /></label>
        <label className="form-field"><span>比赛结束</span><input type="datetime-local" value={ends} onChange={(event) => setEnds(event.target.value)} /></label>
      </div>
      <div className="form-grid four-fields">
        <label className="form-field"><span>状态</span><select value={status} onChange={(event) => setStatus(event.target.value)}><option value="draft">草稿</option><option value="published">发布</option><option value="archived">归档</option></select></label>
        <label className="form-field"><span>最少队员</span><input type="number" min="1" value={teamMin} onChange={(event) => setTeamMin(Number(event.target.value))} /></label>
        <label className="form-field"><span>最多队员</span><input type="number" min={teamMin} value={teamMax} onChange={(event) => setTeamMax(Number(event.target.value))} /></label>
        <label className="form-field"><span>提交次数</span><input type="number" min="1" value={submissionLimit} onChange={(event) => setSubmissionLimit(Number(event.target.value))} /></label>
      </div>
      <FieldError>{error}</FieldError>
      {checkpoints.length > 0 && <section><h3>Checkpoint 进度记录</h3><p>仅用于交流反馈，不扣自测次数。留空日期显示“待公布”。</p><div className="form-grid">{checkpoints.map((point, index) => <label className="form-field" key={point.id}><span>{point.label} · 截止时间</span><input type="datetime-local" value={localDate(point.due_at)} onChange={(event) => setCheckpoints(checkpoints.map((item, i) => i === index ? { ...item, due_at: isoDate(event.target.value) } : item))} /></label>)}</div><Link href="/manage/checkpoints" className="text-button">查看队伍进度与填写反馈 <ArrowRight size={15} /></Link></section>}
    </form>
    <ConfirmDialog open={deleteOpen} title={`删除赛事“${competition.name}”？`} description="没有参赛记录时，赛事及其赛道和赛题会一并删除。已有队伍、报名或成绩的赛事只能归档。" busy={deleting} error={deleteError} onCancel={() => { setDeleteOpen(false); setDeleteError(""); }} onConfirm={() => void remove()} />
  </>;
}

function TrackEditor({ track, competitionId, reload }: {
  track: Track;
  competitionId: string;
  reload: () => Promise<void>;
}) {
  const [name, setName] = useState(track.name);
  const [slug, setSlug] = useState(track.slug);
  const [description, setDescription] = useState(track.description);
  const [short, setShort] = useState(track.config.short ?? "");
  const [accent, setAccent] = useState(track.config.accent ?? "ink");
  const [error, setError] = useState("");
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState("");
  const toast = useToast();

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    const validationError = validateForm(event.currentTarget);
    if (validationError) return;
    try {
      await api(`/manage/tracks/${track.id}`, {
        method: "PATCH",
        ...jsonBody({ name, slug, description, config: { ...track.config, short, accent } }),
      });
      await reload();
      toast.success(`赛道“${name}”已保存`);
    } catch (requestError) {
      setError(formatApiError(requestError));
    }
  }

  async function remove() {
    setDeleting(true);
    setDeleteError("");
    try {
      await api(`/manage/tracks/${track.id}`, { method: "DELETE" });
      setDeleteOpen(false);
      await reload();
      toast.success(`赛道“${track.name}”已删除`);
    } catch (requestError) {
      setDeleteError(formatApiError(requestError));
    } finally {
      setDeleting(false);
    }
  }

  return <section className="track-manage-block">
    <form onSubmit={save} noValidate>
      <div className="track-manage-head">
        <div><span className="mono-label">{short || "TRACK"}</span><h3>{track.name}</h3></div>
        <div className="manage-row-actions">
          <button type="button" className="danger-button subtle compact-button" onClick={() => setDeleteOpen(true)} aria-label={`删除赛道 ${track.name}`} title="删除赛道"><Trash2 size={14} /></button>
          <button className="outline-button"><Save size={14} />保存赛道</button>
        </div>
      </div>
      <div className="form-grid">
        <label className="form-field"><span>赛道名称</span><input value={name} onChange={(event) => setName(event.target.value)} required /></label>
        <label className="form-field"><span>URL 标识</span><input value={slug} onChange={(event) => setSlug(event.target.value)} required /></label>
      </div>
      <label className="form-field"><span>赛道介绍</span><textarea value={description} onChange={(event) => setDescription(event.target.value)} rows={2} /></label>
      <div className="form-grid">
        <label className="form-field"><span>简称</span><input value={short} onChange={(event) => setShort(event.target.value)} /></label>
        <label className="form-field"><span>主题色</span><select value={accent} onChange={(event) => setAccent(event.target.value)}><option value="coral">朱红</option><option value="ink">墨黑</option><option value="sage">鼠尾草</option><option value="sand">砂金</option></select></label>
      </div>
      <FieldError>{error}</FieldError>
    </form>
    <div className="managed-problems">
      <header><span>赛题 · {track.problems?.length ?? 0}</span><Link className="primary-button compact-button" href={`/manage/problems/new?track=${track.id}&competition=${competitionId}`}><FilePlus2 size={14} />发布赛题</Link></header>
      {track.problems?.length ? track.problems.map((problem) => <Link href={`/manage/problems/${problem.id}`} key={problem.id}><span className="problem-code-block">{problem.code}</span><div><strong>{problem.title}</strong><small>{problem.status === "published" ? "已发布" : problem.status === "draft" ? "草稿" : "候选"}</small></div><ArrowRight size={15} /></Link>) : <EmptyState title="这个赛道还没有题目" description="点击“发布赛题”开始创建。" />}
    </div>
    <ConfirmDialog open={deleteOpen} title={`删除赛道“${track.name}”？`} description="没有报名或提交记录时，赛道及其赛题会一并删除。已有参赛记录的赛道不能删除。" busy={deleting} error={deleteError} onCancel={() => { setDeleteOpen(false); setDeleteError(""); }} onConfirm={() => void remove()} />
  </section>;
}

export default function ManageCompetitionDetailPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const { user, loading: sessionLoading } = useSession();
  const toast = useToast();
  const allowed = user && ["organizer", "admin"].includes(user.role);
  const { data, loading, error, reload } = useApiResource<Competition[]>(allowed ? "/manage/catalog" : null);
  const competition = data?.find((item) => item.id === params.id);
  const [showTrack, setShowTrack] = useState(false);
  const [trackName, setTrackName] = useState("");
  const [trackSlug, setTrackSlug] = useState("");
  const [trackDescription, setTrackDescription] = useState("");
  const [formError, setFormError] = useState("");

  async function createTrack(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!competition) return;
    setFormError("");
    const validationError = validateForm(event.currentTarget);
    if (validationError) return;
    try {
      await api(`/competitions/${competition.slug}/tracks`, {
        method: "POST",
        ...jsonBody({ name: trackName, slug: trackSlug, description: trackDescription, position: (competition.tracks?.length ?? 0) + 1 }),
      });
      setTrackName("");
      setTrackSlug("");
      setTrackDescription("");
      setShowTrack(false);
      await reload();
      toast.success(`赛道“${trackName}”已创建`);
    } catch (requestError) {
      setFormError(formatApiError(requestError));
    }
  }

  if (sessionLoading || loading) return <AppShell title="赛事设置" eyebrow="WORKSPACE"><PageLoading /></AppShell>;
  if (!allowed) return <AppShell title="赛事设置" eyebrow="WORKSPACE"><PageError message="当前账户没有赛事管理权限。" /></AppShell>;
  if (error || !competition) return <AppShell title="赛事设置" eyebrow="WORKSPACE"><PageError message={error || "赛事不存在。"} retry={reload} /></AppShell>;

  return <AppShell title={competition.name} eyebrow="COMPETITION SETTINGS" actions={<Link className="outline-button" href="/manage/competitions"><ArrowLeft size={15} />赛事列表</Link>}>
    <CompetitionEditor key={competition.id} competition={competition} reload={reload} onDeleted={() => router.replace("/manage/competitions")} />
    <section className="dashboard-section">
      <header className="section-header"><div><span className="eyebrow"><i />TRACKS & PROBLEMS</span><h2>赛道与赛题</h2></div><button className="outline-button" onClick={() => setShowTrack((value) => !value)}><Plus size={15} />新建赛道</button></header>
      {showTrack && <form className="manage-create-band track-create-band" onSubmit={createTrack} noValidate>
        <label className="form-field"><span>赛道名称</span><input value={trackName} onChange={(event) => setTrackName(event.target.value)} required /></label>
        <label className="form-field"><span>URL 标识</span><input value={trackSlug} onChange={(event) => setTrackSlug(event.target.value.toLowerCase().replace(/[^a-z0-9-]/g, "-"))} required /></label>
        <label className="form-field grow-field"><span>赛道简介</span><input value={trackDescription} onChange={(event) => setTrackDescription(event.target.value)} /></label>
        <button className="primary-button">创建赛道</button>
        <FieldError>{formError}</FieldError>
      </form>}
      <div className="track-manage-list">{competition.tracks?.map((track) => <TrackEditor key={`${track.id}-${JSON.stringify(track.config)}`} track={track} competitionId={competition.id} reload={reload} />)}</div>
    </section>
  </AppShell>;
}
