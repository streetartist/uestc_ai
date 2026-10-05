export type User = {
  id: string;
  email: string;
  name: string;
  role: "member" | "reviewer" | "editor" | "organizer" | "admin";
  email_verified: boolean;
};

export type RegistrationInvite = {
  id: string;
  code_prefix: string;
  note: string;
  status: "active" | "used" | "expired" | "revoked";
  created_by: string;
  created_by_name: string | null;
  expires_at: string;
  used_at: string | null;
  used_by: string | null;
  used_by_email: string | null;
  created_at: string;
};

export type Problem = {
  id: string;
  track_id: string;
  code: string;
  slug: string;
  title: string;
  summary: string;
  status: string;
  statement_md?: string;
  difficulty: number;
  compute_note: string;
  source_url?: string | null;
  submission_schema: SubmissionSchema;
  judging_schema: { rubric?: Record<string, number> };
  scoring_config: { external_weight_percent?: number; review_score_mode?: "sum" | "weighted"; external_score_label?: string; online_score_label?: string };
  evaluation_config: EvaluationConfig;
};

export type SubmissionFieldDefinition = {
  label?: string;
  type?: "text" | "url" | "textarea";
  required?: boolean;
  help?: string;
  placeholder?: string;
};

export type SubmissionSchema = {
  fields?: string[];
  field_definitions?: Record<string, SubmissionFieldDefinition>;
  readme_required?: boolean;
  readme_template?: string;
  attachments?: Array<{ key: string; label: string; extensions: string[]; min_count: number; max_count: number; required_files?: string[] }>;
};

export type ProblemTemplate = {
  id: string;
  name: string;
  description: string;
  problem: Omit<Problem, "id" | "track_id" | "status"> & { status: "draft" };
};

export type EvaluationConfig = {
  adapter: string;
  task: string;
  resources: { cpus: number; memory_mb: number; gpu: boolean; time_seconds: number; episodes: number };
  api: { enabled: boolean; max_calls: number };
  metrics: string[];
  max_team_runs?: number;
} | { adapter?: undefined; task?: undefined; resources?: undefined; api?: undefined; metrics?: undefined; max_team_runs?: undefined };

export type EvaluationBudget = {
  problem_id: string;
  team_id: string;
  enabled: boolean;
  time_seconds: number | null;
  max_team_runs: number | null;
  used_runs: number;
  remaining_runs: number | null;
};

export type EvaluationAdapter = {
  id: string;
  name: string;
  available: boolean;
  submission: { extension: string; label: string };
  tasks: string[];
  metrics: { key: string; label: string; unit: string; direction: "min" | "max"; min: number; max: number }[];
};

export type EvaluationRun = {
  id: string;
  adapter: string;
  status: "queued" | "running" | "completed" | "failed" | "superseded";
  attempts: number;
  api_calls_used: number;
  time_seconds: number;
  metrics: Record<string, number>;
  metric_definitions: EvaluationAdapter["metrics"];
  episodes: Array<Record<string, number> | {
    scenario: { id: string; label: string; difficulty: "beginner" | "intermediate" | "challenge" };
    metrics: Record<string, number>;
  }>;
  error: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
};

export type Track = {
  id: string;
  competition_id: string;
  slug: string;
  name: string;
  description: string;
  position: number;
  config: { accent?: string; short?: string };
  problems?: Problem[];
  competition?: Competition;
};

export type Competition = {
  id: string;
  slug: string;
  name: string;
  summary: string;
  status: string;
  registration_opens_at?: string | null;
  registration_closes_at?: string | null;
  starts_at?: string | null;
  ends_at?: string | null;
  config: {
    overview_md?: string;
    team_size?: { min: number; max: number };
    submission_limit?: number;
    leaderboard?: { visible: boolean; rank_scope?: "track" };
  };
  tracks?: Track[];
};

export type Team = {
  id: string;
  competition_id: string;
  name: string;
  invite_code: string;
  captain_id: string;
  members?: User[];
};

export type Registration = {
  id: string;
  competition_id: string;
  competition_name: string;
  track_id: string | null;
  track_name: string;
  track_slug: string | null;
  team_id: string;
  team_name: string;
  status: string;
  fields: Record<string, unknown>;
};

export type SubmissionVersion = {
  id: string;
  submission_id: string;
  version: number;
  readme_md: string;
  fields: Record<string, string>;
  snapshot: Record<string, unknown>;
  status: string;
  created_by: string;
  created_at: string;
  assets?: SubmissionAsset[];
};

export type SubmissionAsset = {
  id: string;
  original_name: string;
  content_type: string;
  size: number;
  visibility: string;
};

export type Submission = {
  id: string;
  problem_id: string;
  problem_slug: string;
  problem_code: string;
  team_id: string;
  team_name: string;
  title: string;
  status: string;
  current_version: number;
  latest_version_status?: string | null;
  latest_submitted_version?: number | null;
  version?: SubmissionVersion;
  versions?: SubmissionVersion[];
  assets?: SubmissionAsset[];
};

export type PublicWork = Submission & {
  track_name: string;
  track_slug: string;
  competition_name: string;
  competition_slug: string;
  team_members: Array<{ id: string; name: string }>;
  problem: Pick<Problem, "id" | "code" | "slug" | "title">;
  track: Pick<Track, "id" | "slug" | "name">;
  competition: Pick<Competition, "id" | "slug" | "name">;
  version: SubmissionVersion;
  evaluation?: EvaluationRun | null;
  evaluation_config?: EvaluationConfig;
};

export type StagedSubmissionAsset = {
  id: string;
  original_name: string;
  content_type: string;
  size: number;
  created_at: string;
};

export type ContentItem = {
  id: string;
  kind: string;
  slug: string;
  title: string;
  excerpt: string;
  body_md?: string;
  status: string;
  author: string | null;
  author_id?: string | null;
  review_note?: string;
  published_at: string | null;
};

export type LeaderboardRow = {
  rank: number | null;
  total_score: number | null;
  metrics: Record<string, number>;
  source: string;
  batch: string;
  submission_id: string;
  version: number;
  team: string;
  team_members?: { id: string; name: string }[];
  work: string;
  problem: string;
  track: string;
};

export type ReviewQueueItem = SubmissionVersion & {
  submission: Submission;
  team: Team;
  problem: Problem;
  track: Track;
  competition: Competition;
  reviewer_weight_percent?: number | null;
  review_locked: boolean;
  evaluation?: EvaluationRun | null;
  my_review: null | {
    id: string;
    scores: Record<string, number>;
    total_score: number | null;
    feedback_md: string;
    status: string;
  };
};

export type ReviewerWeightCandidate = User & {
  selected: boolean;
  weight_percent: number | null;
  effective_weight_percent: number | null;
};

export type ReviewWeightSummary = {
  submission_version_id: string;
  submission_id: string;
  title: string;
  team_name: string;
  problem_code: string;
  version: number;
  reviewed_count: number;
  reviewer_count: number;
  completed_weight_percent: number;
  external_weight_percent: number;
  external_score: number | null;
  provisional_score: number | null;
  final_score: number | null;
};

export type ReviewerWeightConfiguration = {
  competition_id: string;
  configured: boolean;
  locked: boolean;
  locked_at: string | null;
  locked_by: string | null;
  reviewers: ReviewerWeightCandidate[];
  review_summaries: ReviewWeightSummary[];
};

export type ManagedSubmission = Submission & {
  track_id: string;
  track_name: string;
  competition_id: string;
  competition_name: string;
  version_id?: string;
  version_number?: number;
  version_status?: string;
  version_title?: string;
  version_created_at?: string;
  latest_version_id: string | null;
  updated_at: string;
};

export type ScoreBatch = {
  id: string;
  competition_id: string;
  problem_id: string | null;
  problem_code?: string | null;
  problem_title?: string | null;
  track_name?: string | null;
  source: string;
  label: string;
  status: "draft" | "confirmed" | "withdrawn";
  score_count: number;
  created_at: string;
};

export type AuditEntry = {
  id: string;
  action: string;
  entity_type: string;
  entity_id: string;
  actor_id: string | null;
  actor_name: string | null;
  details: Record<string, unknown>;
  created_at: string;
};

export type ProblemDetail = Problem & { track: Track; competition: Competition };
