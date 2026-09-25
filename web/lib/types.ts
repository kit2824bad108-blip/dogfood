export type Role = "admin" | "judge" | "participant";

export type User = {
  id: number;
  email: string;
  name: string;
  role: Role;
  github_login: string | null;
};

export type Me = { authenticated: boolean; user: User | null };

export type DemoAccount = {
  role: Role;
  email: string;
  name: string;
  seeded: boolean;
};

export type AuthStatus = {
  github_oauth_enabled: boolean;
  event_name: string;
  event_start: string;
  event_end: string;
  mock_github: boolean;
  local_dev_login: boolean;
  demo_accounts: DemoAccount[];
};

export type RubricCriterion = {
  key: string;
  label: string;
  /** Relative weight. Optional because the shares are what actually matter. */
  weight?: number;
  /** The share of the technical score, already normalised to sum to 100. */
  percent: number;
};

export type Rubric = {
  id: number | null;
  name: string | null;
  criteria: RubricCriterion[];
};

export type Track = {
  id: number;
  name: string;
  slug: string;
  description: string | null;
  prize_pool: string | null;
  display_order: number;
  prizes: Array<{ id: number; rank: number; title: string; description: string | null }>;
};

export type EventWindow = {
  now: string;
  opens_at: string;
  closes_at: string;
  closed: boolean;
  not_yet_open: boolean;
};

export type PublicEvent = {
  event: {
    name: string;
    starts_at: string;
    ends_at: string;
    phase: "upcoming" | "open" | "closed";
    submission_window: EventWindow;
  };
  environment: {
    github_oauth_enabled: boolean;
    mock_github: boolean;
    local_dev_login: boolean;
    commit_integrity_source: string;
  };
  rubric: Rubric;
  tracks: Track[];
  overall_prizes: Array<{ id: number; rank: number; title: string; description: string | null }>;
  stats: {
    teams: number;
    submissions: number;
    drafts: number;
    judges: number;
    verdicts: number;
    assignments: number;
  };
};

export type GalleryProject = {
  id: number;
  title: string;
  team: string;
  summary: string | null;
  repo_url: string;
  docs_url: string | null;
  track: { slug: string; name: string } | null;
  submitted_at: string | null;
};

export type Gallery = {
  query: string;
  track: string | null;
  count: number;
  tracks: Array<{ slug: string; name: string }>;
  projects: GalleryProject[];
};

export type JudgeProgressRow = {
  judge_id: number;
  name: string;
  email: string;
  assigned: number;
  technical_done: number;
  technical_pending: number;
  presentation_done: number;
  percent: number;
  last_activity: string | null;
};

export type JudgingProgress = {
  submissions: number;
  judges: JudgeProgressRow[];
  totals: {
    expected_technical_verdicts: number;
    technical_verdicts: number;
    outstanding: number;
    percent: number;
  };
};

export type TeamMember = { id: number; name: string; email: string };

export type TeamSummary = {
  id: number;
  name: string;
  invite_code: string;
  members: TeamMember[];
  submission: {
    id: number;
    title: string;
    repo_url: string;
    integrity_flagged: boolean;
    integrity_pct_in_window: number | null;
  } | null;
};

export type CommitIntegrity = {
  flagged: boolean;
  pct_in_window: number | null;
  source: string | null;
  reason: string | null;
  details: Record<string, unknown>;
  checked_at: string | null;
};

export type SubmissionStatus = "draft" | "submitted";

export type Submission = {
  id: number;
  team_id: number;
  team_name: string | null;
  title: string;
  repo_url: string;
  docs_url: string | null;
  demo_url: string | null;
  video_url: string | null;
  summary: string | null;
  track_id: number | null;
  status: SubmissionStatus;
  submitted_at: string | null;
  commit_integrity: CommitIntegrity;
  created_at: string | null;
  updated_at: string | null;
};

export type ScoreRecord = {
  technical_score: number | null;
  technical_comment: string | null;
  presentation_score: number | null;
  presentation_comment: string | null;
  technical_submitted_at: string | null;
  presentation_submitted_at: string | null;
  rubric_id: number | null;
  criteria: Record<string, number>;
};

export type JudgeSubmission = {
  id: number;
  title: string;
  team_name: string | null;
  repo_url: string;
  docs_url: string | null;
  summary: string | null;
  presentation_unlocked: boolean;
  score: ScoreRecord | null;
  commit_integrity: { flagged: boolean; pct_in_window: number | null };
};

export type Assignment = {
  submission_id: number;
  title: string;
  team_name: string;
  repo_url: string;
  technical_submitted: boolean;
  presentation_submitted: boolean;
  technical_score: number | null;
  presentation_score: number | null;
  commit_integrity_flagged: boolean;
};

export type LeaderboardRow = {
  submission_id: number;
  title: string;
  team: string;
  repo_url: string;
  axion_score: number;
  z_score: number;
  axion_rank: number;
  raw_average: number;
  raw_rank: number;
  rank_movement: number;
  judges: number;
};

export type JudgeStat = {
  id: number;
  name: string;
  verdicts: number;
  raw_mean: number | null;
  raw_sigma: number | null;
  discriminative: boolean | null;
};

export type Leaderboard = {
  leaderboard: LeaderboardRow[];
  judges: JudgeStat[];
  methodology: { prior_strength: number; sigma_floor: number; display_mapping: string };
  coverage_warnings: Record<string, number>;
  verdict_count: number;
};

export type Overview = {
  event: { name: string; starts_at: string; ends_at: string };
  totals: Record<string, number>;
};

export type AuditEntry = {
  id: number;
  action: string;
  actor: string | null;
  entity: string | null;
  entity_id: string | null;
  ip: string | null;
  details: Record<string, unknown> | null;
  created_at: string | null;
};

export type ArchiveBundle = {
  bundle: Record<string, unknown> & {
    generated_at: string;
    event: { name: string };
    results: Array<Record<string, unknown>>;
  };
  markdown: string;
};
