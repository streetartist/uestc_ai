"use client";

import { FormEvent, useState } from "react";
import Link from "next/link";
import { ArrowRight, ExternalLink, FilePenLine, Plus, Save, Send, Trash2 } from "lucide-react";
import { AppShell } from "@/app/components/AppShell";
import { ConfirmDialog } from "@/app/components/ConfirmDialog";
import { MarkdownEditor } from "@/app/components/MarkdownEditor";
import { useSession } from "@/app/components/SessionProvider";
import { useToast } from "@/app/components/ToastProvider";
import { EmptyState, FieldError, PageError, PageLoading, StatusPill } from "@/app/components/ui";
import { api, formatApiError, jsonBody } from "@/app/lib/api";
import type { ContentItem } from "@/app/lib/domain";
import { validateForm } from "@/app/lib/formValidation";
import { formatBeijing } from "@/app/lib/time";
import { useApiResource } from "@/app/lib/useApiResource";

const kindLabels: Record<string, string> = {
  announcement: "赛事公告",
  blog: "博客文章",
  research: "题目调研",
  event: "活动信息",
};

function publishedDate(value: string | null) {
  if (!value) return "尚未发布";
  return formatBeijing(value, { month: "2-digit", day: "2-digit" });
}

function ContentForm({ item, onSaved, onDeleted }: { item?: ContentItem; onSaved: () => Promise<void>; onDeleted: () => Promise<void> }) {
  const [kind, setKind] = useState(item?.kind ?? "announcement");
  const [title, setTitle] = useState(item?.title ?? "");
  const [slug, setSlug] = useState(item?.slug ?? "");
  const [excerpt, setExcerpt] = useState(item?.excerpt ?? "");
  const [body, setBody] = useState(item?.body_md ?? "# 标题\n\n在这里开始写作。");
  const [status, setStatus] = useState<"draft" | "published">(item?.status === "published" ? "published" : "draft");
  const [formError, setFormError] = useState("");
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState("");
  const toast = useToast();

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setFormError("");
    const validationError = validateForm(event.currentTarget);
    if (validationError) return;
    try {
      const bodyData = { kind, title, slug, excerpt, body_md: body, status };
      await api(item ? `/content/${item.id}` : "/content", { method: item ? "PATCH" : "POST", ...jsonBody(bodyData) });
      await onSaved();
      toast.success(item ? "内容修改已保存" : status === "published" ? "内容已创建并发布" : "内容草稿已创建");
    } catch (requestError) { setFormError(formatApiError(requestError)); }
  }

  async function remove() {
    if (!item) return;
    setDeleting(true); setDeleteError("");
    try {
      await api(`/content/${item.id}`, { method: "DELETE" });
      setDeleteOpen(false); await onDeleted();
      toast.success("内容已删除");
    } catch (requestError) { setDeleteError(formatApiError(requestError)); }
    finally { setDeleting(false); }
  }

  return <><form className="editor-form" onSubmit={submit} noValidate><div className="editor-form-head"><FilePenLine size={19} /><div><h2>{item ? "编辑内容" : "新建内容"}</h2><p>{item ? `正在编辑 ${item.slug}` : "公告、博客和题目调研共用同一套 Markdown 发布流程。"}</p></div>{item && <button type="button" className="danger-button subtle compact-button" onClick={() => setDeleteOpen(true)}><Trash2 size={14} />删除</button>}</div><div className="form-grid"><label className="form-field"><span>类型</span><select value={kind} onChange={(event) => setKind(event.target.value)}><option value="announcement">赛事公告</option><option value="blog">博客文章</option><option value="research">题目调研</option><option value="event">活动信息</option></select></label><label className="form-field"><span>URL 标识</span><input value={slug} onChange={(event) => setSlug(event.target.value.toLowerCase().replace(/[^a-z0-9-]/g, "-"))} placeholder="paper-city-update" required /></label></div><label className="form-field"><span>标题</span><input value={title} onChange={(event) => setTitle(event.target.value)} required /></label><label className="form-field"><span>摘要</span><textarea value={excerpt} onChange={(event) => setExcerpt(event.target.value)} rows={3} /></label><MarkdownEditor label="正文" name="content-body" value={body} onChange={setBody} required height={540} /><div className="submit-mode"><span>发布状态</span><div className="segmented-control"><button type="button" className={status === "draft" ? "selected" : ""} onClick={() => setStatus("draft")}><Save size={14} />草稿</button><button type="button" className={status === "published" ? "selected" : ""} onClick={() => setStatus("published")}><Send size={14} />发布</button></div></div><FieldError>{formError}</FieldError><button className="primary-button form-submit">{item ? "保存修改" : "创建内容"} <ArrowRight size={16} /></button></form><ConfirmDialog open={deleteOpen} title={`删除“${item?.title ?? "这项内容"}”？`} description="删除后公开页面会立即下线，正文和发布记录无法恢复。" busy={deleting} error={deleteError} onCancel={() => { setDeleteOpen(false); setDeleteError(""); }} onConfirm={() => void remove()} /></>;
}

export default function EditorPage() {
  const { user, loading: sessionLoading } = useSession();
  const allowed = user && ["editor", "organizer", "admin"].includes(user.role);
  const { data, loading, error, reload } = useApiResource<ContentItem[]>(allowed ? "/content?scope=all" : null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const selected = data?.find((item) => item.id === selectedId);

  if (sessionLoading) return <AppShell title="内容发布" eyebrow="WORKSPACE"><PageLoading /></AppShell>;
  if (!allowed) return <AppShell title="内容发布" eyebrow="WORKSPACE"><PageError message="当前账户没有内容发布权限。" /></AppShell>;
  return <AppShell title="内容发布" eyebrow="EDITORIAL DESK" actions={<button className="primary-button compact-button" onClick={() => setSelectedId(null)}><Plus size={15} />新建内容</button>}>
    <div className="editor-layout"><ContentForm key={selected?.id ?? "new"} item={selected} onSaved={reload} onDeleted={async () => { setSelectedId(null); await reload(); }} /><aside className="editor-archive"><header><span className="eyebrow"><i />CONTENT INDEX</span><div className="editor-archive-heading"><strong>内容列表</strong><span>{data?.length ?? 0} 项</span></div></header>{loading ? <PageLoading /> : error ? <PageError message={error} /> : !data?.length ? <EmptyState title="还没有内容" description="新内容会显示在这里。" /> : <div className="editor-archive-list">{data.map((content) => <div className={`editor-archive-row ${selectedId === content.id ? "selected" : ""}`} key={content.id}><button type="button" onClick={() => setSelectedId(content.id)}><div className="editor-archive-copy"><strong>{content.title}</strong><span><i />{kindLabels[content.kind] ?? content.kind}<time>{publishedDate(content.published_at)}</time></span></div><StatusPill tone={content.status === "published" ? "live" : "muted"}>{content.status === "published" ? "已发布" : "草稿"}</StatusPill></button>{content.status === "published" && <Link href={`/news/${content.slug}`} aria-label={`打开《${content.title}》公开页面`} title="打开公开页面"><ExternalLink size={14} /></Link>}</div>)}</div>}</aside></div>
  </AppShell>;
}
