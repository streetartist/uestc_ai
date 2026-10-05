"use client";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { FormEvent, useState } from "react";
import { AppShell } from "@/app/components/AppShell";
import { MarkdownEditor } from "@/app/components/MarkdownEditor";
import { useSession } from "@/app/components/SessionProvider";
import { useToast } from "@/app/components/ToastProvider";
import { EmptyState, FieldError, PageError, PageLoading, StatusPill } from "@/app/components/ui";
import { api, formatApiError, jsonBody } from "@/app/lib/api";
import type { ContentItem } from "@/app/lib/domain";
import { useApiResource } from "@/app/lib/useApiResource";
import { validateForm } from "@/app/lib/formValidation";

const statuses: Record<string, string> = { draft: "草稿", pending: "待审核", rejected: "需修改", published: "已发布" };
function ContributionForm({ item, saved }: { item?: ContentItem; saved: (id: string) => Promise<void> }) {
  const params = useSearchParams();
  const [kind, setKind] = useState(item?.kind ?? (params.get("kind") === "work" ? "work" : "blog"));
  const [title, setTitle] = useState(item?.title ?? "");
  const [excerpt, setExcerpt] = useState(item?.excerpt ?? "");
  const [body, setBody] = useState(item?.body_md ?? "");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState("draft");
  const [message, setMessage] = useState("");
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setError(""); setMessage("");
    if (busy || validateForm(event.currentTarget)) return;
    setBusy(true);
    try {
      const result = await api<ContentItem>(item ? `/me/contributions/${item.id}` : "/me/contributions", { method: item ? "PATCH" : "POST", ...jsonBody({ kind, title, excerpt, body_md: body, status }) });
      await saved(result.id); setMessage(status === "pending" ? "投稿已提交，审核通过后公开。" : "草稿已保存。");
    } catch (failure) { setError(formatApiError(failure)); }
    finally { setBusy(false); }
  }
  return <form className="form-panel contribution-form" onSubmit={submit} noValidate><h2>{item ? "编辑投稿" : "新投稿"}</h2>
    <p>欢迎分享技术文章与独立作品，不需要报名比赛。审核通过后展示在个人主页与资讯或作品档案中。已发布内容修改后需要重新审核。</p>
    {item?.review_note && <div className="inline-alert">审核意见：{item.review_note}</div>}
    <label className="form-field"><span>投稿类型</span><select value={kind} onChange={(event) => setKind(event.target.value)}><option value="blog">文章</option><option value="work">作品（含非比赛作品）</option></select></label>
    <label className="form-field"><span>标题</span><input value={title} onChange={(event) => setTitle(event.target.value)} maxLength={240} required /></label>
    <label className="form-field"><span>摘要</span><textarea value={excerpt} onChange={(event) => setExcerpt(event.target.value)} maxLength={2000} rows={3} /></label>
    <MarkdownEditor label="正文 / 作品说明" name="contribution-body" value={body} onChange={setBody} scope="contribution" required height={450} help="可以添加图片、附件、代码仓库和演示链接。附件随投稿审核通过后公开。" />
    <div className="segmented-control"><button type="button" className={status === "draft" ? "selected" : ""} onClick={() => setStatus("draft")}>保存草稿</button><button type="button" className={status === "pending" ? "selected" : ""} onClick={() => setStatus("pending")}>提交审核</button></div>
    <FieldError>{error}</FieldError>{message && <p role="status">{message}</p>}<button className="primary-button" disabled={busy}>{busy ? "正在保存" : status === "pending" ? "提交审核" : "保存草稿"}</button>
  </form>;
}
export default function ContributePage() {
  const { user, loading: sessionLoading } = useSession();
  const { data, loading, error, reload } = useApiResource<ContentItem[]>(user ? "/me/contributions" : null);
  const [selected, setSelected] = useState<string | null>(null);
  const toast = useToast();
  const item = data?.find((entry) => entry.id === selected);
  return <AppShell title="文章与作品投稿" eyebrow="CONTRIBUTE" >
    {user && <div className="page-intro"><p>分享文章与独立作品，审核通过后公开。</p><Link className="outline-button" href={`/people/${user.id}`}>返回个人主页</Link></div>}
    {sessionLoading ? <PageLoading /> : !user ? <div className="sign-in-required"><h2>登录后投稿</h2><Link className="primary-button" href="/login?next=/contribute">登录或注册</Link></div> : <div className="contribution-layout"><ContributionForm key={selected ?? "new"} item={item} saved={async (id) => { await reload(); setSelected(id); toast.success("投稿已保存，提交审核的内容通过后公开。"); }} /><aside className="form-panel"><h2>我的投稿</h2><button type="button" className="outline-button" onClick={() => setSelected(null)}>新建投稿</button>{loading ? <PageLoading /> : error ? <PageError message={error} retry={reload} /> : !data?.length ? <EmptyState title="还没有投稿" description="草稿、待审核及已发布内容会显示在这里。" /> : <div className="contribution-list">{data.map((entry) => <div key={entry.id}><button type="button" aria-pressed={selected === entry.id} onClick={() => setSelected(entry.id)}><strong>{entry.title}</strong><StatusPill tone={entry.status === "published" ? "live" : "warm"}>{statuses[entry.status] ?? entry.status}</StatusPill></button>{entry.status === "published" && <Link className="text-button" href={`/news/${entry.slug}`}>查看公开页面</Link>}</div>)}</div>}</aside></div>}
  </AppShell>;
}
