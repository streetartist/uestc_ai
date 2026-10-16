"use client";
import { UserLink } from "@/app/components/UserLink";

import { useMemo, useState } from "react";
import { Check, Search, ShieldCheck, Trash2 } from "lucide-react";
import { AdminTabs } from "@/app/components/AdminTabs";
import { AppShell } from "@/app/components/AppShell";
import { TypedDeleteDialog } from "@/app/components/TypedDeleteDialog";
import { useSession } from "@/app/components/SessionProvider";
import { useToast } from "@/app/components/ToastProvider";
import { EmptyState, FieldError, PageError, PageLoading, StatusPill } from "@/app/components/ui";
import { api, formatApiError, jsonBody } from "@/app/lib/api";
import type { User, UserDeletionImpact } from "@/app/lib/domain";
import { userImpactItems } from "@/app/lib/deletionLabels";
import { useApiResource } from "@/app/lib/useApiResource";

const roleLabels: Record<User["role"], string> = { member: "普通成员", reviewer: "评委", editor: "内容编辑", organizer: "赛事组织者", admin: "管理员" };

export default function AdminUsersPage() {
  const { user, loading: sessionLoading } = useSession();
  const toast = useToast();
  const allowed = user?.role === "admin";
  const { data, loading, error, reload } = useApiResource<User[]>(allowed ? "/admin/users" : null);
  const [query, setQuery] = useState("");
  const [formError, setFormError] = useState("");
  const [savedId, setSavedId] = useState("");
  const users = useMemo(() => (data ?? []).filter((item) => `${item.name} ${item.email} ${item.student_number ?? ""}`.toLowerCase().includes(query.toLowerCase())), [data, query]);

  const [target, setTarget] = useState<User | null>(null);
  const [impact, setImpact] = useState<UserDeletionImpact | null>(null);
  const [impactLoading, setImpactLoading] = useState(false);
  const [impactError, setImpactError] = useState("");
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState("");

  async function openDelete(item: User) {
    setTarget(item); setImpact(null); setImpactError(""); setDeleteError(""); setImpactLoading(true);
    try { setImpact(await api<UserDeletionImpact>(`/admin/users/${item.id}/deletion-impact`)); }
    catch (requestError) { setImpactError(formatApiError(requestError)); }
    finally { setImpactLoading(false); }
  }

  async function removeUser(confirm: string) {
    if (!target) return;
    setDeleting(true); setDeleteError("");
    try {
      await api(`/admin/users/${target.id}`, { method: "DELETE", headers: { "X-Confirm": encodeURIComponent(confirm) }, ...jsonBody({ confirm }) });
      toast.success(`用户 ${target.name} 已删除`);
      setTarget(null);
      await reload();
    } catch (requestError) { setDeleteError(formatApiError(requestError)); }
    finally { setDeleting(false); }
  }

  async function changeRole(target: User, role: User["role"]) {
    setFormError(""); setSavedId("");
    try { await api(`/admin/users/${target.id}`, { method: "PATCH", ...jsonBody({ role }) }); setSavedId(target.id); await reload(); toast.success(`${target.name} 的角色已更新为${roleLabels[role]}`); }
    catch (requestError) { setFormError(formatApiError(requestError)); }
  }

  if (sessionLoading) return <AppShell title="系统管理" eyebrow="ADMIN"><PageLoading /></AppShell>;
  if (!allowed) return <AppShell title="系统管理" eyebrow="ADMIN"><PageError message="当前账户没有系统管理权限。" /></AppShell>;
  return <AppShell title="系统管理" eyebrow="ADMINISTRATION"><AdminTabs /><div className="page-intro admin-intro"><p>角色决定用户能看到的工作区入口和可以执行的写操作。</p><label className="search-field"><Search size={15} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索姓名、邮箱或学号" /></label></div><FieldError>{formError}</FieldError>{loading ? <PageLoading /> : error ? <PageError message={error} retry={reload} /> : !users.length ? <EmptyState title="没有匹配用户" description="尝试其他关键词。" /> : <div className="admin-user-list">{users.map((item) => <div key={item.id}><div className="account-avatar">{item.name.slice(0, 1)}</div><div><strong><UserLink id={item.id} name={item.name} /></strong><span>{item.email}</span>{item.student_number && <small>学号：{item.student_number}</small>}</div><StatusPill tone={item.role === "admin" ? "live" : "neutral"}>{roleLabels[item.role]}</StatusPill><label><ShieldCheck size={14} /><select value={item.role} onChange={(event) => void changeRole(item, event.target.value as User["role"])}><option value="member">普通成员</option><option value="reviewer">评委</option><option value="editor">内容编辑</option><option value="organizer">赛事组织者</option><option value="admin">管理员</option></select></label>{savedId === item.id ? <Check className="role-saved" size={15} /> : <span />}<button type="button" className="danger-button subtle compact-button" onClick={() => void openDelete(item)} disabled={item.id === user?.id} title={item.id === user?.id ? "不能删除自己" : undefined} aria-label={`删除用户 ${item.name}`}><Trash2 size={14} />删除</button></div>)}</div>}<TypedDeleteDialog
      open={Boolean(target)}
      title={`删除用户“${target?.name ?? ""}”？`}
      warning={impact?.recommendation ?? "删除不可恢复。拥有队伍、作品或评审记录的账户不建议删除，如只需收回权限，请先把角色改为“普通成员”。"}
      impactItems={impact ? userImpactItems(impact.counts) : []}
      loading={impactLoading}
      loadError={impactError}
      blockers={impact?.blockers.map((entry) => entry.message) ?? []}
      expected={impact?.confirm_text ?? target?.email ?? ""}
      caseInsensitive
      inputLabel="请输入该用户的邮箱以确认"
      busy={deleting}
      error={deleteError}
      onCancel={() => { setTarget(null); setDeleteError(""); }}
      onConfirm={(text) => void removeUser(text)}
    /></AppShell>;
}
