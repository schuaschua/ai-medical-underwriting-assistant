/* Generated from contracts.schema.json by scripts/generate-contracts.mjs.
 * Do not edit: change packages/contracts, then run `npm run contracts:generate`. */

export type ActorKind = "human" | "ai";
export type JsonValue = unknown;
/**
 * Every `error.code` a service may return.
 */
export type ErrorCode =
  | "validation_failed"
  | "invalid_role"
  | "role_not_allowed"
  | "actor_not_human"
  | "not_found"
  | "method_not_allowed"
  | "file_too_large"
  | "unsupported_file_type"
  | "payload_too_large"
  | "unsupported_media_type"
  | "too_many_requests"
  | "in_progress"
  | "not_redacted"
  | "not_awaiting_decision"
  | "pages_not_terminal"
  | "rule_not_seen"
  | "step_limit"
  | "retriever_not_available"
  | "stage_timeout"
  | "stage_failed"
  | "redaction_failed"
  | "invalid_model_output"
  | "model_unavailable"
  | "upstream_unavailable"
  | "internal_error";
/**
 * How one tool call of the verdict agent ended (AD-15).
 */
export type StepOutcome = "done" | "refused" | "failed";
/**
 * The verdict agent's three tools (AD-15).
 */
export type ToolName = "list_facts" | "search_rules" | "read_rule";
export type AuditAction =
  | "case.started"
  | "document.redacted"
  | "page.classified"
  | "page.routed"
  | "page.kept"
  | "page.discarded"
  | "page.accepted"
  | "page.denied"
  | "facts.extracted"
  | "verdict.suggested"
  | "stage.failed"
  | "case.completed";
/**
 * The retrieval ladder rows (AD-11).
 */
export type RetrieverConfig = "r1" | "r2" | "r3" | "r4" | "r5" | "r6";
export type CaseStatus = "running" | "awaiting_human" | "completed" | "failed";
export type PageStatus =
  | "uploaded"
  | "classified"
  | "awaiting_customer"
  | "awaiting_triage"
  | "extracting"
  | "extracted"
  | "discarded"
  | "denied"
  | "failed";
/**
 * Status of one stage result (AD-6).
 */
export type StageStatus = "running" | "done" | "failed";
export type ClassifierContender = "llm" | "doc-intelligence";
/**
 * Where a case may be told to stop early (AD-17: the classifier bake-off).
 */
export type StopAfter = "gate";
export type ChunkSet = "fixed" | "smart";
export type PageType =
  | "lab_report"
  | "attending_physician_statement"
  | "application_form"
  | "id_document"
  | "invoice"
  | "other";
/**
 * The human-reserved actions (AD-10).
 */
export type Decision = "keep" | "discard" | "accept" | "deny";
/**
 * The values of the `X-Demo-Role` header (AD-9).
 */
export type DemoRole = "customer" | "underwriter";
export type HttpMethod = "GET" | "POST";
/**
 * How a page came to wait in the triage queue (AD-7, AD-10).
 */
export type QueuedBy = "gate" | "customer";
export type ReasonEffect = "none" | "debit" | "decline";
/**
 * The seven services, by Dapr app id (AD-1).
 */
export type Service =
  | "web"
  | "intake"
  | "classification"
  | "extraction"
  | "retrieval"
  | "verdict"
  | "workflow";
/**
 * Verdict reasons that need no citation (AD-15).
 */
export type SystemReason =
  | "no_matching_rule"
  | "conflicting_rules"
  | "unverified_quote"
  | "low_confidence"
  | "step_limit";
export type Verdict = "standard" | "loaded" | "decline" | "refer";

export interface Contracts {
  ActorKind: ActorKind;
  AgentStep: AgentStep;
  AgentStepList: AgentStepList;
  AgentStepQuery: AgentStepQuery;
  AuditAction: AuditAction;
  AuditRecord: AuditRecord;
  AuditTrail: AuditTrail;
  CaseCreated: CaseCreated;
  CaseList: CaseList;
  CaseProgress: CaseProgress;
  CaseStarted: CaseStarted;
  CaseStatus: CaseStatus;
  CaseSummary: CaseSummary;
  ChunkSet: ChunkSet;
  Classification: Classification;
  ClassificationList: ClassificationList;
  ClassificationResult: ClassificationResult;
  ClassifierContender: ClassifierContender;
  ClassifierOutput: ClassifierOutput;
  ClassifyCommand: ClassifyCommand;
  Decision: Decision;
  DecisionRecorded: DecisionRecorded;
  DecisionRequest: DecisionRequest;
  DemoRole: DemoRole;
  ErrorBody: ErrorBody;
  ErrorCode: ErrorCode;
  ErrorDetail: ErrorDetail;
  ExtractFactsCommand: ExtractFactsCommand;
  ExtractedFact: ExtractedFact;
  ExtractionOutput: ExtractionOutput;
  Fact: Fact;
  FactList: FactList;
  FactSetResult: FactSetResult;
  Health: Health;
  HttpMethod: HttpMethod;
  JsonValue: JsonValue;
  Me: Me;
  Page: Page;
  PageBoxes: PageBoxes;
  PageBoxesQuery: PageBoxesQuery;
  PageDecisionRequest: PageDecisionRequest;
  PageList: PageList;
  PageProgress: PageProgress;
  PageQueue: PageQueue;
  PageQueueQuery: PageQueueQuery;
  PageStatus: PageStatus;
  PageText: PageText;
  PageType: PageType;
  QueuedBy: QueuedBy;
  QueuedPage: QueuedPage;
  ReadRuleArguments: ReadRuleArguments;
  Reason: Reason;
  ReasonEffect: ReasonEffect;
  RedactionCommand: RedactionCommand;
  RedactionResult: RedactionResult;
  RetrieverConfig: RetrieverConfig;
  RouteDetail: RouteDetail;
  RuleReadQuery: RuleReadQuery;
  RuleText: RuleText;
  SearchItem: SearchItem;
  SearchRequest: SearchRequest;
  SearchResponse: SearchResponse;
  SearchRulesArguments: SearchRulesArguments;
  Service: Service;
  StageStatus: StageStatus;
  StartCaseOptions: StartCaseOptions;
  StartCaseRequest: StartCaseRequest;
  StepOutcome: StepOutcome;
  StopAfter: StopAfter;
  SystemReason: SystemReason;
  ToolName: ToolName;
  TriagePage: TriagePage;
  TriageQueue: TriageQueue;
  UploadedCase: UploadedCase;
  Verdict: Verdict;
  VerdictDetail: VerdictDetail;
  VerdictOutput: VerdictOutput;
  VerdictRun: VerdictRun;
  VerdictRunCommand: VerdictRunCommand;
  VerdictRunList: VerdictRunList;
  VerdictRunRequest: VerdictRunRequest;
  VerdictRunRequested: VerdictRunRequested;
  VerdictRunResult: VerdictRunResult;
  WordBox: WordBox;
}
/**
 * One tool call of the verdict agent, as logged in `verdict.agent_step`.
 */
export interface AgentStep {
  arguments: {
    [k: string]: JsonValue;
  };
  case_id: string;
  error_code: ErrorCode | null;
  fact_id: string | null;
  latency_ms: number;
  occurred_at: string;
  outcome: StepOutcome;
  rule_ids: string[];
  step_no: number;
  tool: ToolName;
  verdict_run_id: string;
}
/**
 * Response of both agent log reads: the steps in the order they were made.
 *
 * Within a run by step number; across the runs of a case, run after run.
 * The answer is bounded: `has_more` says that more steps exist than are
 * listed.
 */
export interface AgentStepList {
  has_more: boolean;
  steps: AgentStep[];
}
/**
 * Query of `GET /cases/{case_id}/agent-steps?tool=&rule_id=`; both filters are optional.
 */
export interface AgentStepQuery {
  rule_id?: string | null;
  tool?: ToolName | null;
}
/**
 * One row of the audit trail; `workflow` is the only writer.
 */
export interface AuditRecord {
  action: AuditAction;
  actor: string;
  actor_kind: ActorKind;
  case_id: string;
  detail:
    | {
        /**
         * This interface was referenced by `undefined`'s JSON-Schema definition
         * via the `patternProperty` "\S".
         */
        [k: string]: number;
      }
    | RouteDetail
    | VerdictDetail
    | null;
  error_code?: ErrorCode | null;
  eval_run_id: string | null;
  occurred_at: string;
  page_id: string | null;
  ref: string;
  trace_id: string;
}
/**
 * Detail of `page.routed`: the status the gate gave the page, and the threshold it used.
 */
export interface RouteDetail {
  route: "extracting" | "awaiting_customer" | "awaiting_triage";
  threshold: number;
}
/**
 * Detail of `verdict.suggested`: the retriever configuration the run was made with (AD-15).
 *
 * A case gets one run per configuration it was started with, and each is
 * an event of its own: the detail says which.
 */
export interface VerdictDetail {
  retriever_config: RetrieverConfig;
}
/**
 * Response of `GET /cases/{case_id}/audit`: the case's events, oldest first.
 *
 * The order is the one `workflow` recorded the events in, so a cause never
 * comes after its effect, whatever the clocks of the services that set
 * `occurred_at` say. The answer is bounded: it holds the first events, and
 * `has_more` says that the case has more than are listed.
 */
export interface AuditTrail {
  case_id: string;
  events: AuditRecord[];
  has_more: boolean;
}
/**
 * Response of `POST /cases`; the request body is the PDF itself.
 */
export interface CaseCreated {
  case_id: string;
  document_id: string;
}
/**
 * Response of `GET /cases`: the cases, newest started first.
 *
 * Cases that belong to an eval run are left out. The answer is bounded:
 * `has_more` says that more cases exist than are listed.
 */
export interface CaseList {
  cases: CaseSummary[];
  has_more: boolean;
}
/**
 * One case in the underwriter's list.
 */
export interface CaseSummary {
  case_id: string;
  case_status: CaseStatus;
  page_count: number;
  started_at: string;
  waiting_page_count: number;
}
/**
 * Response of `GET /cases/{case_id}/progress`.
 */
export interface CaseProgress {
  case_id: string;
  case_status: CaseStatus;
  error_code?: ErrorCode | null;
  pages: PageProgress[];
  redaction_status: StageStatus;
}
export interface PageProgress {
  error_code?: ErrorCode | null;
  page_id: string;
  page_number: number;
  page_status: PageStatus;
}
/**
 * Response of `POST /cases/{case_id}/start`: the case as it was actually started.
 */
export interface CaseStarted {
  case_id: string;
  case_status: CaseStatus;
  classifier_contender: ClassifierContender;
  eval_run_id: string | null;
  /**
   * @minItems 1
   */
  retriever_configs: [RetrieverConfig, ...RetrieverConfig[]];
  stop_after: StopAfter | null;
}
/**
 * One classifier's reading of one page; it never carries a route (AD-7).
 */
export interface Classification {
  case_id: string;
  classification_id: string;
  confidence: number;
  contender: ClassifierContender;
  is_medical: boolean;
  page_id: string;
  page_type: PageType;
  reason: string;
}
/**
 * Response of `GET /cases/{case_id}/classifications`.
 */
export interface ClassificationList {
  case_id: string;
  classifications: Classification[];
}
export interface ClassificationResult {
  audit: AuditRecord;
  case_id: string;
  classification: Classification | null;
  classification_id: string;
  contender: ClassifierContender;
  error_code: ErrorCode | null;
  page_id: string;
  status: StageStatus;
}
/**
 * What one LLM classifier run must return; anything else is a failed parse.
 */
export interface ClassifierOutput {
  page_type: PageType;
  reason: string;
}
/**
 * Request of `POST /classifications`; key `case_id` + `page_id` + `contender`.
 */
export interface ClassifyCommand {
  case_id: string;
  contender: ClassifierContender;
  eval_run_id?: string | null;
  page_id: string;
}
/**
 * Response of `POST /cases/{case_id}/pages/{page_id}/decisions`: the stored decision.
 *
 * `page_status` is the status the decision left the page in. A repeat of a
 * decision is answered with the one stored the first time.
 */
export interface DecisionRecorded {
  actor: DemoRole;
  case_id: string;
  decision: Decision;
  decision_id: string;
  occurred_at: string;
  page_id: string;
  page_status: PageStatus;
}
/**
 * Request of `POST /cases/{case_id}/pages/{page_id}/decisions`.
 */
export interface DecisionRequest {
  actor: string;
  decision: Decision;
}
/**
 * The one error shape: `{"error": {"code", "message", "trace_id"}}`.
 */
export interface ErrorBody {
  error: ErrorDetail;
}
export interface ErrorDetail {
  code: ErrorCode;
  message: string;
  trace_id: string;
}
/**
 * Request of `POST /fact-sets`; key `case_id` + `page_id`.
 */
export interface ExtractFactsCommand {
  case_id: string;
  eval_run_id?: string | null;
  page_id: string;
}
export interface ExtractedFact {
  quote: string;
  statement: string;
}
/**
 * What the extraction model must return for one page; anything else is a failed parse.
 */
export interface ExtractionOutput {
  facts: ExtractedFact[];
}
export interface Fact {
  case_id: string;
  fact_id: string;
  page_id: string;
  page_number: number;
  quote: string;
  quote_end: number | null;
  quote_start: number | null;
  quote_verified: boolean;
  statement: string;
}
/**
 * Response of `GET /cases/{case_id}/facts`.
 */
export interface FactList {
  case_id: string;
  facts: Fact[];
}
export interface FactSetResult {
  audit: AuditRecord;
  case_id: string;
  error_code: ErrorCode | null;
  fact_ids: string[];
  fact_set_id: string;
  page_id: string;
  status: StageStatus;
  unverified_count: number;
}
/**
 * Response of every service's health and readiness routes.
 */
export interface Health {
  status?: "ok";
}
/**
 * Response of `GET /api/me`: the demo role `web` read from the request.
 */
export interface Me {
  role: DemoRole;
}
export interface Page {
  case_id: string;
  document_id: string;
  page_id: string;
  page_number: number;
}
/**
 * Response of `GET /pages/{page_id}/boxes`.
 */
export interface PageBoxes {
  boxes: WordBox[];
  page_height: number;
  page_id: string;
  page_number: number;
  page_width: number;
}
/**
 * One word of the page text and where it sits on the page.
 */
export interface WordBox {
  char_end: number;
  char_start: number;
  x0: number;
  x1: number;
  y0: number;
  y1: number;
}
/**
 * Query of `GET /pages/{page_id}/boxes`: an optional offset range into the page text.
 */
export interface PageBoxesQuery {
  quote_end?: number | null;
  quote_start?: number | null;
}
/**
 * Request of `POST /api/cases/{case_id}/pages/{page_id}/decisions`.
 *
 * It names no actor: `web` passes the request's demo role on as the actor
 * (AD-9), so a browser cannot say who decided.
 */
export interface PageDecisionRequest {
  decision: Decision;
}
/**
 * Response of `GET /cases/{case_id}/pages`; empty until redaction is done.
 */
export interface PageList {
  case_id: string;
  pages: Page[];
}
/**
 * Response of `GET /pages?status=`: the pages across cases that wait in that status.
 *
 * Oldest waiting first. Pages of a case that belongs to an eval run, that is
 * failed or completed, or that was started with `stop_after: gate` are left
 * out. The answer is bounded: `has_more` says that more pages wait than are
 * listed.
 */
export interface PageQueue {
  has_more: boolean;
  pages: QueuedPage[];
}
export interface QueuedPage {
  case_id: string;
  classifier_contender: ClassifierContender;
  page_id: string;
  page_number: number;
  page_status: PageStatus;
  queued_by?: QueuedBy | null;
}
/**
 * Query of `GET /pages?status=`.
 */
export interface PageQueueQuery {
  status: PageStatus;
}
/**
 * Response of `GET /pages/{page_id}/text`: the one stored reading of the page.
 */
export interface PageText {
  page_id: string;
  page_number: number;
  text: string;
}
/**
 * Arguments of the agent tool `read_rule`.
 */
export interface ReadRuleArguments {
  rule_id: string;
}
/**
 * One cited reason: a rule, the facts it was applied to, and its effect.
 */
export interface Reason {
  debit_pct: number | null;
  effect: ReasonEffect;
  /**
   * @minItems 1
   */
  fact_ids: [string, ...string[]];
  rule_id: string;
}
/**
 * Request of `POST /cases/{case_id}/redaction`; the key is the `case_id` in the path.
 */
export interface RedactionCommand {
  eval_run_id?: string | null;
}
export interface RedactionResult {
  audit: AuditRecord;
  case_id: string;
  document_id: string;
  error_code: ErrorCode | null;
  page_ids: string[];
  redaction_counts: {
    /**
     * This interface was referenced by `undefined`'s JSON-Schema definition
     * via the `patternProperty` "\S".
     */
    [k: string]: number;
  };
  status: StageStatus;
}
/**
 * Query of `GET /rules/{rule_id}`; without the parameter the `smart` chunk is returned.
 */
export interface RuleReadQuery {
  retriever_config?: RetrieverConfig | null;
}
/**
 * Response of `GET /rules/{rule_id}`.
 */
export interface RuleText {
  chunk_id: string;
  chunk_set: ChunkSet;
  impairment: string;
  manual_page: number;
  reference_rule_ids: string[];
  rule_id: string;
  text: string;
}
export interface SearchItem {
  chunk_id: string;
  impairment: string;
  manual_page: number;
  rank: number;
  rule_ids: string[];
  score: number;
  text: string;
}
/**
 * Request of `POST /searches`; the result is not stored.
 */
export interface SearchRequest {
  query: string;
  retriever_config: RetrieverConfig;
  top_k?: number;
}
export interface SearchResponse {
  items: SearchItem[];
  latency_ms: number;
  retriever_config: RetrieverConfig;
}
/**
 * Arguments of the agent tool `search_rules`.
 */
export interface SearchRulesArguments {
  fact_id: string;
  query: string;
}
/**
 * What a case may be started with; every option is optional (settings fill the gaps).
 *
 * Also the body of `web`'s `POST /api/cases/{case_id}/start`. It names no
 * actor: `web` passes the request's demo role on as the actor (AD-9), so a
 * browser cannot say who started the case.
 */
export interface StartCaseOptions {
  classifier_contender?: ClassifierContender | null;
  eval_run_id?: string | null;
  retriever_configs?: [RetrieverConfig, ...RetrieverConfig[]] | null;
  stop_after?: StopAfter | null;
}
/**
 * Request of `POST /cases/{case_id}/start`: the options, and who asks.
 *
 * `workflow` refuses a start that names no demo role.
 */
export interface StartCaseRequest {
  actor?: string | null;
  classifier_contender?: ClassifierContender | null;
  eval_run_id?: string | null;
  retriever_configs?: [RetrieverConfig, ...RetrieverConfig[]] | null;
  stop_after?: StopAfter | null;
}
/**
 * One page that waits for the underwriter, with what the classifier said of it.
 *
 * `web` composes it: the page from `workflow`'s queue, the reading from
 * `classification`, for the classifier the case was started with. The four
 * fields of the reading are null together when it could not be read; the
 * page can be decided all the same.
 */
export interface TriagePage {
  case_id: string;
  confidence: number | null;
  is_medical: boolean | null;
  page_id: string;
  page_number: number;
  page_type: PageType | null;
  queued_by?: QueuedBy | null;
  reason: string | null;
  thumbnail_path: string;
}
/**
 * Response of `GET /api/triage`: the pages that wait for the underwriter, oldest first.
 */
export interface TriageQueue {
  has_more: boolean;
  pages: TriagePage[];
}
/**
 * Response of `POST /api/cases`: the case `intake` created.
 *
 * It carries no status: the case is started by a call of its own
 * (`POST /api/cases/{case_id}/start`), and `workflow` reports its status
 * from then on (`GET /api/cases/{case_id}/progress`).
 */
export interface UploadedCase {
  case_id: string;
  document_id: string;
}
/**
 * What the verdict agent must return; `verdict`'s domain code finishes it (AD-15).
 */
export interface VerdictOutput {
  confidence: number;
  reasons: Reason[];
  system_reasons: SystemReason[];
  verdict: Verdict;
}
/**
 * One verdict run as read by `web`: always a suggestion, never a decision.
 */
export interface VerdictRun {
  case_id: string;
  confidence: number | null;
  error_code: ErrorCode | null;
  label?: "AI suggestion, not a decision";
  loading_pct: number | null;
  reasons: Reason[];
  retriever_config: RetrieverConfig;
  status: StageStatus;
  system_reasons: SystemReason[];
  verdict: Verdict | null;
  verdict_run_id: string;
}
/**
 * Request of `POST /verdict-runs`; key `case_id` + `retriever_config`.
 */
export interface VerdictRunCommand {
  case_id: string;
  eval_run_id?: string | null;
  retriever_config: RetrieverConfig;
}
/**
 * Response of `GET /cases/{case_id}/verdict-runs`: the case's runs, oldest first.
 *
 * The answer is bounded: `has_more` says that the case has more runs than
 * are listed.
 */
export interface VerdictRunList {
  case_id: string;
  has_more: boolean;
  verdict_runs: VerdictRun[];
}
/**
 * Request of `POST /cases/{case_id}/verdict-runs` (the Compare toggle, AD-11).
 */
export interface VerdictRunRequest {
  retriever_config: RetrieverConfig;
}
/**
 * The requested run's state; the run itself is read from `verdict`.
 *
 * `running` while the run is under way, then `done` or `failed`.
 */
export interface VerdictRunRequested {
  case_id: string;
  error_code?: ErrorCode | null;
  retriever_config: RetrieverConfig;
  status: StageStatus;
  verdict_run_id: string | null;
}
export interface VerdictRunResult {
  audit: AuditRecord;
  case_id: string;
  error_code: ErrorCode | null;
  retriever_config: RetrieverConfig;
  status: StageStatus;
  verdict: Verdict | null;
  verdict_run_id: string;
}
