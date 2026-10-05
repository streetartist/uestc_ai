"use client";
import { FormEvent, useState } from "react";
import { useSearchParams } from "next/navigation";
import { ArrowRight, LockKeyhole } from "lucide-react";
import { AppShell } from "@/app/components/AppShell";
import { EmailCodeField } from "@/app/components/EmailCodeField";
import { useSession } from "@/app/components/SessionProvider";
import { FieldError } from "@/app/components/ui";
import { api, formatApiError, jsonBody } from "@/app/lib/api";
import { validateForm } from "@/app/lib/formValidation";
type Mode = "login" | "register" | "reset";
const labels: Record<Mode, string> = { login: "登录", register: "创建账户", reset: "找回密码" };

export default function LoginPage() {
  const searchParams = useSearchParams();
  const { refresh } = useSession();
  const [mode, setMode] = useState<Mode>(searchParams.get("mode") === "reset" ? "reset" : "login");
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [inviteCode, setInviteCode] = useState("");
  const [code, setCode] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const externalEmail = mode === "register" && email.includes("@") && !email.toLowerCase().endsWith("@std.uestc.edu.cn");
  function switchMode(next: Mode) { setMode(next); setError(""); setNotice(""); setCode(""); setPassword(""); setConfirmation(""); }
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setError("");
    if (submitting || validateForm(event.currentTarget)) return;
    if (mode !== "login" && password !== confirmation) { setError("两次输入的密码不一致。"); return; }
    setSubmitting(true);
    try {
      await api(`/auth/${mode === "reset" ? "reset-password" : mode}`, { method: "POST", ...jsonBody(mode === "login" ? { email, password } : mode === "reset" ? { email, verification_code: code, new_password: password, confirm_password: confirmation } : { email, password, confirm_password: confirmation, name, verification_code: code, invite_code: inviteCode }) });
      if (mode === "reset") { switchMode("login"); setNotice("密码已重置，请使用新密码登录。"); return; }
      await refresh();
      const next = searchParams.get("next");
      window.location.assign(next?.startsWith("/") && !next.startsWith("//") && !next.includes("\\") ? next : "/dashboard");
    } catch (failure) { setError(formatApiError(failure)); }
    finally { setSubmitting(false); }
  }
  return <AppShell title={labels[mode]} eyebrow="ACCOUNT"><div className="auth-layout">
    <section className="auth-note"><span>平台账户</span><h2>一个账户，连接队伍、作品与个人履历。</h2><p>学生可使用 <strong>@std.uestc.edu.cn</strong> 邮箱注册，其他邮箱需要管理员邀请码。</p><p>忘记密码时，通过注册邮箱验证身份即可重置。</p></section>
    <form className="form-panel auth-form" onSubmit={submit} noValidate>
      <div className="segmented-control" aria-label="账户操作">{(["login", "register"] as Mode[]).map((item) => <button type="button" className={mode === item ? "selected" : ""} key={item} onClick={() => switchMode(item)}>{item === "login" ? "登录" : "注册"}</button>)}</div>
      {mode === "reset" && <h2>通过邮箱找回密码</h2>}
      {mode === "register" && <label className="form-field"><span>姓名或昵称</span><input value={name} onChange={(event) => setName(event.target.value)} maxLength={80} autoComplete="name" required /></label>}
      <label className="form-field"><span>邮箱</span><input type="email" value={email} onChange={(event) => { setEmail(event.target.value); setCode(""); }} autoComplete="email" maxLength={255} required /></label>
      {externalEmail && <label className="form-field"><span>注册邀请码</span><input value={inviteCode} onChange={(event) => setInviteCode(event.target.value.toUpperCase().replace(/[^A-Z0-9]/g, ""))} minLength={12} maxLength={12} required /><small>非学生邮箱需要一次性邀请码。</small></label>}
      <label className="form-field"><span>{mode === "reset" ? "新密码" : "密码"}</span><input type="password" value={password} onChange={(event) => setPassword(event.target.value)} autoComplete={mode === "login" ? "current-password" : "new-password"} minLength={8} maxLength={128} required /><small>8～128 个字符</small></label>
      {mode !== "login" && <><label className="form-field"><span>确认密码</span><input type="password" value={confirmation} onChange={(event) => setConfirmation(event.target.value)} autoComplete="new-password" minLength={8} maxLength={128} required /></label><EmailCodeField key={`${mode}-${email}`} email={email} purpose={mode} inviteCode={inviteCode} value={code} onChange={setCode} /></>}
      <FieldError>{error}</FieldError>{notice && <p className="inline-alert" role="status">{notice}</p>}<button className="primary-button form-submit" disabled={submitting}><LockKeyhole size={16} />{submitting ? "正在处理" : mode === "login" ? "进入工作台" : mode === "reset" ? "重置密码" : "创建账户"}<ArrowRight size={16} /></button>
      {mode === "login" && <button type="button" className="text-button" onClick={() => switchMode("reset")}>忘记密码？通过邮箱找回</button>}
    </form></div></AppShell>;
}
