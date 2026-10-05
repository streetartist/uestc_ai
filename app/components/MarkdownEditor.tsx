"use client";

import { ChangeEvent, ClipboardEvent, useId, useMemo, useRef, useState } from "react";
import MDEditor, { type ContextStore } from "@uiw/react-md-editor/nohighlight";
import * as commands from "@uiw/react-md-editor/commands-cn";
import "@uiw/react-md-editor/markdown-editor.css";
import { Check, LoaderCircle, Paperclip } from "lucide-react";
import rehypeSanitize from "rehype-sanitize";
import remarkGfm from "remark-gfm";
import { markdownComponents } from "@/app/components/Markdown";
import { api, formatApiError } from "@/app/lib/api";

const MAX_ASSET_SIZE = 8 * 1024 * 1024;
const ACCEPTED_ASSETS = ".png,.jpg,.jpeg,.gif,.webp,.pdf,.md,.txt,.csv,.json,.docx,.xlsx,.pptx,.zip";

type MarkdownAsset = {
  id: string;
  original_name: string;
  size: number;
  kind: "image" | "file";
  url: string;
};

type MarkdownEditorProps = {
  label: string;
  value: string;
  onChange: (value: string) => void;
  required?: boolean;
  height?: number;
  minHeight?: number;
  help?: string;
  name?: string;
  scope?: "contribution";
};

function insertAtSelection(value: string, insertion: string, textarea?: HTMLTextAreaElement) {
  const start = textarea?.selectionStart ?? value.length;
  const end = textarea?.selectionEnd ?? start;
  const before = value.slice(0, start);
  const after = value.slice(end);
  const leading = before && !before.endsWith("\n") ? "\n\n" : "";
  const trailing = after && !after.startsWith("\n") ? "\n\n" : "";
  const text = `${leading}${insertion}${trailing}`;
  return { value: `${before}${text}${after}`, cursor: start + text.length };
}

function markdownLabel(value: string) {
  return value.replace(/([\\[\]])/g, "\\$1");
}

export function MarkdownEditor({ label, value, onChange, required = false, height = 500, minHeight = 360, help, name, scope }: MarkdownEditorProps) {
  const generatedId = useId();
  const editorId = name ?? `markdown-${generatedId.replace(/:/g, "")}`;
  const editorRef = useRef<ContextStore | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const selectionRef = useRef<{ start: number; end: number } | null>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState("");
  const [uploadedName, setUploadedName] = useState("");

  function rememberSelection() {
    const textarea = editorRef.current?.textarea;
    selectionRef.current = textarea ? { start: textarea.selectionStart, end: textarea.selectionEnd } : null;
  }

  async function uploadAsset(file: File) {
    setUploadError("");
    setUploadedName("");
    if (file.size > MAX_ASSET_SIZE) {
      setUploadError("附件不能超过 8 MB。");
      return;
    }

    setUploading(true);
    try {
      const form = new FormData();
      form.set("file", file);
      if (scope) form.set("scope", scope);
      const asset = await api<MarkdownAsset>("/markdown-assets", { method: "POST", body: form });
      const label = markdownLabel(asset.original_name);
      const markdown = asset.kind === "image"
        ? `![${label}](${asset.url})`
        : `[${label}](${asset.url})`;
      const textarea = editorRef.current?.textarea;
      if (textarea && selectionRef.current) {
        textarea.setSelectionRange(selectionRef.current.start, selectionRef.current.end);
      }
      const inserted = insertAtSelection(value, markdown, textarea);
      onChange(inserted.value);
      setUploadedName(asset.original_name);
      requestAnimationFrame(() => {
        editorRef.current?.textarea?.focus();
        editorRef.current?.textarea?.setSelectionRange(inserted.cursor, inserted.cursor);
      });
    } catch (error) {
      setUploadError(formatApiError(error));
    } finally {
      setUploading(false);
      if (inputRef.current) inputRef.current.value = "";
    }
  }

  function pickAsset(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (file) void uploadAsset(file);
  }

  function pasteAsset(event: ClipboardEvent<HTMLTextAreaElement>) {
    const file = Array.from(event.clipboardData.files).find((item) => item.type.startsWith("image/"));
    if (!file) return;
    event.preventDefault();
    rememberSelection();
    void uploadAsset(file);
  }

  const toolbarCommands = useMemo(() => {
    return commands.getCommands().filter((command) => command.name !== "image" && command.name !== "help");
  }, []);

  return (
    <div className="form-field markdown-editor-field">
      <div className="markdown-editor-label">
        <label htmlFor={editorId}>{label}{required && <span aria-hidden="true"> *</span>}</label>
        <div>
          <span>Markdown · GFM</span>
          <button type="button" onClick={() => { rememberSelection(); inputRef.current?.click(); }} disabled={uploading}>
            {uploading ? <LoaderCircle className="spin" size={13} /> : <Paperclip size={13} />}
            {uploading ? "上传中" : "插入附件"}
          </button>
        </div>
      </div>
      <div className="markdown-editor-shell" data-color-mode="light">
        <MDEditor
          ref={editorRef}
          value={value}
          onChange={(next) => onChange(next ?? "")}
          commands={toolbarCommands}
          extraCommands={[commands.codeEdit, commands.codeLive, commands.codePreview, commands.divider, commands.fullscreen]}
          height={height}
          minHeight={minHeight}
          maxHeight={900}
          preview="live"
          visibleDragbar
          highlightEnable={false}
          data-color-mode="light"
          textareaProps={{
            id: editorId,
            name,
            required,
            "aria-label": label,
            onPaste: pasteAsset,
            spellCheck: true,
          }}
          previewOptions={{
            components: markdownComponents,
            remarkPlugins: [remarkGfm],
            rehypePlugins: [rehypeSanitize],
          }}
        />
      </div>
      <input ref={inputRef} className="visually-hidden" type="file" accept={ACCEPTED_ASSETS} onChange={pickAsset} tabIndex={-1} />
      <div className="markdown-editor-meta" aria-live="polite">
        <small>{help ?? "可粘贴图片，或用回形针插入图片与附件；单个文件不超过 8 MB。"}</small>
        {uploading && <span><LoaderCircle className="spin" size={13} />正在上传</span>}
        {!uploading && uploadedName && <span className="upload-success"><Check size={13} />已插入 {uploadedName}</span>}
        {uploadError && <span className="upload-error">{uploadError}</span>}
      </div>
    </div>
  );
}
