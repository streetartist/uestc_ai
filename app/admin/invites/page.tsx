"use client";
import { UserLink } from "@/app/components/UserLink";

import { FormEvent, useState } from "react";
import { Ban, Check, Copy, KeyRound, Plus } from "lucide-react";
import { AdminTabs } from "@/app/components/AdminTabs";
import { AppShell } from "@/app/components/AppShell";
import { ConfirmDialog } from "@/app/components/ConfirmDialog";
import { useSession } from "@/app/components/SessionProvider";
import { useToast } from "@/app/components/ToastProvider";
import { EmptyState, FieldError, PageError, PageLoading, StatusPill } from "@/app/components/ui";
import { api, formatApiError, jsonBody } from "@/app/lib/api";
import { copyText } from "@/app/lib/clipboard";
import type { RegistrationInvite } from "@/app/lib/domain";
import { validateForm } from "@/app/lib/formValidation";
import { formatBeijing } from "@/app/lib/time";
import { useApiResource } from "@/app/lib/useApiResource";

const statusLabels: Record<RegistrationInvite["status"], string> = {
  active: "可使用",
  used: "已使用",
  expired: "已过期",
  revoked: "已撤销",
};

function formatDate(value: string) {
  return formatBeijing(value, { dateStyle: "medium", timeStyle: "short" });
}

export default function AdminInvitesPage() {
  const { user, loading: sessionLoading } = useSession();
  const toast = useToast();
  const allowed = user?.role === "admin";
  const { data, loading, error, reload } = useApiResource<RegistrationInvite[]>(allowed ? "/admin/registration-invites" : null);
  const [note, setNote] = useState("");
  const [expiresInDays, setExpiresInDays] = useState("30");
  const [newCode, setNewCode] = useState("");
  const [creating, setCreating] = useState(false);
  const [formError, setFormError] = useState("");
  const [revokeTarget, setRevokeTarget] = useState<RegistrationInvite | null>(null);
  const [revoking, setRevoking] = useState(false);
  const [revokeError, setRevokeError] = useState("");

  async function createInvite(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFormError(""); setNewCode("");
    if (validateForm(event.currentTarget)) return;
    setCreating(true);
    try {
      const result = await api<RegistrationInvite & { code: string }>("/admin/registration-invites", {
        method: "POST",
        ...jsonBody({ note, expires_in_days: Number(expiresInDays) }),
      });
      setNewCode(result.code);
      setNote("");
      await reload();
      toast.success("注册邀请码已生成");
    } catch (requestError) {
      setFormError(formatApiError(requestError));
    } finally {
      setCreating(false);
    }
  }

  async function copyCode() {
    try {
      await copyText(newCode);
      toast.success("注册邀请码已复制");
    } catch {
      toast.error("复制失败，请手动选择邀请码");
    }
  }

  async function revokeInvite() {
    if (!revokeTarget) return;
    setRevoking(true); setRevokeError("");
    try {
      await api(`/admin/registration-invites/${revokeTarget.id}`, { method: "DELETE" });
      setRevokeTarget(null);
      await reload();
      toast.success("注册邀请码已撤销");
    } catch (requestError) {
      setRevokeError(formatApiError(requestError));
    } finally {
      setRevoking(false);
    }
  }

  if (sessionLoading) return <AppShell title="注册邀请码" eyebrow="ADMIN"><PageLoading /></AppShell>;
  if (!allowed) return <AppShell title="注册邀请码" eyebrow="ADMIN"><PageError message="当前账户没有系统管理权限。" /></AppShell>;
  return <AppShell title="注册邀请码" eyebrow="ADMINISTRATION">
    <AdminTabs />
    <div className="invite-admin-layout">
      <section className="invite-create-panel">
        <header><span className="eyebrow"><i />NEW INVITE</span><h2>生成一次性邀请码</h2></header>
        <form onSubmit={createInvite} noValidate>
          <label className="form-field"><span>用途备注</span><input value={note} onChange={(event) => setNote(event.target.value)} maxLength={160} placeholder="例如：校友评委、合作社团成员" /></label>
          <label className="form-field"><span>有效天数</span><input type="number" min="1" max="365" value={expiresInDays} onChange={(event) => setExpiresInDays(event.target.value)} required /></label>
          <FieldError>{formError}</FieldError>
          <button className="primary-button" disabled={creating}><Plus size={15} />{creating ? "正在生成" : "生成邀请码"}</button>
        </form>
        {newCode && <div className="new-invite-code" role="status"><Check size={17} /><div><strong>请立即保存</strong><code>{newCode}</code><small>完整邀请码只显示这一次，使用后自动失效。</small></div><button type="button" className="icon-button" title="复制邀请码" aria-label="复制邀请码" onClick={() => void copyCode()}><Copy size={16} /></button></div>}
      </section>
      <aside className="invite-policy-note"><KeyRound size={19} /><strong>注册规则</strong><p><b>@std.uestc.edu.cn</b> 学生邮箱验证后可直接注册。其他邮箱必须先取得一个有效邀请码，再接收邮箱验证码。</p><p>邀请码只能使用一次；验证码仍会发送到注册邮箱，邀请码不能替代邮箱验证。</p></aside>
    </div>
    <section className="dashboard-section invite-history"><header className="section-header"><div><span className="eyebrow"><i />HISTORY</span><h2>邀请码记录</h2></div></header>{loading ? <PageLoading /> : error ? <PageError message={error} retry={reload} /> : !data?.length ? <EmptyState title="还没有邀请码" description="生成后会在这里显示状态和使用记录。" /> : <div className="invite-list">{data.map((invite) => <div key={invite.id}><StatusPill tone={invite.status === "active" ? "live" : invite.status === "used" ? "neutral" : "muted"}>{statusLabels[invite.status]}</StatusPill><div><strong>{invite.code_prefix} ···· ····</strong><span>{invite.note || "无备注"}</span></div><div><strong>{invite.used_by_email ? <UserLink id={invite.used_by} name={invite.used_by_email} /> : `有效至 ${formatDate(invite.expires_at)}`}</strong><span><UserLink id={invite.created_by} name={invite.created_by_name ?? "管理员"} /> · {formatDate(invite.created_at)}</span></div>{invite.status === "active" ? <button className="icon-button" type="button" title="撤销邀请码" aria-label={`撤销邀请码 ${invite.code_prefix}`} onClick={() => setRevokeTarget(invite)}><Ban size={15} /></button> : <span />}</div>)}</div>}</section>
    <ConfirmDialog open={Boolean(revokeTarget)} title={`撤销邀请码 ${revokeTarget?.code_prefix ?? ""}…？`} description="撤销后该邀请码将立即失效，尚未完成的外部邮箱注册无法继续。" confirmLabel="确认撤销" busy={revoking} error={revokeError} onCancel={() => { setRevokeTarget(null); setRevokeError(""); }} onConfirm={() => void revokeInvite()} />
  </AppShell>;
}
