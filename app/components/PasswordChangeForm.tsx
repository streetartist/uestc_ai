"use client";
import { FormEvent, useState } from "react";
import { FieldError } from "@/app/components/ui";
import { api, formatApiError, jsonBody } from "@/app/lib/api";
import { validateForm } from "@/app/lib/formValidation";

export function PasswordChangeForm({ userId }: { userId: string }) {
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setError("");
    if (saving || validateForm(event.currentTarget)) return;
    if (newPassword !== confirmation) { setError("两次输入的新密码不一致。"); return; }
    if (newPassword === currentPassword) { setError("新密码不能与当前密码相同。"); return; }
    setSaving(true);
    try {
      await api("/auth/password", { method: "POST", ...jsonBody({ current_password: currentPassword, new_password: newPassword, confirm_password: confirmation }) });
      window.sessionStorage.setItem("uestc-ai-flash", "密码已修改，请使用新密码重新登录。");
      window.location.replace(`/login?next=/people/${userId}`);
    } catch (failure) { setError(formatApiError(failure)); setSaving(false); }
  }
  return <form className="form-panel account-password-form" onSubmit={submit} noValidate>
    <header><h2>修改密码</h2><p>新密码需为 8～128 个字符。修改后，所有设备上的已有登录都会退出。</p></header>
    <label className="form-field"><span>当前密码</span><input type="password" autoComplete="current-password" value={currentPassword} onChange={(event) => setCurrentPassword(event.target.value)} required /></label>
    <label className="form-field"><span>新密码</span><input type="password" autoComplete="new-password" value={newPassword} onChange={(event) => setNewPassword(event.target.value)} minLength={8} maxLength={128} required /></label>
    <label className="form-field"><span>确认新密码</span><input type="password" autoComplete="new-password" value={confirmation} onChange={(event) => setConfirmation(event.target.value)} minLength={8} maxLength={128} required /></label>
    <FieldError>{error}</FieldError><button className="primary-button" disabled={saving}>{saving ? "正在保存" : "修改密码"}</button>
  </form>;
}
