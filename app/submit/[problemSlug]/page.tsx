"use client";

import Link from "next/link";
import { FormEvent, useMemo, useState } from "react";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { AlertCircle, ArrowLeft, ArrowRight, Check, Download, FileText, FileUp, LoaderCircle, LogIn, RotateCcw, Save, Send, TicketCheck, X } from "lucide-react";
import { AppShell } from "@/app/components/AppShell";
import { MarkdownEditor } from "@/app/components/MarkdownEditor";
import { EvaluationTrials } from "@/app/components/EvaluationTrials";
import { CheckpointProgress } from "@/app/components/CheckpointProgress";
import { useSession } from "@/app/components/SessionProvider";
import { useToast } from "@/app/components/ToastProvider";
import { FieldError, PageError, PageLoading, StatusPill } from "@/app/components/ui";
import { api, assetUrl, formatApiError, jsonBody } from "@/app/lib/api";
import type { EvaluationBudget, EvaluationRun, ProblemDetail, Registration, StagedSubmissionAsset, Submission, Team } from "@/app/lib/domain";
import { validateForm } from "@/app/lib/formValidation";
import { formatBeijing } from "@/app/lib/time";
import { useApiResource } from "@/app/lib/useApiResource";

const fieldLabels: Record<string, string> = {
  repository: "代码仓库地址", prediction_file: "预测结果地址或说明", report: "技术报告地址", model_link: "模型地址",
  agent_package: "Agent 包地址", policy_package: "策略包地址", demo_url: "在线演示地址", demo_video: "演示视频地址", readme: "补充 README 地址",
};

const MAX_ATTACHMENT_SIZE = 20 * 1024 * 1024;
const ALLOWED_ATTACHMENT_EXTENSIONS = new Set(["md", "pdf", "csv", "json", "zip", "tar", "gz", "png", "jpg", "jpeg", "webp", "mp4"]);

type PendingAsset = {
  id: string;
  file: File;
  status: "uploading" | "done" | "error";
  stagedId?: string;
  error?: string;
};

function formatBytes(value: number) {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}

function fileExtension(name: string) {
  return name.includes(".") ? name.split(".").pop()?.toLowerCase() ?? "" : "";
}

function readmeTemplate(problem?: ProblemDetail | null) {
  if (problem?.submission_schema.readme_template) return problem.submission_schema.readme_template;
  return `# ${problem?.title ?? "作品名称"}\n\n## 项目概览\n\n用两三句话说明你做了什么。\n\n## Motivation\n\n为什么这个问题值得解决？\n\n## 技术方案\n\n- 核心方法\n- 数据或环境\n- 关键决策\n\n## 复现步骤\n\n1. 环境准备\n2. 运行方式\n3. 结果说明\n\n## AI 工具使用声明\n\n说明使用过的模型、用途与人工确认方式。`;
}

export default function SubmitPage() {
  const params = useParams<{ problemSlug: string }>();
  const router = useRouter();
  const searchParams = useSearchParams();
  const draftSubmissionId = searchParams.get("submission");
  const { user, loading: sessionLoading } = useSession();
  const toast = useToast();
  const { data: problem, loading, error, reload } = useApiResource<ProblemDetail>(params.problemSlug ? `/problems/${params.problemSlug}` : null);
  const { data: draftSubmission, loading: draftLoading, error: draftError } = useApiResource<Submission>(user && draftSubmissionId ? `/submissions/${draftSubmissionId}` : null);
  const { data: teams, reload: reloadTeams } = useApiResource<Team[]>(user ? "/me/teams" : null);
  const { data: registrations, reload: reloadRegistrations } = useApiResource<Registration[]>(user ? "/me/registrations" : null);
  const [chosenTeamId, setChosenTeamId] = useState<string | null>(null);
  const [editedTitle, setEditedTitle] = useState<string | null>(null);
  const [editedReadme, setEditedReadme] = useState<string | null>(null);
  const [editedFields, setEditedFields] = useState<Record<string, string> | null>(null);
  const [pendingAssets, setPendingAssets] = useState<PendingAsset[]>([]);
  const [retainedAssetIds, setRetainedAssetIds] = useState<string[] | null>(null);
  const [assetError, setAssetError] = useState("");
  const [submitError, setSubmitError] = useState("");
  const [submittingMode, setSubmittingMode] = useState<"draft" | "submitted" | "test" | null>(null);
  const [trialSelection, setTrialSelection] = useState<{ id: string; teamId: string } | null>(null);
  const [trialRefreshKey, setTrialRefreshKey] = useState(0);

  const eligibleTeams = useMemo(() => (teams ?? []).filter((team) => team.competition_id === problem?.competition.id), [teams, problem]);
  const draftVersion = draftSubmission?.versions?.[draftSubmission.versions.length - 1];
  const teamId = chosenTeamId ?? draftSubmission?.team_id ?? eligibleTeams[0]?.id ?? "";
  const hasEvaluation = Boolean(problem?.evaluation_config?.adapter);
  const { data: budgetData, loading: budgetLoading, error: budgetError, reload: reloadBudget } = useApiResource<EvaluationBudget>(
    user && problem?.id && teamId && hasEvaluation ? `/problems/${problem.id}/evaluation-budget?team_id=${encodeURIComponent(teamId)}` : null,
  );
  const budget = budgetData?.team_id === teamId && budgetData?.problem_id === problem?.id ? budgetData : null;
  const testLimitReached = hasEvaluation && budget?.remaining_runs === 0;
  const budgetUnavailable = hasEvaluation && (!budget || budgetLoading || Boolean(budgetError));
  const title = editedTitle ?? draftSubmission?.title ?? problem?.title ?? "";
  const readme = editedReadme ?? draftVersion?.readme_md ?? readmeTemplate(problem);
  const fields = editedFields ?? draftVersion?.fields ?? {};
  const selectedRegistration = registrations?.find((item) => item.team_id === teamId && (item.track_id === problem?.track.id || item.track_id === null));
  const waitingForDraft = Boolean(draftSubmissionId && user && !draftSubmission && !draftError);
  const assetsIncomplete = pendingAssets.some((item) => item.status !== "done");
  const retainedIds = retainedAssetIds ?? draftVersion?.assets?.map((asset) => asset.id) ?? [];
  const deadlinePassed = Boolean(problem?.competition.ends_at && new Date(problem.competition.ends_at).getTime() <= Date.now());
  const selectedTrialId = trialSelection?.teamId === teamId ? trialSelection.id : typeof draftVersion?.snapshot.evaluation_run_id === "string" ? draftVersion.snapshot.evaluation_run_id : "";

  async function startTest() {
    if (!problem || !teamId || submittingMode) return;
    setSubmitError("");
    if (testLimitReached || budgetUnavailable || assetsIncomplete) {
      setSubmitError(testLimitReached ? "本队在本题的测试次数已用尽，仍可选择已完成的结果正式提交。" : "请等待附件上传完成并确认测试额度。");
      return;
    }
    setSubmittingMode("test");
    try {
      await api<EvaluationRun>(`/problems/${problem.id}/evaluation-runs`, { method: "POST", ...jsonBody({ team_id: teamId, staged_asset_ids: pendingAssets.map((item) => item.stagedId), retained_asset_ids: retainedIds }) });
      setTrialRefreshKey((value) => value + 1);
      await reloadBudget();
      toast.success("测试已加入队列，可在下方查看状态与指标。正式稿未改变。");
    } catch (requestError) { setSubmitError(formatApiError(requestError)); void reloadBudget(); }
    finally { setSubmittingMode(null); }
  }

  async function registerTrack() {
    if (!problem || !teamId) return;
    setSubmitError("");
    try {
      await api("/registrations", { method: "POST", ...jsonBody({ competition_id: problem.competition.id, track_id: problem.track.id, team_id: teamId }) });
      await reloadRegistrations();
      toast.success(`已报名 ${problem.track.name}`);
    } catch (requestError) { setSubmitError(formatApiError(requestError)); }
  }

  function chooseAssets(fileList: FileList | null) {
    if (!fileList) return;
    setAssetError("");
    const accepted: PendingAsset[] = [];
    const rejected: string[] = [];
    const known = new Set(pendingAssets.map((item) => `${item.file.name}-${item.file.size}-${item.file.lastModified}`));
    Array.from(fileList).forEach((file, index) => {
      const extension = fileExtension(file.name);
      if (!ALLOWED_ATTACHMENT_EXTENSIONS.has(extension)) {
        rejected.push(`${file.name}：格式不支持`);
      } else if (file.size > MAX_ATTACHMENT_SIZE) {
        rejected.push(`${file.name}：超过 20 MB`);
      } else if (!known.has(`${file.name}-${file.size}-${file.lastModified}`)) {
        accepted.push({ id: `${file.name}-${file.size}-${file.lastModified}-${index}-${crypto.randomUUID()}`, file, status: "uploading" });
      }
    });
    setPendingAssets((current) => [...current, ...accepted]);
    accepted.forEach((item) => void stageAsset(item));
    if (rejected.length) setAssetError(rejected.join("；"));
  }

  function updateAsset(id: string, update: Partial<PendingAsset>) {
    setPendingAssets((current) => current.map((item) => item.id === id ? { ...item, ...update } : item));
  }

  async function stageAsset(item: PendingAsset) {
    updateAsset(item.id, { status: "uploading", stagedId: undefined, error: undefined });
    try {
      const body = new FormData();
      body.set("file", item.file);
      body.set("problem_id", problem?.id ?? "");
      const result = await api<StagedSubmissionAsset>("/submission-assets/stage", { method: "POST", body });
      updateAsset(item.id, { status: "done", stagedId: result.id, error: undefined });
    } catch (requestError) {
      const message = formatApiError(requestError);
      updateAsset(item.id, { status: "error", stagedId: undefined, error: message });
      toast.error(`${item.file.name} 上传失败：${message}`);
    }
  }

  async function removeAsset(item: PendingAsset) {
    if (item.status === "uploading") return;
    try {
      if (item.stagedId) await api(`/submission-assets/stage/${item.stagedId}`, { method: "DELETE" });
      setPendingAssets((current) => current.filter((asset) => asset.id !== item.id));
    } catch (requestError) {
      const message = formatApiError(requestError);
      setAssetError(message);
      toast.error(message);
    }
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!problem || !teamId) return;
    const submitter = (event.nativeEvent as SubmitEvent).submitter as HTMLButtonElement | null;
    const mode = submitter?.value === "submitted" ? "submitted" : "draft";
    setSubmitError("");
    if (submittingMode) return;
    if (mode === "submitted" && hasEvaluation && !selectedTrialId) {
      setSubmitError("请先自主测试，并选择一条已完成的结果用于正式提交。");
      return;
    }
    if (assetsIncomplete) {
      setAssetError(pendingAssets.some((item) => item.status === "uploading") ? "请等待附件上传完成后再保存。" : "有附件上传失败，请重试或移除后再保存。");
      return;
    }
    setSubmittingMode(mode);
    try {
      const validationError = mode === "submitted" ? validateForm(event.currentTarget) : null;
      if (validationError) return;
      const result = await api<Submission>("/submissions", { method: "POST", ...jsonBody({
        problem_id: problem.id,
        team_id: teamId,
        title,
        readme_md: readme,
        fields,
        status: mode,
        staged_asset_ids: pendingAssets.map((item) => item.stagedId),
        retained_asset_ids: retainedIds,
        evaluation_run_id: mode === "submitted" ? selectedTrialId : undefined,
      }) });
      setPendingAssets([]);
      toast.success(mode === "draft" ? "草稿已保存；作品已退出公开展示和评审" : "作品已正式提交并公开，已进入评审流程");
      router.push(mode === "submitted" ? `/works/${result.id}` : "/dashboard");
    } catch (requestError) { setSubmitError(formatApiError(requestError)); void reloadBudget(); }
    finally { setSubmittingMode(null); }
  }

  if (sessionLoading || loading || waitingForDraft || (draftSubmissionId && user && draftLoading)) return <AppShell title="提交作品" eyebrow="SUBMISSION"><PageLoading /></AppShell>;
  if (error || !problem) return <AppShell title="提交作品" eyebrow="SUBMISSION"><PageError message={error || "题目不存在。"} retry={reload} /></AppShell>;
  if (!user) { const next = `/submit/${problem.slug}${draftSubmissionId ? `?submission=${draftSubmissionId}` : ""}`; return <AppShell title="提交作品" eyebrow={problem.code}><div className="sign-in-required"><LogIn size={22} /><h2>登录后提交作品</h2><p>平台需要把版本归属到你的队伍和账户。</p><Link className="primary-button" href={`/login?next=${encodeURIComponent(next)}`}>登录或注册 <ArrowRight size={16} /></Link></div></AppShell>; }
  if (draftSubmissionId && (draftError || !draftSubmission || draftSubmission.problem_id !== problem.id)) return <AppShell title="继续编辑作品" eyebrow={problem.code}><PageError message={draftError || "这份作品不存在或不属于当前赛题。"} /></AppShell>;

  return (
    <AppShell title={draftSubmission ? "编辑作品" : "提交作品"} eyebrow={problem.code} actions={<Link className="outline-button" href={draftSubmission ? "/dashboard" : `/competitions/${problem.competition.slug}/tracks/${problem.track.slug}/problems/${problem.slug}`}><ArrowLeft size={15} />{draftSubmission ? "返回工作台" : "返回题目"}</Link>}>
      <div className="submission-layout">
        <form className="submission-form" onSubmit={submit} noValidate>
          <header><StatusPill tone={deadlinePassed ? "muted" : draftSubmission?.status === "submitted" ? "live" : draftSubmission ? "warm" : "live"}>{deadlinePassed ? "已截止" : draftSubmission?.status === "submitted" ? "当前为正式稿" : draftSubmission ? "当前为草稿" : problem.track.name}</StatusPill><h2>{draftSubmission ? draftSubmission.title : problem.title}</h2><p>{deadlinePassed ? "赛事提交已经截止，作品内容与附件不能再修改。" : draftSubmission ? "保存草稿或正式提交都会覆盖当前内容，不保留旧稿；内容变化后原评分会清除。" : "每个队伍在本题只有一份当前稿，可反复保存草稿或正式提交，截止后锁定。"}</p></header>
          {deadlinePassed && <div className="form-gate"><AlertCircle size={18} /><div><strong>作品提交已截止</strong><p>{formatBeijing(problem.competition.ends_at!, { dateStyle: "long", timeStyle: "short" })} 后不能保存、提交或修改附件。</p></div></div>}
          {!eligibleTeams.length ? <div className="form-gate"><strong>先创建参赛队伍</strong><p>当前赛事下还没有你的队伍。</p><Link className="outline-button" href="/dashboard">前往工作台 <ArrowRight size={15} /></Link></div> : <>
            <fieldset className="submission-edit-fields" disabled={deadlinePassed}>
            <label className="form-field"><span>参赛队伍</span><select value={teamId} onChange={(event) => setChosenTeamId(event.target.value)} disabled={Boolean(draftSubmission)}>{eligibleTeams.map((team) => <option key={team.id} value={team.id}>{team.name}</option>)}</select></label>
            {hasEvaluation && <section className="form-gate"><div><strong>本题指标测试额度</strong>
              {budget && <><p>每次测试最长 {budget.time_seconds} 秒（全部场景共用）；本队已用 {budget.used_runs} 次，{budget.max_team_runs === null ? "次数不限" : `最多 ${budget.max_team_runs} 次，剩余 ${budget.remaining_runs} 次`}。</p><small>在下方点击“开始测试”使用机会；正式提交选择已完成的结果，不扣测试次数。{testLimitReached && "次数已用尽，仍可选择历史结果正式提交。"}</small></>}
              {budgetLoading && <p>正在读取测试额度…</p>}{budgetError && <p role="alert">{budgetError}</p>}
            </div><button type="button" className="outline-button" disabled={budgetLoading} onClick={() => void reloadBudget()}>刷新额度</button></section>}
            {!selectedRegistration && <div className="form-gate warm"><TicketCheck size={18} /><div><strong>该队伍尚未报名本赛道</strong><p>报名后即可保存草稿或正式提交。</p></div><button className="outline-button" type="button" onClick={registerTrack}>报名 {problem.track.name}</button></div>}
            <label className="form-field"><span>作品名称</span><input value={title} onChange={(event) => setEditedTitle(event.target.value)} required /></label>
            <div className="dynamic-fields">{(problem.submission_schema.fields ?? []).map((field) => {
              const spec = problem.submission_schema.field_definitions?.[field];
              const type = spec?.type ?? (field.includes("url") || field.includes("link") || field === "repository" ? "url" : "text");
              const props = { value: fields[field] ?? "", required: spec?.required ?? false, placeholder: spec?.placeholder ?? (type === "url" ? "https://" : "填写材料说明"), onChange: (event: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => setEditedFields((current) => ({ ...(current ?? fields), [field]: event.target.value })) };
              return <label className="form-field" key={field}><span>{spec?.label || fieldLabels[field] || field}{spec?.required ? "（必填）" : ""}</span>{type === "textarea" ? <textarea rows={5} {...props} /> : <input type={type} {...props} />}{spec?.help && <small>{spec.help}</small>}</label>;
            })}</div>
            <MarkdownEditor label="作品 README" name="submission-readme" value={readme} onChange={setEditedReadme} required={problem.submission_schema.readme_required ?? false} height={560} help="支持 GitHub Flavored Markdown；可粘贴图片或插入 8 MB 以内的附件。" />
            {Boolean(problem.submission_schema.attachments?.length) && <section className="form-gate"><div><strong>正式提交所需附件</strong>{problem.submission_schema.attachments?.map((item) => <p key={item.key}>{item.label}：{item.extensions.map((ext) => `.${ext}`).join(" / ")}，{item.min_count === item.max_count ? item.min_count : `${item.min_count}—${item.max_count}`} 个</p>)}<small>可先保存不完整的草稿；正式提交会检查全部材料。</small></div></section>}
            <div className="form-field file-field"><span>{problem.evaluation_config?.adapter ? "附件（自主测试与正式提交使用相同程序包）" : "附件（可选）"}</span>{draftVersion?.assets?.length ? <div className="draft-asset-list"><small>已有附件可继续使用；替换程序包后，需要重新测试并选择对应结果</small>{draftVersion.assets.map((asset) => { const retained = retainedIds.includes(asset.id); return <div className={retained ? "" : "removed"} key={asset.id}><a href={assetUrl(asset.id)}><FileText size={14} /><span><strong>{asset.original_name}</strong><small>{formatBytes(asset.size)}</small></span><Download size={13} /></a><span className={`asset-upload-state ${retained ? "done" : "error"}`}>{retained ? "将保留" : "已移除"}</span><button type="button" disabled={Boolean(submittingMode)} title={retained ? `从本次版本移除 ${asset.original_name}` : `恢复 ${asset.original_name}`} aria-label={retained ? `从本次版本移除 ${asset.original_name}` : `恢复 ${asset.original_name}`} onClick={() => setRetainedAssetIds(retained ? retainedIds.filter((id) => id !== asset.id) : [...retainedIds, asset.id])}>{retained ? <X size={14} /> : <RotateCcw size={14} />}</button></div>; })}</div> : null}<span className={`file-input ${submittingMode ? "disabled" : ""}`}><FileUp size={17} /><input type="file" multiple disabled={Boolean(submittingMode)} accept=".md,.pdf,.csv,.json,.zip,.tar,.gz,.png,.jpg,.jpeg,.webp,.mp4" aria-label="选择作品附件" onChange={(event) => { chooseAssets(event.target.files); event.target.value = ""; }} /><span><strong>{pendingAssets.length ? `继续添加附件（已上传 ${pendingAssets.filter((item) => item.status === "done").length}/${pendingAssets.length}）` : "选择并上传附件"}</strong><small>选择后立即上传；支持图片、文档、压缩包和视频</small></span></span>{pendingAssets.length > 0 && <div className="pending-asset-list">{pendingAssets.map((item) => <div key={item.id}><FileText size={15} /><span><strong>{item.file.name}</strong><small>{formatBytes(item.file.size)}{item.error ? ` · ${item.error}` : ""}</small></span><span className={`asset-upload-state ${item.status}`}>{item.status === "uploading" ? <><LoaderCircle className="spin" size={13} />上传中</> : item.status === "done" ? <><Check size={13} />已上传</> : <><AlertCircle size={13} />失败</>}</span><span className="asset-row-actions">{item.status === "error" && <button type="button" title={`重试上传 ${item.file.name}`} aria-label={`重试上传 ${item.file.name}`} onClick={() => void stageAsset(item)}><RotateCcw size={14} /></button>}<button type="button" disabled={item.status === "uploading"} title={`移除 ${item.file.name}`} aria-label={`移除 ${item.file.name}`} onClick={() => void removeAsset(item)}><X size={14} /></button></span></div>)}</div>}{assetError && <span className="attachment-error" role="alert">{assetError}</span>}<small>单个附件不超过 20 MB；{problem.evaluation_config?.adapter ? "程序包将进入独立的评测环境运行。" : "平台只存储与展示附件。"} </small></div>
            {hasEvaluation && teamId && <EvaluationTrials key={`${problem.id}:${teamId}`} problemId={problem.id} teamId={teamId} config={problem.evaluation_config} budget={budget} busy={Boolean(submittingMode)} disabled={deadlinePassed || !selectedRegistration || assetsIncomplete || budgetUnavailable} selectedId={selectedTrialId} onSelect={(id) => setTrialSelection({ id, teamId })} onStart={() => void startTest()} onBudgetRefresh={reloadBudget} refreshKey={trialRefreshKey} />}
            <div className="submission-decision"><div><strong>选择当前稿状态</strong><span>保存草稿会退出公开展示与评审；正式提交会立即公开并进入评审。</span></div><div className="submission-actions"><button className="outline-button" type="submit" name="submission-status" value="draft" disabled={Boolean(submittingMode) || !selectedRegistration || assetsIncomplete || deadlinePassed}><Save size={15} />{submittingMode === "draft" ? "正在保存草稿" : "保存为草稿"}</button><button className="primary-button" type="submit" name="submission-status" value="submitted" disabled={Boolean(submittingMode) || !selectedRegistration || assetsIncomplete || deadlinePassed || (hasEvaluation && !selectedTrialId)}><Send size={15} />{submittingMode === "submitted" ? "正在正式提交" : "正式提交"}<ArrowRight size={15} /></button></div></div>
            <FieldError>{submitError}</FieldError>
            </fieldset>
          </>}
        </form>
        <aside className="submission-guide"><span>README 建议结构</span><ol><li>项目概览与 Motivation</li><li>技术方案与关键决策</li><li>环境、运行和复现步骤</li><li>结果、限制与失败记录</li><li>AI 工具使用声明</li></ol><hr /><p>评审和外部成绩只对应当前正式稿；覆盖内容后旧评分会清除。</p><button className="text-button" type="button" disabled={deadlinePassed} onClick={() => { setEditedReadme(readmeTemplate(problem)); void reloadTeams(); }}>恢复模板</button></aside>
      </div>
      {teamId && selectedRegistration && <CheckpointProgress key={`${problem.id}:${teamId}`} problemId={problem.id} teamId={teamId} />}
    </AppShell>
  );
}
