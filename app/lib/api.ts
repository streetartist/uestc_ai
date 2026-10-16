import { clearResourceCache } from "@/app/lib/resourceCache";

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
  const mutation = !["GET", "HEAD"].includes((init.method ?? "GET").toUpperCase());
  if (mutation) clearResourceCache();
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
  if (mutation) clearResourceCache();
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
      "numeric captcha expired": "数字验证码已失效，请获取新图片。",
      "numeric captcha incorrect": "数字验证码不正确，请重新输入。",
      "captcha requested too frequently": "获取数字验证码过于频繁，请稍后重试。",
      "invalid captcha request": "数字验证码请求不正确，请重新获取图片。",
      "registration fields must be strings": "请填写注册信息并确认密码。",
      "invalid registration name": "姓名或昵称须为 1～80 个字符。",
      "invalid contribution fields": "请填写标题和正文，标题最多 240 字、摘要最多 2000 字。",
      "invalid contribution status": "投稿类型或状态不正确。",
      "contribution attachment is unavailable": "附件不可用或不属于你，请重新上传。",
      "user not found": "个人主页不存在。",
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
      "current password is incorrect": "当前密码不正确。",
      "new password must contain 8 to 128 characters": "新密码需为 8～128 个字符。",
      "password confirmation does not match": "两次输入的新密码不一致。",
      "new password must differ from current password": "新密码不能与当前密码相同。",
      "password fields must be strings": "请完整填写当前密码和新密码。",
      "valid registration invite required": "该邮箱需要有效的注册邀请码。",
      "work not found": "参赛作品不存在，或你没有查看权限。",
      "verification code expired": "邮箱验证码不存在或已过期，请重新发送邮件。",
      "invalid verification code": "邮箱验证码不正确，请检查邮件后重试。",
      "registration deadline has passed": "报名已经截止，不能再创建、加入队伍或修改报名。",
      "submission deadline has passed": "作品提交已经截止，不能再保存、提交或修改附件。",
      "submission is not formally submitted": "当前作品不是正式提交状态，不能评分。",
      "evaluation must complete before review": "自动评测尚未成功完成，请等待有效指标生成后再评分。",
      "verification code requested too frequently": "邮箱验证码发送过于频繁，请稍后再试。",
      "verification code hourly limit reached": "邮箱验证码发送次数已达上限，请一小时后再试。",
      "email delivery is not configured": "邮件服务尚未配置，请联系管理员。",
      "email provider rejected the request": "邮件服务拒绝了发送请求，请联系管理员检查发件域名。",
      "email provider is unavailable": "邮件服务暂时不可用，请稍后重试。",
      "email provider is not supported": "不支持的邮件通道，请选择 Resend 或 SMTP。",
      "invalid sender address": "发件人必须是有效的邮箱地址，可写成“名称 <地址>”。",
      "invalid smtp host": "请填写有效的 SMTP 服务器地址。",
      "invalid smtp port": "SMTP 端口必须在 1 到 65535 之间。",
      "invalid smtp security": "请选择 SSL 或 STARTTLS 加密方式。",
      "invalid smtp username": "SMTP 用户名过长或包含非法字符。",
      "email settings are invalid": "邮件配置不完整或包含非法字符。",
      "email settings cannot be decrypted": "已保存的邮件配置无法解密，请重新保存密钥。",
      "mail encryption key is not configured": "服务器未配置邮件加密密钥，无法保存密钥，请联系运维。",
      "invite expiry must be between 1 and 365 days": "邀请码有效期必须为 1 到 365 天。",
      "invite note is too long": "邀请码备注不能超过 160 个字符。",
      "used registration invite cannot be revoked": "该邀请码已经使用，不能撤销。",
      "confirmation required": "请输入确认内容。",
      "confirmation does not match": "确认内容不匹配，请核对后重试。",
      "administrator required to delete competitions with participation data": "仅管理员可以删除已有参赛数据的赛事。",
      "cannot delete your own account": "不能删除自己的账户。",
      "cannot delete the last administrator": "不能删除最后一位管理员。",
      "active compute sessions exist": "存在进行中的算力会话，请先停止。",
      "remote compute instances not stopped": "远端算力实例尚未确认停止，请先在算力管理中停止。",
      "user has reviews in locked competitions": "该用户在已锁定评分的赛事中有已提交评审，无法删除。",
      "user team in locked or scored competition": "该用户独自所在的队伍位于已锁定或已有成绩的赛事，无法删除。",
      "deletion failed": "删除失败，数据未改动，请稍后重试。",
      "track has participation records": "该赛道已有报名或提交记录，不能删除，请保留并停止发布。",
      "problem has submissions": "该赛题已有作品提交，不能删除，请改为归档。",
      "team captain required": "只有队长可以执行此操作。",
      "team captain cannot leave": "队长不能直接退出队伍，请先转让队长或解散队伍。",
      "team captain already assigned": "该成员已经是队长。",
      "team member not found": "该成员不在队伍中。",
      "cannot remove team captain": "不能移除队长，请先转让队长。",
      "invalid invite code": "邀请码无效，请检查后重试。",
      "user already joined another team": "你在本场比赛中已经加入了一支队伍，每场比赛每人只能加入一支队伍。",
      "team is full": "该队伍人数已满，不能再加入新成员。",
      "team is below the minimum size": "队伍人数未达到本场比赛的要求，请先补齐队员再报名。",
      "track limit reached": "本队在本场比赛中可报名的赛道数已达上限。",
      "team name already exists": "本赛事中已存在同名队伍，请换一个名称。",
      "team not found": "队伍不存在或已被解散。",
      "team already registered": "该队伍已经报名这个赛道。",
      "team belongs to a different competition": "该队伍不属于这场赛事。",
      "track belongs to a different competition": "该赛道不属于这场赛事。",
      "registration not found": "报名记录不存在。",
      "competition is not open": "这场赛事当前未开放报名。",
      "competition not found": "赛事不存在。",
      "competition or team not found": "赛事或队伍不存在。",
      "problem not found": "题目不存在。",
      "track not found": "赛道不存在。",
      "missing required fields": "请填写所有必填项。",
      "invalid request body": "请求内容格式不正确，请刷新页面后重试。",
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
      "evaluation test limit reached": "本队在这道题的测试次数已用尽，仍可选择已完成的结果正式提交。",
      "an evaluation trial is already active": "本队已有测试排队或运行中，请等待结束后再开始新的测试。",
      "selected evaluation result is not available": "请先选择本队在本题已完成的测评结果。",
      "select a completed evaluation trial": "请选择已完成并生成指标的自主测试记录。",
      "submitted package differs from selected evaluation trial": "当前程序包与所选测试不一致，请使用该次测试的程序包，或重新测试。",
      "evaluation rules have changed": "测评规则已调整，请按当前规则重新测试。",
      "max_team_runs must be between 1 and 1000": "每队最多测试次数须为 1—1000 的整数。",
      "CSV must use UTF-8 encoding": "CSV 文件必须使用 UTF-8 编码。",
      "asset not found": "附件不存在或已被删除。",
      "cannot remove the last administrator": "不能移除最后一名管理员。",
      "checkpoint deadline has passed": "阶段节点已截止，不能再提交。",
      "checkpoint has no package": "该阶段节点没有可用的程序包。",
      "checkpoint not configured": "该赛题尚未配置阶段节点。",
      "checkpoint not found": "阶段节点不存在。",
      "competition slug already exists": "赛事标识已存在，请更换。",
      "content not found": "内容不存在。",
      "content slug already exists": "内容标识已存在，请更换。",
      "cross-origin request rejected": "请求来源不受信任，请刷新页面后重试。",
      "duplicate submission_version_id": "存在重复的作品版本编号。",
      "episode count does not match evaluation config": "运行场次与评测配置不一致。",
      "evaluation API call limit reached": "评测接口调用次数已达上限。",
      "evaluation API expects a JSON object": "评测接口需要 JSON 对象。",
      "evaluation API is unavailable": "评测接口暂时不可用。",
      "evaluation API request is too large": "评测请求内容过大。",
      "evaluation API response is too large": "评测返回内容过大。",
      "evaluation API token invalid or expired": "评测接口令牌无效或已过期。",
      "evaluation API upstream unavailable": "评测上游服务不可用。",
      "evaluation adapter is no longer registered": "评测适配器已下线。",
      "evaluation adapter is not connected": "评测适配器未连接，请稍后重试。",
      "evaluation asset unavailable": "评测文件不可用。",
      "evaluation lease invalid or expired": "评测任务已失效，请重新发起测试。",
      "evaluation rules cannot change after submissions": "已有作品提交后不能修改评测规则。",
      "evaluation run not found": "评测记录不存在。",
      "evidence limit reached": "证据材料数量已达上限。",
      "evidence not found": "证据材料不存在。",
      "external scoring is not enabled for this problem": "该赛题未启用外部评分。",
      "invalid checkpoint fields": "阶段节点内容不完整。",
      "invalid evaluation result status": "评测结果状态不正确。",
      "invalid evidence name": "证据材料名称不正确。",
      "invalid evidence scene": "证据材料场景不正确。",
      "invalid feedback fields": "反馈内容不正确。",
      "invalid score batch status": "成绩批次状态不正确。",
      "invalid submission status": "作品状态不正确。",
      "invalid submission_version_id": "作品版本编号不正确。",
      "invalid user role": "账户角色不正确。",
      "invalid verification purpose": "验证码用途不正确。",
      "invalid worker runtime capabilities": "运行环境能力配置不正确。",
      "locked must be a boolean": "锁定状态必须为布尔值。",
      "markdown asset not found": "附件不存在。",
      "no valid score records": "没有可用的成绩记录。",
      "payload exceeds 26 MB": "上传内容超过 26 MB。",
      "problem code already exists": "题目编号已存在。",
      "problem is not open": "该题目尚未开放。",
      "problem is not open for submissions": "该题目尚未开放提交。",
      "problem or team not found": "题目或队伍不存在。",
      "problem slug already exists": "题目标识已存在。",
      "record must be an object": "成绩记录格式不正确。",
      "records must be a non-empty list": "成绩记录不能为空。",
      "registration invite not found": "注册邀请码不存在。",
      "resource not found": "资源不存在。",
      "retained_asset_ids contains duplicates": "保留的附件存在重复。",
      "retained_asset_ids must be a list": "保留的附件格式不正确。",
      "reviewer list contains duplicates": "评委名单存在重复。",
      "reviewers must contain valid user_id values": "评委名单包含无效用户。",
      "score batch not found": "成绩批次不存在。",
      "score values must be numeric": "成绩必须为数字。",
      "staged submission asset not found": "待提交附件不存在或已失效。",
      "staged_asset_ids contains duplicates": "待提交附件存在重复。",
      "staged_asset_ids must be a list": "待提交附件格式不正确。",
      "submission has changed": "作品已更新，请刷新页面后重试。",
      "submission not found": "作品不存在。",
      "submission version not found": "作品版本不存在。",
      "team_id is required": "请选择队伍。",
      "this problem uses external-only scoring": "该赛题仅使用外部评分。",
      "track slug already exists": "赛道标识已存在，请更换。",
      "update the submission to change evaluation attachments": "请先更新作品，再更换评测附件。",
      "work has no public version": "该作品还没有公开版本。",
      "work not published": "作品尚未发布。",
      "worker authentication required": "评测服务认证失败。",
      "worker capabilities are required": "评测服务缺少运行能力配置。",
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
