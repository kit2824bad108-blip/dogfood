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
    /**
     * The community clock, which is deliberately not the submission deadline: an
     * event can stop accepting work and still be taking votes. It lives inside
     * `event` because it is a property of the event, not of the deployment.
     */
    voting_window: VotingWindow;
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

/**
 * One public project. `community` is absent until the voting window closes, and the
 * presentation links are absent always — they are the tier the blind gate protects.
 */
export type ProjectDetail = {
  project: GalleryProject & { source_ref: string | null };
  comments: { count: number };
  voting_window: VotingWindow;
  community?: { votes: number; average: number | null };
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
  /** Verdicts actually filed for this project. */
  reviews_filed?: number;
  /** Judges assigned to it — the denominator coverage is measured against. */
  reviews_expected?: number;
  /** True while coverage is below the minimum: the score is not final. */
  provisional?: boolean;
};

export type JudgeStat = {
  id: number;
  name: string;
  verdicts: number;
  raw_mean: number | null;
  raw_sigma: number | null;
  discriminative: boolean | null;
};

export type UnrankedProject = {
  submission_id: number;
  title: string | null;
  team: string | null;
  reviews_expected: number;
  reason: string;
};

export type Leaderboard = {
  leaderboard: LeaderboardRow[];
  judges: JudgeStat[];
  methodology: { prior_strength: number; sigma_floor: number; display_mapping: string };
  coverage_warnings: Record<string, number>;
  verdict_count: number;
  unranked?: UnrankedProject[];
  coverage_summary?: {
    minimum_judges: number;
    ranked: number;
    provisional: number;
    provisional_ids: number[];
    unranked: number;
    coverage_percent: number;
  };
};

export type JudgeCalibrationRow = {
  judge_id: number;
  name: string;
  email: string;
  verdicts: number;
  raw_mean: number | null;
  raw_sigma: number | null;
  effective_mean: number | null;
  effective_sigma: number | null;
  discriminative: boolean | null;
  reliability: string;
};

export type JudgeCalibration = {
  judges: JudgeCalibrationRow[];
  totals: {
    judges: number;
    verdicts: number;
    non_discriminative: number;
    single_verdict: number;
    no_verdicts: number;
  };
};

/** One detected duplicate, with whatever decision an organiser has recorded. */
export type DuplicateCluster = {
  submission_id: number;
  title: string;
  team: string;
  source_ref: string | null;
  duplicate_of_submission_id: number;
  duplicate_of_title: string;
  duplicate_of_team: string;
  duplicate_of_source_ref: string | null;
  reason: string;
  decision: "duplicate" | "distinct" | "undecided";
  decided_by: string | null;
  note: string | null;
};

/** The counters an organiser sees before trusting an import. */
export type ImportDiagnostics = {
  last_batch: {
    id: number;
    source: string;
    mode: string;
    fixture_version: number | null;
    summary: ImportSummary | null;
    actor_email: string | null;
    created_at: string | null;
  } | null;
  fixture: { path: string; present: boolean; mode: string };
  live: {
    duplicates: number;
    duplicates_undecided: number;
    coverage: CoverageTotals;
    provisional_submissions: number;
  };
};

export type CoverageTotals = {
  submissions: number;
  assignments: number;
  verdicts: number;
  provisional: number;
  without_verdicts: number;
  coverage_percent: number;
};

export type ImportSummary = {
  source?: string;
  fixture_version?: number | null;
  records: {
    projects: number;
    judges: number;
    teams: number;
    participants: number;
    reviews: number;
    assignments: number;
  };
  headline: string[];
  invalid: Array<{ kind: string; ref: string; detail: string }>;
  duplicate_candidates: Array<{ submission: string; duplicate_of: string; reason: string }>;
  zero_variance_judges: string[];
  single_verdict_judges: string[];
  incomplete_batches: Array<{ project: string; reviews: number; expected: number }>;
  projects_without_reviews: string[];
  minimum_coverage: number;
  assignments_without_scores: number;
  null_technical_comments: number;
  null_summaries: number;
  event_window: { starts_at: string | null; ends_at: string | null; closed: boolean };
  mode: string;
  applied: Record<string, number> | null;
  refused?: string | null;
};

export type BalancePlan = {
  mode: string;
  created: number;
  plan: Array<{ submission_id: number; title: string; add_judges: number[] }>;
  projected: {
    new_assignments: number;
    judgments_before: number;
    judgments_after: number;
    reviews_per_project: { min: number; max: number; mean: number; variance: number };
    judge_load: { min: number; max: number; mean: number; variance: number };
    components_before: number;
    components_after: number;
    projects_without_assignments: number;
    coverage_percent: number;
  };
  dispersion: string;
  coverage: CoverageTotals;
  max_projects_per_judge_note: string;
};

export type Overview = {
  event: {
    name: string;
    starts_at: string;
    ends_at: string;
    /** Where the window came from — the console says so rather than letting a
     * moved deadline look like one that was always there. */
    window_source?: EventWindowSource;
    voting_opens_at?: string;
    voting_closes_at?: string;
  };
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

// ── the community surface (T3) ───────────────────────────────────────────────

/**
 * The community window is its own clock, separate from the submission deadline.
 * `results_visible` is false until it closes: no public endpoint returns a tally
 * while a running total could still decide the rest of the vote.
 */
export type VotingWindow = {
  now: string;
  opens_at: string;
  closes_at: string;
  phase: "upcoming" | "open" | "closed";
  open: boolean;
  closed: boolean;
  results_visible: boolean;
};

export type Voter = {
  id: number;
  email: string;
  display_name: string | null;
  verified: boolean;
  blocked: boolean;
  blocked_reason: string | null;
};

/** One row of a ballot. `position` is this voter's own order, not a ranking. */
export type BallotEntry = {
  position: number;
  submission_id: number;
  title: string;
  team: string;
  summary: string | null;
  repo_url: string;
  docs_url: string | null;
  track: { slug: string; name: string } | null;
  my_score: number | null;
  my_status: string | null;
};

export type Ballot = {
  voter: Voter;
  window: VotingWindow;
  ballot: BallotEntry[];
  progress: { votable: number; cast: number; remaining: number };
  ordering: { method: string; properties: string[] };
};

export type VoteRegistration = {
  voter: Voter;
  token: string;
  verify_url: string;
  delivery: { channel: string; mailer_configured: boolean; note: string };
  window: VotingWindow;
};

export type Comment = {
  id: number;
  submission_id: number;
  author: string;
  author_email: string | null;
  author_kind: "user" | "voter" | "unknown";
  body: string;
  status: string;
  created_at: string | null;
  moderated_reason: string | null;
};

export type CommentThread = {
  submission_id: number;
  title: string;
  count: number;
  includes_hidden: boolean;
  comments: Comment[];
};

export type CommunityResult = {
  submission_id: number;
  title: string | null;
  votes: number;
  average: number | null;
  lowest: number | null;
  highest: number | null;
  distribution: Record<string, number>;
  struck_votes?: number;
};

export type CommunityResults = {
  results: CommunityResult[];
  totals: Record<string, number>;
  window: VotingWindow;
  visibility: { results_visible: boolean; shown_to: string; reason: string };
};

// ── the outbound webhook console (T4) ───────────────────────────────────────

export type WebhookEndpoint = {
  id: number;
  url: string;
  description: string | null;
  events: string[] | "all";
  active: boolean;
  created_by: string | null;
  failure_count: number;
  last_delivered_at: string | null;
  last_failed_at: string | null;
  created_at: string | null;
  secret: string | null;
  signature_header: string;
  signature_scheme: string;
};

export type WebhookDelivery = {
  id: number;
  endpoint_id: number;
  event: string;
  status: "pending" | "delivered" | "failed" | "dead";
  attempts: number;
  response_status: number | null;
  error: string | null;
  created_at: string | null;
  next_attempt_at: string | null;
  delivered_at: string | null;
  payload?: Record<string, unknown>;
  signature?: string;
  response_body?: string | null;
};

export type WebhookConsole = {
  endpoints: WebhookEndpoint[];
  catalogue: Array<{ event: string; description: string }>;
  queue: { pending: number; delivered: number; failed: number; dead: number };
  signature: {
    header: string;
    timestamp_header: string;
    delivery_header: string;
    scheme: string;
    tolerance_seconds: number;
  };
  delivery: { attempts: number; backoff_seconds: number[]; timeout_seconds: number; worker: string };
  dead_letters: number;
};

// ── signed participation records (T4) ───────────────────────────────────────

export type ParticipationRecord = {
  code: string;
  subject_kind: "judge" | "team" | "participant";
  subject_name: string;
  subject_ref: string | null;
  subject_email: string | null;
  role: string | null;
  event_name: string;
  issued_at: string | null;
  issued_by: string | null;
  algorithm: string;
  signature: string;
  revoked: boolean;
  revoked_at: string | null;
  revoked_reason: string | null;
  verify_url: string;
  certificate_url: string;
  payload?: Record<string, unknown>;
};

export type RecordKey = {
  algorithm: string;
  fingerprint: string;
  published: boolean;
  key: string | null;
  reason: string;
  scheme: string;
};

export type RecordConsole = {
  records: ParticipationRecord[];
  counts: { total: number; revoked: number };
  key: RecordKey;
};

// ── whole-event bundles (T4) ────────────────────────────────────────────────

export type BundleEvent = {
  name: string;
  starts_at: string;
  ends_at: string;
  voting_opens_at: string;
  voting_closes_at: string;
  /** Whether the window travelled from the deployment's configuration or from an
   * organiser's edit; an exported event has to say which. */
  source?: EventWindowSource;
  revision?: number;
  note?: string | null;
};

export type EventBundle = {
  bundle_version: number;
  generated_at: string;
  generated_by: string;
  event: BundleEvent;
  counts: Record<string, number>;
  tables: Record<string, Array<Record<string, unknown>>>;
  checksum: string;
};

// ── the event clock (organiser-controlled) ──────────────────────────────────

/** Where the effective event window came from. */
export type EventWindowSource = "deployment" | "organiser";

export type EventClock = {
  name: string;
  starts_at: string;
  ends_at: string;
  voting_opens_at: string;
  voting_closes_at: string;
  source: EventWindowSource;
  revision: number;
  note: string | null;
  updated_at: string | null;
  updated_by: string | null;
};

/** What a proposed change would do, in sentences rather than flags. */
export type EventImpact = {
  submissions_open_before: boolean;
  submissions_open_after: boolean;
  results_public_before: boolean;
  results_public_after: boolean;
  submissions_after_deadline: number;
  submissions_after_deadline_ids: number[];
  warnings: string[];
};

export type EventStatus = {
  now: string;
  submissions_open: boolean;
  submissions_closed: boolean;
  submissions_upcoming: boolean;
  voting_open: boolean;
  voting_phase: "upcoming" | "open" | "closed";
  results_visible: boolean;
  record_key_published: boolean;
};

export type EventSettings = {
  event: EventClock;
  deployment_default: EventClock;
  overridden: boolean;
  can_reset: boolean;
  status: EventStatus;
  impact: EventImpact;
  validation: {
    name_max: number;
    note_max: number;
    timezone: string;
    rules: string[];
  };
};

export type EventSettingsUpdate = {
  event: EventClock;
  before: EventClock;
  changed: string[];
  impact: EventImpact;
  warnings: string[];
  status: EventStatus;
  audited?: boolean;
  notice: string;
};

export type EventHistoryEntry = {
  id: number;
  action: string;
  actor_email: string | null;
  at: string | null;
  ip: string | null;
  note: string | null;
  revision: number | null;
  changed: string[];
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
};

export type BundleImportResult = {
  mode: "dry_run" | "apply";
  refused: boolean;
  created: Record<string, number>;
  updated: Record<string, number>;
  checksum?: string;
  event?: BundleEvent;
  note: string;
};
