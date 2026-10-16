"use client";
import { useEffect, useState } from "react";
import { api, formatApiError, jsonBody } from "@/app/lib/api";
type Challenge = { id: string; image: string };

export function EmailCodeField({ email, purpose, inviteCode, value, onChange }: {
  email: string; purpose: "register" | "reset"; inviteCode?: string; value: string; onChange: (value: string) => void;
}) {
  const [challenge, setChallenge] = useState<Challenge | null>(null);
  const [digits, setDigits] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [cooldown, setCooldown] = useState(0);
  const [sent, setSent] = useState(false);
  const [challengeUsed, setChallengeUsed] = useState(false);
  useEffect(() => {
    if (!cooldown) return;
    const timer = window.setTimeout(() => setCooldown(cooldown - 1), 1000);
    return () => window.clearTimeout(timer);
  }, [cooldown]);
  async function refreshCaptcha() {
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.trim())) {
      setError("获取数字验证码前，请先填写有效的邮箱地址。"); return;
    }
    setBusy(true); setError(""); setChallenge(null); setDigits("");
    setChallengeUsed(false); setSent(false);
    try { setChallenge(await api<Challenge>("/auth/captcha", { method: "POST", ...jsonBody({ email, purpose }) })); }
    catch (failure) { setError(formatApiError(failure)); }
    finally { setBusy(false); }
  }
  async function send() {
    if (challengeUsed || !challenge || !/^\d{6}$/.test(digits)) { setError("请先输入新图片中的 6 位数字验证码。"); return; }
    setBusy(true); setError("");
    try {
      const result = await api<{ retry_after: number }>("/auth/verification-codes", { method: "POST", ...jsonBody({ email, purpose, invite_code: inviteCode, captcha_id: challenge.id, captcha_code: digits }) });
      setCooldown(result.retry_after); setSent(true); setChallengeUsed(true);
    } catch (failure) { setError(formatApiError(failure)); setChallengeUsed(true); }
    finally { setBusy(false); }
  }
  return <div className="email-code-fields"><div className="captcha-row">
    <label className="form-field"><span>数字验证码</span><input aria-label="数字验证码" disabled={busy || challengeUsed} inputMode="numeric" autoComplete="off" maxLength={6} value={digits} onChange={(event) => setDigits(event.target.value.replace(/\D/g, ""))} placeholder="输入图片中的 6 位数字" /></label>
    <button type="button" className="captcha-image" disabled={busy || cooldown > 0} onClick={() => void refreshCaptcha()} aria-busy={busy} aria-label={challenge ? "更换数字验证码" : "获取数字验证码"}>
      {/* eslint-disable-next-line @next/next/no-img-element */}
      {challenge ? <img src={challenge.image} alt="6 位数字验证码，点击更换" width={180} height={56} /> : <span>{busy ? "正在获取…" : "获取数字验证码"}</span>}
    </button></div>
    <div className="verification-control"><label className="form-field"><span>邮箱验证码</span><input inputMode="numeric" autoComplete="one-time-code" pattern="[0-9]{6}" maxLength={6} value={value} onChange={(event) => onChange(event.target.value.replace(/\D/g, ""))} placeholder="邮件中的 6 位数字" required /></label><button type="button" className="outline-button" disabled={busy || cooldown > 0 || challengeUsed || !challenge || !/^\d{6}$/.test(digits)} onClick={() => void send()}>{busy ? "正在处理" : cooldown > 0 ? `${cooldown} 秒后重发` : "发送邮件验证码"}</button></div>
    <small className={`email-code-feedback${error ? " is-error" : ""}`} role={error ? "alert" : "status"} aria-live="polite">
      {error || (sent ? `${purpose === "reset" ? "若邮箱已注册，邮箱验证码已发送" : "数字验证已通过，邮箱验证码已发送"}（10分钟有效）。重发前请点击图片重新验证。` : challengeUsed ? "数字验证码已使用，请点击图片获取新数字验证码。" : challenge ? "输入图片中的 6 位数字，再发送邮箱验证码。" : "先获取数字验证码图片，填写其中的 6 位数字，再发送邮箱验证码。")}
    </small>
  </div>;
}
