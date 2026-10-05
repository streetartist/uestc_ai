const configuredBase = process.env.NEXT_PUBLIC_API_URL?.replace(/\/$/, "");
// The browser must use the public same-origin proxy in production. Keep the
// absolute URL override for deployments that host the API separately.
export const API_BASE = configuredBase ?? "/api";

export class ApiError extends Error {
  status: number;
  details?: unknown;

  constructor(message: string, status: number, details?: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.details = details;
  }
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body && !(init.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    credentials: "include",
    headers,
  });
  const isJson = response.headers.get("content-type")?.includes("application/json");
  const body = isJson ? await response.json() : await response.text();
  if (!response.ok) {
    const message = typeof body === "object" && body && "error" in body
      ? String((body as { error: unknown }).error)
      : `请求失败（${response.status}）`;
    throw new ApiError(message, response.status, body);
  }
  return body as T;
}

export function jsonBody(value: unknown): Pick<RequestInit, "body"> {
  return { body: JSON.stringify(value) };
}

export function formatApiError(error: unknown): string {
  if (error instanceof TypeError && /failed to fetch|networkerror|load failed/i.test(error.message)) {
    return "暂时无法连接服务，请稍后重试。";
  }
  if (error instanceof ApiError) {
    const translations: Record<string, string> = {
      "team has compute records": "队伍已有算力实例或使用记录，请保留队伍以便核对。",
      "track has compute records": "赛道已有算力实例或使用记录，请归档以保留记录。",
      "problem has compute records": "赛题已有算力实例或使用记录，请归档以保留记录。",
      "problem has API records": "赛题已有 API 密钥或用量记录，请归档以保留记录。",
      "authentication required": "请先登录后继续。",
      "insufficient permissions": "当前账户没有执行此操作的权限。",
      "team membership required": "只有队伍成员可以执行此操作。",
      "team is not registered for this track": "请先为队伍报名该赛道。",
      "email already registered": "该邮箱已经注册。",
      "invalid email address": "请输入有效的邮箱地址。",
      "invalid email or password": "邮箱或密码不正确。",
      "valid registration invite required": "该邮箱需要有效的注册邀请码。",
      "verification code expired": "验证码不存在或已过期，请重新发送。",
      "invalid verification code": "验证码不正确，请检查后重试。",
      "registration deadline has passed": "报名已经截止，不能再创建、加入队伍或修改报名。",
      "submission deadline has passed": "作品提交已经截止，不能再保存、提交或修改附件。",
      "submission is not formally submitted": "当前作品不是正式提交状态，不能评分。",
      "evaluation must complete before review": "自动评测尚未成功完成，请等待有效指标生成后再评分。",
      "verification code requested too frequently": "验证码发送过于频繁，请稍后再试。",
      "verification code hourly limit reached": "验证码发送次数已达上限，请一小时后再试。",
      "email delivery is not configured": "邮件服务尚未配置，请联系管理员。",
      "email provider rejected the request": "邮件服务拒绝了发送请求，请联系管理员检查发件域名。",
      "email provider is unavailable": "邮件服务暂时不可用，请稍后重试。",
      "invite expiry must be between 1 and 365 days": "邀请码有效期必须为 1 到 365 天。",
      "invite note is too long": "邀请码备注不能超过 160 个字符。",
      "used registration invite cannot be revoked": "该邀请码已经使用，不能撤销。",
      "competition has participation records": "该赛事已有队伍、报名或成绩记录，请改为归档。",
      "track has participation records": "该赛道已有报名或提交记录，不能删除，请保留并停止发布。",
      "problem has submissions": "该赛题已有作品提交，不能删除，请改为归档。",
      "team captain required": "只有队长可以删除队伍。",
      "team has participation records": "该队伍已有报名或作品记录，请先处理相关记录。",
      "registration has submissions": "该报名已经产生作品提交，不能取消。",
      "only unreviewed drafts can be deleted": "只有未送审、未计分的纯草稿作品可以删除。",
      "file is required": "请选择需要上传的文件。",
      "markdown asset type is not allowed": "不支持这种附件格式，请选择图片或常见文档。",
      "markdown asset exceeds size limit": "附件不能超过 8 MB。",
      "file type is not allowed": "不支持这种附件格式。",
      "submission asset exceeds size limit": "作品附件不能超过 20 MB。",
      "invalid staged submission assets": "有附件已失效，请移除后重新上传。",
      "invalid retained submission assets": "原草稿附件已变化，请刷新页面后重试。",
      "retained submission asset is unavailable": "原草稿附件文件不可用，请移除后重新上传。",
      "at least one reviewer is required": "请至少选择一位评委。",
      "reviewer is not eligible": "所选用户已不具备评审权限，请刷新后重试。",
      "reviewer weights must be between 0 and 100": "评委权重必须大于 0 且不超过 100%。",
      "reviewer weights exceed 100 percent": "手动指定的评委权重已超过 100%。",
      "automatic reviewers need remaining weight": "请为自动分配的评委保留一部分权重。",
      "reviewer weights must total 100 percent": "全部手动设置时，评委权重合计必须为 100%。",
      "reviewer is not assigned to this competition": "你没有被分配到这场赛事的评审工作。",
      "online review is locked for this competition": "本赛事的在线评分已锁定，不能再修改评分或评委配置。",
      "reviewers must be a list": "评委配置格式不正确，请刷新后重试。",
      "external weight must be between 0 and 100 percent": "外部评测权重必须在 0% 到 100% 之间。",
      "external-only scoring cannot include reviewers": "外部评测为 100% 时无需选择在线评委。",
      "file, competition_id and problem_id are required": "请选择赛事、赛题和成绩 CSV 文件。",
      "CSV must include submission_version_id and total_score": "CSV 必须包含 submission_version_id 和 total_score 两列。",
      "problem belongs to a different competition": "所选赛题不属于当前赛事。",
      "problem_id is required": "请选择本次外部评分对应的赛题。",
      "submission version is not formally submitted": "CSV 中包含尚未正式提交的作品版本。",
      "submission version belongs to a different problem": "CSV 中包含其他赛题的作品版本。",
      "score batch must contain one problem": "一个成绩批次只能包含同一道赛题。",
      "evaluation test limit reached": "本队在这道题的指标测试次数已用尽。可继续保存草稿，请联系组织方调整次数后再正式提交。",
      "max_team_runs must be between 1 and 1000": "每队最多测试次数须为 1—1000 的整数。",
    };
    return translations[error.message] ?? error.message;
  }
  return error instanceof Error ? error.message : "请求没有完成，请稍后重试。";
}

export function assetUrl(assetId: string): string {
  return `${API_BASE}/assets/${assetId}`;
}

export function resolveMarkdownUrl(value?: string): string | undefined {
  if (!value) return value;
  if (value.startsWith("/api/")) {
    return `${API_BASE.replace(/\/api$/, "")}${value}`;
  }
  return value;
}
