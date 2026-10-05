"use client";

import Link from "next/link";
import { UserLinks } from "@/app/components/UserLink";
import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { ArrowRight, ClipboardCheck, Copy, LogIn, Plus, TicketCheck, Trash2, Users } from "lucide-react";
import { AppShell } from "@/app/components/AppShell";
import { ConfirmDialog } from "@/app/components/ConfirmDialog";
import { useSession } from "@/app/components/SessionProvider";
import { useToast } from "@/app/components/ToastProvider";
import { EmptyState, FieldError, PageLoading, StatusPill } from "@/app/components/ui";
import { api, formatApiError, jsonBody } from "@/app/lib/api";
import { copyText } from "@/app/lib/clipboard";
import type { Competition, Registration, Submission, Team } from "@/app/lib/domain";
import { validateForm } from "@/app/lib/formValidation";

export default function DashboardPage() {
  const { user, loading: sessionLoading } = useSession();
  const toast = useToast();
  const [teams, setTeams] = useState<Team[]>([]);
  const [registrations, setRegistrations] = useState<Registration[]>([]);
  const [submissions, setSubmissions] = useState<Submission[]>([]);
  const [competitions, setCompetitions] = useState<Competition[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [teamName, setTeamName] = useState("");
  const [competitionId, setCompetitionId] = useState("");
  const [inviteCode, setInviteCode] = useState("");
  const [deleteTarget, setDeleteTarget] = useState<null | { kind: "team" | "registration" | "submission"; id: string; title: string }>(null);
  const [deleteError, setDeleteError] = useState("");
  const [deleting, setDeleting] = useState(false);

  const load = useCallback(async () => {
    if (!user) return;
    setLoading(true);
    setError("");
    try {
      const [teamData, registrationData, submissionData, competitionData] = await Promise.all([
        api<Team[]>("/me/teams"), api<Registration[]>("/me/registrations"),
        api<Submission[]>("/me/submissions"), api<Competition[]>("/competitions?status=published"),
      ]);
      setTeams(teamData); setRegistrations(registrationData); setSubmissions(submissionData); setCompetitions(competitionData);
      setCompetitionId((current) => current || competitionData[0]?.id || "");
    } catch (requestError) {
      setError(formatApiError(requestError));
    } finally {
      setLoading(false);
    }
  }, [user]);

  useEffect(() => {
    let active = true;
    Promise.resolve().then(() => { if (active) return load(); });
    return () => { active = false; };
  }, [load]);

  const registrationsByTeam = useMemo(() => registrations.reduce<Record<string, Registration[]>>((result, item) => {
    (result[item.team_id] ??= []).push(item); return result;
  }, {}), [registrations]);

  async function createTeam(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setError("");
    const validationError = validateForm(event.currentTarget);
    if (validationError) return;
    try {
      await api("/teams", { method: "POST", ...jsonBody({ competition_id: competitionId, name: teamName }) });
      setTeamName(""); await load(); toast.success("队伍已创建，邀请码可发给队友");
    } catch (requestError) { setError(formatApiError(requestError)); }
  }

  async function joinTeam(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setError("");
    const validationError = validateForm(event.currentTarget);
    if (validationError) return;
    try {
      await api("/teams/join", { method: "POST", ...jsonBody({ invite_code: inviteCode }) });
      setInviteCode(""); await load(); toast.success("已加入队伍");
    } catch (requestError) { setError(formatApiError(requestError)); }
  }

  async function remove() {
    if (!deleteTarget) return;
    setDeleting(true); setDeleteError("");
    const endpoint = deleteTarget.kind === "team" ? `/teams/${deleteTarget.id}` : deleteTarget.kind === "registration" ? `/registrations/${deleteTarget.id}` : `/submissions/${deleteTarget.id}`;
    try {
      await api(endpoint, { method: "DELETE" });
      const successMessage = deleteTarget.kind === "team" ? "队伍已删除" : deleteTarget.kind === "registration" ? "报名已取消" : "草稿作品已删除";
      setDeleteTarget(null); await load(); toast.success(successMessage);
    } catch (requestError) { setDeleteError(formatApiError(requestError)); }
    finally { setDeleting(false); }
  }

  async function copyInviteCode(inviteCode: string) {
    try {
      await copyText(inviteCode);
      toast.success(`邀请码 ${inviteCode} 已复制`);
    } catch {
      toast.error("邀请码复制失败，请手动选择后复制");
    }
  }

  if (sessionLoading) return <AppShell title="工作台" eyebrow="DASHBOARD"><PageLoading label="正在确认账户" /></AppShell>;
  if (!user) return <AppShell title="工作台" eyebrow="DASHBOARD"><div className="sign-in-required"><LogIn size={22} /><h2>登录后管理参赛工作</h2><p>队伍、报名和提交记录只对账户本人可见。</p><Link className="primary-button" href="/login?next=/dashboard">登录或注册 <ArrowRight size={16} /></Link></div></AppShell>;

  return (
    <AppShell title={`${user.name}的工作台`} eyebrow="DASHBOARD" actions={<Link className="primary-button compact-button" href="/problems"><Plus size={15} />选择题目</Link>}>
      <div className="page-intro"><p>管理队伍、报名与作品。</p><Link className="outline-button" href={`/people/${user.id}`}>个人主页 · 修改密码 <ArrowRight size={15} /></Link></div>
      {loading ? <PageLoading /> : <>
        {error && <div className="inline-alert error">{error}</div>}
        <section className="dashboard-section">
          <header className="section-header"><div><span className="eyebrow"><i />TEAMS</span><h2>我的队伍</h2></div><span className="section-note">邀请码用于邀请成员，不要公开发布</span></header>
          {teams.length ? <div className="team-list">{teams.map((team) => <div className="team-row" key={team.id}>
            <div className="team-symbol"><Users size={18} /></div><div className="team-main"><strong>{team.name}</strong><span><UserLinks users={team.members ?? []} /></span></div>
            <button type="button" className="invite-code" title="复制邀请码" onClick={() => void copyInviteCode(team.invite_code)}><Copy size={13} />{team.invite_code}</button>
            <div className="team-registrations">{(registrationsByTeam[team.id] ?? []).map((registration) => <span className="registration-chip" key={registration.id}><StatusPill tone="live">{registration.track_name}</StatusPill><button type="button" title={`取消 ${registration.track_name} 报名`} aria-label={`取消 ${registration.track_name} 报名`} onClick={() => setDeleteTarget({ kind: "registration", id: registration.id, title: `${team.name} / ${registration.track_name}` })}><Trash2 size={12} /></button></span>)}{!registrationsByTeam[team.id]?.length && <StatusPill tone="muted">尚未报名</StatusPill>}</div>
            {team.captain_id === user.id ? <button type="button" className="icon-button team-delete" title="删除队伍" aria-label={`删除队伍 ${team.name}`} onClick={() => setDeleteTarget({ kind: "team", id: team.id, title: team.name })}><Trash2 size={15} /></button> : <span />}
          </div>)}</div> : <EmptyState title="还没有队伍" description="可以创建自己的队伍，或使用邀请码加入。" />}
          <div className="team-actions-grid">
            <form className="form-panel compact-form" onSubmit={createTeam} noValidate><h3>创建队伍</h3><label className="form-field"><span>赛事</span><select value={competitionId} onChange={(event) => setCompetitionId(event.target.value)} required>{competitions.map((item) => <option value={item.id} key={item.id}>{item.name}</option>)}</select></label><label className="form-field"><span>队伍名称</span><input value={teamName} onChange={(event) => setTeamName(event.target.value)} required /></label><button className="outline-button" disabled={!competitionId}><Plus size={15} />创建</button></form>
            <form className="form-panel compact-form" onSubmit={joinTeam} noValidate><h3>加入队伍</h3><label className="form-field"><span>8 位邀请码</span><input value={inviteCode} onChange={(event) => setInviteCode(event.target.value.toUpperCase())} minLength={8} maxLength={8} required /></label><p>加入后即可和队友共享报名与作品记录。</p><button className="outline-button"><TicketCheck size={15} />加入</button></form>
          </div>
          <FieldError>{error}</FieldError>
        </section>
        <section className="dashboard-section">
          <header className="section-header"><div><span className="eyebrow"><i />SUBMISSIONS</span><h2>我的作品</h2></div><Link href="/problems">从题库新建提交 <ArrowRight size={15} /></Link></header>
          {!submissions.length ? <EmptyState title="还没有作品" description="进入具体题目后创建第一份 README 提交。" /> : <div className="submission-list">{submissions.map((submission) => { const isDraft = submission.status === "draft"; return <div key={submission.id}><Link href={isDraft ? `/submit/${submission.problem_slug}?submission=${submission.id}` : `/works/${submission.id}`}>
            <ClipboardCheck size={18} /><div><strong>{submission.title}</strong><span>{submission.problem_code} · {submission.team_name}</span></div><StatusPill tone={isDraft ? "warm" : "live"}>{isDraft ? "草稿" : "已正式提交"}</StatusPill><ArrowRight size={16} />
          </Link>{submission.status === "draft" && <button type="button" className="icon-button submission-delete" title="删除草稿作品" aria-label={`删除草稿作品 ${submission.title}`} onClick={() => setDeleteTarget({ kind: "submission", id: submission.id, title: submission.title })}><Trash2 size={15} /></button>}</div>; })}</div>}
        </section>
      </>}
      <ConfirmDialog open={Boolean(deleteTarget)} title={`删除“${deleteTarget?.title ?? ""}”？`} description={deleteTarget?.kind === "team" ? "只有没有报名和作品记录的队伍可以删除。队员关系与邀请码会同时失效。" : deleteTarget?.kind === "registration" ? "只有尚未产生作品的报名可以取消，之后仍可重新报名。" : "只有未送审、未计分的草稿作品可以删除，草稿版本和附件会一并移除。"} busy={deleting} error={deleteError} onCancel={() => { setDeleteTarget(null); setDeleteError(""); }} onConfirm={() => void remove()} />
    </AppShell>
  );
}
