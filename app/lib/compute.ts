export interface ComputeProvider {
  id: string; name: string; enabled: boolean; has_token: boolean;
  base_url: string; image_uuid: string; gpu_spec_uuid: string; gpu_label: string;
  gpu_count: number; hourly_price_millis: number; cuda_v_from: number; data_centers: string[];
}
export interface ComputeCatalog {
  gpu_specs: { id: string; name: string; memory_gb: number }[];
  public_images: { id: string; name: string; cuda_v_from: number }[];
  data_centers: { id: string; name: string }[];
  source: string; verified_at: string;
}
export interface ComputePrivateImages {
  images: { id: string; name: string; ready: boolean }[];
  page: number; has_more: boolean;
}
export interface ComputeConfig {
  provider_id: string; enabled: boolean; max_gpu_seconds: number; max_cost_millis: number | null;
}
export interface ComputeGrant extends ComputeConfig {
  id: string; team_id: string; team_name: string; problem_id: string; problem_title: string;
  competition_name: string; provider_name: string; gpu_label: string; gpu_count: number;
  hourly_price_millis: number; gpu_seconds_used: number; cost_used_millis: number;
}
export interface ComputeInstance {
  id: string; grant_id: string; name: string; remote_id: string | null; provider_status: string;
}
export interface ComputeSession {
  id: string; grant_id: string; instance_id: string; state: string; duration_seconds: number;
  team_name: string; problem_title: string; gpu_count: number; rate_millis: number;
  reserved_gpu_seconds: number; reserved_cost_millis: number;
  charged_gpu_seconds: number | null; charged_cost_millis: number | null;
  created_at: string; dispatched_at: string | null; deadline: string | null; finished_at: string | null;
  stop_requested: boolean; error_code: string | null;
}
export interface ComputeOverview {
  grants: ComputeGrant[]; instances: ComputeInstance[]; sessions: ComputeSession[]; active_sessions: ComputeSession[]; worker_online: boolean;
  summary: { gpu_seconds: number; cost_millis: number };
}
export const gpuHours = (seconds: number) => (seconds / 3600).toLocaleString("zh-CN", { maximumFractionDigits: 3 });
export interface ComputeTools {
  available: { jupyter: boolean; autopanel: boolean };
  services: { port: number; protocol: string; address: string; url: string | null }[];
  monitor: {
    valid: boolean; stale: boolean; valid_at: string | null; cpu_usage_percent: number | null; mem_usage_percent: number | null;
    mem_usage: number | null; mem_limit: number | null; root_fs_used_size: number | null; root_fs_total_size: number | null;
    data_disk_used_size: number | null; data_disk_total_size: number | null;
  };
}
export const computeCost = (millis: number) => (millis / 1000).toFixed(3);
export const activeCompute = (state: string) => ["queued", "creating", "starting", "running", "stopping", "uncertain"].includes(state);
const computeStates: Record<string, string> = { queued: "等待开机", creating: "创建实例中", starting: "开机中", running: "运行中", stopping: "关机中", uncertain: "操作结果待确认", stopped: "已关机" };
export const computeState = (state: string) => computeStates[state] ?? state;
