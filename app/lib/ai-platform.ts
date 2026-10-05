export const points = (value: number) => (value / 1e6).toLocaleString("zh-CN", { maximumFractionDigits: 6 });
export type AIGrant = {
  id: string; team_id: string; team_name: string; competition_id: string; competition_name: string;
  problem_id: string | null; problem_title: string | null;
  enabled: boolean; allowed_models: string[]; allowed_channels: string[]; max_calls: number;
  max_tokens: number; max_cost_micros: number | null; max_output_tokens: number;
  requests_per_minute: number; max_concurrent: number; calls_used: number; tokens_used: number; cost_used_micros: number;
};
export type AIChannel = {
  id: string; name: string; base_url: string; protocol: "openai" | "anthropic" | "custom";
  models: Record<string, string>; disabled_models: string[]; enabled: boolean;
  weight: number; priority: number; input_price: number; output_price: number; key_count: number;
  last_test: { ok: boolean; at: string; models: string[] } | null;
};
export type AIKey = { id: string; grant_id: string; name: string; prefix: string; enabled: boolean; created_at: string };
export type AIModel = { id: string; protocols: string[]; input_price: number; output_price: number };
export type AIOverview = {
  grants: AIGrant[]; key_grant_ids: string[]; models: AIModel[];
  summary: { requests: number; completed: number; input_tokens: number; output_tokens: number;
    charged_tokens: number; cost_micros: number; average_latency_ms: number; uncertain: number; pending: number };
  daily: { date: string; requests: number; tokens: number }[];
};
export type AIUsage = {
  id: string; grant_id: string; team_name: string; model: string; status: string; created_at: string;
  problem_id: string | null; problem_title: string | null;
  channel_id: string | null; key_id: string | null; evaluation_run_id: string | null;
  input_tokens: number | null; output_tokens: number | null; cached_tokens: number | null;
  charged_tokens: number; charged_cost_micros: number; latency_ms: number | null; first_token_ms: number | null;
  error_code: string | null; attempts: { channel_id: string; status: number }[];
};
export type AIUsagePage = { items: AIUsage[]; total: number; page: number; limit: number };
export type AITeam = { id: string; name: string; competition_id: string; competition_name: string };
export type AIQuotaConfig = Pick<AIGrant, "enabled" | "allowed_models" | "allowed_channels" | "max_calls" | "max_tokens" | "max_cost_micros" | "max_output_tokens" | "requests_per_minute" | "max_concurrent">;
export type AIProblemQuota = { problem_id: string; config: AIQuotaConfig | null; team_count: number };
export type AIProblem = { id: string; title: string; code: string; competition_id: string; competition_name: string };
export const grantLabel = (grant: AIGrant) => `${grant.team_name} · ${grant.problem_title ?? "队伍通用额度"}`;
