/* Generated from contracts.schema.json by scripts/generate-contracts.mjs.
 * Do not edit: change packages/contracts, then run `npm run contracts:generate`. */

export type ActorKind = "human" | "ai";
export type JsonValue = unknown;
/**
 * The verdict agent's three tools (AD-15).
 */
export type ToolName = "list_facts" | "search_rules" | "read_rule";
export type AuditAction =
  | "document.redacted"
  | "page.classified"
  | "page.kept"
  | "page.discarded"
  | "page.accepted"
  | "page.denied"
  | "facts.extracted"
  | "verdict.suggested"
  | "stage.failed";
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
 * The retrieval ladder rows (AD-11).
 */
export type RetrieverConfig = "r1" | "r2" | "r3" | "r4" | "r5" | "r6";
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
  | "stage_timeout"
  | "stage_failed"
  | "redaction_failed"
  | "invalid_model_output"
  | "model_unavailable"
  | "upstream_unavailable"
  | "internal_error";
/**
 * The human-reserved actions (AD-10).
 */
export type Decision = "keep" | "discard" | "accept" | "deny";
/**
 * The values of the `X-Demo-Role` header (AD-9).
 */
export type DemoRole = "customer" | "underwriter";
export type HttpMethod = "GET" | "POST";
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
  CaseProgress: CaseProgress;
  CaseStarted: CaseStarted;
  CaseStatus: CaseStatus;
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
  PageList: PageList;
  PageProgress: PageProgress;
  PageQueue: PageQueue;
  PageQueueQuery: PageQueueQuery;
  PageStatus: PageStatus;
  PageText: PageText;
  PageType: PageType;
  QueuedPage: QueuedPage;
  ReadRuleArguments: ReadRuleArguments;
  Reason: Reason;
  ReasonEffect: ReasonEffect;
  RedactionCommand: RedactionCommand;
  RedactionResult: RedactionResult;
  RetrieverConfig: RetrieverConfig;
  RuleReadQuery: RuleReadQuery;
  RuleText: RuleText;
  SearchItem: SearchItem;
  SearchRequest: SearchRequest;
  SearchResponse: SearchResponse;
  SearchRulesArguments: SearchRulesArguments;
  Service: Service;
  StageStatus: StageStatus;
  StartCaseRequest: StartCaseRequest;
  StopAfter: StopAfter;
  SystemReason: SystemReason;
  ToolName: ToolName;
  UploadedCase: UploadedCase;
  Verdict: Verdict;
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
  fact_id: string | null;
  latency_ms: number;
  occurred_at: string;
  rule_ids: string[];
  step_no: number;
  tool: ToolName;
  verdict_run_id: string;
}
/**
 * Response of both agent log reads, in step order.
 */
export interface AgentStepList {
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
  detail: {
    /**
     * This interface was referenced by `undefined`'s JSON-Schema definition
     * via the `patternProperty` "\S".
     */
    [k: string]: number;
  } | null;
  eval_run_id: string | null;
  occurred_at: string;
  page_id: string | null;
  ref: string;
  trace_id: string;
}
/**
 * Response of `GET /cases/{case_id}/audit`: events in time order.
 */
export interface AuditTrail {
  case_id: string;
  events: AuditRecord[];
}
/**
 * Response of `POST /cases`; the request body is the PDF itself.
 */
export interface CaseCreated {
  case_id: string;
  document_id: string;
}
/**
 * Response of `GET /cases/{case_id}/progress`.
 */
export interface CaseProgress {
  case_id: string;
  case_status: CaseStatus;
  pages: PageProgress[];
  redaction_status: StageStatus;
}
export interface PageProgress {
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
  actor: DemoRole;
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
 * Response of `GET /cases/{case_id}/pages`; empty until redaction is done.
 */
export interface PageList {
  case_id: string;
  pages: Page[];
}
/**
 * Response of `GET /pages?status=`: pages across cases, eval-run cases left out.
 */
export interface PageQueue {
  pages: QueuedPage[];
}
export interface QueuedPage {
  case_id: string;
  page_id: string;
  page_number: number;
  page_status: PageStatus;
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
 * Request of `POST /cases/{case_id}/start`; every field is optional (settings fill the gaps).
 */
export interface StartCaseRequest {
  classifier_contender?: ClassifierContender | null;
  eval_run_id?: string | null;
  retriever_configs?: [RetrieverConfig, ...RetrieverConfig[]] | null;
  stop_after?: StopAfter | null;
}
/**
 * Response of `POST /api/cases`: the case `intake` created, with its status.
 */
export interface UploadedCase {
  case_id: string;
  document_id: string;
  status: CaseStatus;
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
 * Response of `GET /cases/{case_id}/verdict-runs`.
 */
export interface VerdictRunList {
  case_id: string;
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
 */
export interface VerdictRunRequested {
  case_id: string;
  retriever_config: RetrieverConfig;
  status: StageStatus;
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
