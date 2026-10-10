---
title: 'Story 3.7, changed: row r4 reranks with Cohere Rerank on Foundry'
type: 'feature'
created: '2026-10-08'
status: 'done'
baseline_commit: '0e0c850297265130e54120035a2c46d096208f33'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/CLAUDE.md'
  - '{project-root}/_bmad-output/implementation-artifacts/epic-3-context.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-3-7-row-r4-with-a-reranker.md'
  - '{project-root}/docs/standards/coding-style.md'
  - '{project-root}/docs/standards/security.md'
  - '{project-root}/docs/standards/azure.md'
  - '{project-root}/docs/standards/terraform.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Row `r4` was built with an LLM reranker because Cohere Rerank's availability could not be checked. Azure's model catalogue now lists it for West US 3, and the epic asks for Cohere Rerank where it can be deployed.

**Approach:** Row `r4` reranks with Cohere Rerank on Foundry (`Cohere-rerank-v4.0-fast`, the owner's choice of 2026-10-08) in place of the LLM reranker. Everything else of story 3.7 stands: the same fused candidates as `r3`, one reranker call, the row's own deadline, no fall back to the fused order. Azure stays down: the reranker is built against a local stand-in.

## Boundaries & Constraints

**Always:**
- Spine AD-11, AD-12, AD-16; NFR4. `r4` differs from `r3` in the order alone: the best fused candidates (the same depth setting, never fewer than `top_k`) go to the reranker with the query in one call, each candidate as the text the LLM reranker was shown. `rank` is the reranker's order; `score` is its relevance score, which the service gives between 0 and 1; candidates of equal score stay in the fused order.
- The reranker is a model deployment on the one Foundry account, pinned to an exact version with auto-upgrade off, Global Standard, low capacity, with the default content filter, reached with `retrieval`'s identity and no key. Its deployment name reaches code only as configuration. The deployment is added to the foundation stack and its name passed to the `app` stack.
- An answer that does not score exactly the candidates it was given (one left out, an index it was not given or twice, a score that is no number from 0 to 1) is no answer: `model_unavailable`. A refused call is `upstream_unavailable`, as today. The row keeps its own deadline and call timeout, a timed-out call is never sent again, and rerank calls leave room in the gateway's cap for the other rows' embeddings, as today.
- `r4` is available where `retrieval` is given the reranker deployment, and refused as not available elsewhere; the three lists of rows stay equal. The LLM reranker goes: its prompt, its chat call, its settings and its stand-in modes are removed, and no switch between two rerankers is left behind. `retrieval`'s service no longer needs the chat deployment for `r4`; `r6` still needs it.
- The scoreboard names the reranker: `r4`'s method in `evals/static-metrics.yaml`. Logs carry the row, counts and timings, never the query or a chunk's text.
- Tests follow the owner's rule in `CLAUDE.md`: no package's count grows; the story 3.7 tests are changed in place.

**Never:**
- No second reranker, no reranking of another row, no change to the fusion or to rows other than `r4`.
- Do not bring Azure up, plan or apply Terraform, or run any `az` or `gh` command that changes something. The stand-in ships in no image.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Search `r4` | A query, `r4`, `top_k` 5 | The 20 best fused candidates sent to Cohere Rerank; the 5 best by its score, common shape, scores 0 to 1 | N/A |
| Same candidates | The same query on `r3` and `r4` | `r4`'s items are among `r3`'s fused candidates | N/A |
| Bad answer | A result is missing, names an index not given, or a score is out of range | No items | 503 `model_unavailable` |
| Refused | The service answers 4xx | No items | 502 `upstream_unavailable` |
| Slow | The call outlasts `r4`'s deadline | No partial answer; sent once | `model_unavailable`; the log says the reranker was waited for |
| Not configured | No reranker deployment set | `r4` refused; the other rows unaffected, `r6` included | 409 `retriever_not_available` |
| Verdict and bake-off | A case run with `r4`; the runner over the cases | As for the other rows; the scoreboard shows `r4` with the reranker's name | N/A |

</frozen-after-approval>

## Code Map

- `services/retrieval/src/retrieval/domain/rerank.py` (the request text and the checked answer of the LLM reranker), `domain/search.py` (`hybrid_reranked_search`, `rerank_depth`, `deadline_of`, the stats and the log line), `domain/ports.py` (`Reranker`), `domain/rows.py` (`needs_reranker`, `available_rows`), `adapters/model.py` (`ModelGateway.relevance`, the part of the cap rerank calls may hold, no resend after a time-out, `RERANK_FORMAT`), `adapters/http/app.py` (`build_query_gateway`, the "row r4 is off" line), `prompts/rerank.md`, `settings.py` (`search_rerank_*`)
- `packages/synthdata/src/synthdata/foundry_standin.py` -- the rerank request it recognises by its schema name, and the modes `rerank_incomplete` and `rerank_slow`: to become a rerank route answered by word overlap, with the same two failures
- `services/verdict/src/verdict/domain/run.py` (`RERANKER_RETRIEVER_CONFIGS`), `packages/synthdata/tests/test_foundry_standin.py` (the three lists, the callers' timeouts above `r4`'s deadline, read from `dapr.yaml` and `terraform.tfvars`), `test_manual_search_end_to_end.py`, `evals/tests/test_whole_path.py`, `services/retrieval/tests/test_retrieval_search.py` and `support/retrieval_fakes.py`
- `infra/demo/foundation/` -- how the chat and embedding deployments are declared (pinned version, `NoAutoUpgrade`, Global Standard, capacity) and output; `infra/demo/app/` -- `locals.tf` (`retrieval`'s env), `variables.tf`, `terraform.tfvars` (the rerank variables of story 3.7); `dapr.yaml`, `tools/`
- `evals/static-metrics.yaml` (row `r4`), `README.md`, `infra/bootstrap/README.md`, `_bmad-output/implementation-artifacts/deferred-work.md` (the story 3.7 entries: the Cohere check is answered; the LLM reranker's checks fall away)

## Tasks & Acceptance

**Execution:**
- [x] `services/retrieval/` -- the Cohere Rerank call and its checked answer in place of the chat call; settings; availability by the reranker deployment
- [x] `packages/synthdata/` -- the stand-in's rerank route with its failure modes, in place of the chat rerank task
- [x] `infra/demo/foundation/`, `infra/demo/app/`, `dapr.yaml`, `tools/` -- the deployment, its name to `retrieval`, the settings
- [x] Tests changed in place, no count growing: `r4`'s search, a bad answer, refused, slow, not configured; the cross-service tests
- [x] `evals/static-metrics.yaml`, `README.md`, `infra/bootstrap/README.md`, `deferred-work.md` -- the reranker named; the Azure checks of the new call

**Acceptance Criteria:**
- Given a search with `r4`, when it runs, then it takes the `r3` candidates and reranks them with Cohere Rerank on Foundry, and returns the common result shape.
- Given `r4` exists, when the runner runs again, then the scoreboard shows `r4` and the reranker used.
- Given the repository, when the commands under Verification run, then all pass.

## Implementation Notes

- **The call:** `ModelGateway.relevance(query, documents)` (`adapters/model.py`) posts `{"model": <deployment>, "query", "documents"}` to `<RETRIEVAL_MODEL_ENDPOINT>/providers/cohere/v2/rerank`, or to the whole address `RETRIEVAL_RERANK_URL` names, signed in with a token of the scope `RETRIEVAL_RERANK_TOKEN_SCOPE`, through the OpenAI client the gateway already has (its generic `post`, an absolute address), so the sign-in, `_call`'s retries, the part of the cap rerank calls may hold, the no-resend after a time-out and the error mapping are unchanged. No `top_n`. It returns the body as text; no Cohere SDK was added.
- **The checked answer:** `domain/rerank.py`: `rerank_document` (a candidate's impairment, a line break, its text) and `scores_in_order(answer, candidates)`, which reads `results[].index` and `results[].relevance_score` and raises `RerankAnswerInvalid` unless every candidate has one result, by its place, with a number from 0 to 1. `hybrid_reranked_search` sorts by score, stable. The stats, the log lines, the deadline and the failures are as they were.
- **Settings and availability:** new `RETRIEVAL_RERANK_DEPLOYMENT`, `RETRIEVAL_RERANK_URL` and `RETRIEVAL_RERANK_TOKEN_SCOPE`; `RETRIEVAL_SEARCH_RERANK_MAX_COMPLETION_TOKENS` is gone. `SearchPorts.reranker` is the gateway where the reranker deployment is set (`row r4 is off: ... missing=RETRIEVAL_RERANK_DEPLOYMENT` otherwise). The search's gateway is given no chat deployment any more; `r6`'s knowledge base still needs `RETRIEVAL_CHAT_DEPLOYMENT`. `verdict`: `CHAT_DEPLOYMENT_RETRIEVER_CONFIGS` is `r6` alone, and the default rows subtract `RERANKER_RETRIEVER_CONFIGS` too. Removed: `prompts/rerank.md`, `RERANK_FORMAT` and its schema, the chat call.
- **Stand-in:** `foundry_standin.py` has the route `POST /providers/cohere/v2/rerank` (score = the share of the query's words a document holds, results best first), `rerank_requests`, and the modes `rerank_incomplete` and `rerank_slow` on it; `invalid` answers prose and `throttled` 429 there too. The chat route no longer knows a rerank request.
- **Infra:** `infra/demo/foundation`: `model_deployments` gained an optional `model_format` (`OpenAI` or `Cohere`, default `OpenAI`) and optional `content_filter`, `version_upgrade_option` and `sku` per deployment, and a validation that the `chat` and `embedding` keys are there (the `rerank` entry may be left out, and `r4` is then off); `terraform.tfvars` adds `rerank` (`Cohere-rerank-v4.0-fast`, format `Cohere`, version `1`, capacity 1). `infra/demo/app`: `RETRIEVAL_RERANK_DEPLOYMENT` from `model_deployment_names["rerank"]` (empty where there is none), the new variables `rerank_url` and `rerank_token_scope`, `search_rerank_max_completion_tokens` removed. `dapr.yaml`: `RETRIEVAL_RERANK_DEPLOYMENT: "local-stand-in-rerank"`. `tools/` needed no change (the ingestion job asks no reranker; the two modes named in `tools/dev.sh` still exist). Formatted and validated only.
- **Tests, changed in place, no count growing:** `retrieval`'s two `r4` tests (the real gateway against a transport: the request, the address, the timeout, the order, the scores; seven bad answers, unavailable, refused, slow) and the "not configured" assertions inside story 3.3's test (the app built from its settings with and without the reranker deployment; `r6` still answers); the three-lists test; the test over the real manual (one rerank call of 20 documents per search to the reranker deployment, no chat call; `r4` refused without the deployment while `r3`, `r5`, `r6` answer); the runner's whole-path test (the scoreboard's method).
- **Verification run (2026-10-08):** `docker compose up -d --wait`; `uv sync`, `ruff format --check`, `ruff check`, `mypy packages services evals` clean; `uv run pytest --cov` 545 passed, 90.78%; the whole-path test of `evals/tests` among them; the `retrieval` image builds and holds neither `rerank.md` nor the stand-in; `terraform fmt -check -recursive` and `validate` of both stacks clean.
- **After review 1 (2026-10-08):** the address of the call and the scope of its token became settings and variables of the `app` stack; the gateway logs `search_units=` for a rerank call in place of token counts, and an answer that is no text is a failed call (`rerank_answer_not_text`); the foundation stack takes per-deployment overrides and may leave the `rerank` deployment out, and both READMEs say how the environment comes up without `r4`; the existing `r4` test now runs as in Azure (tokens, another address with a query string, another scope) and sends one refused call through the real gateway (502). Only the tests of the edited files were run after this.
- **Not proven here:** anything against Azure. The address of the rerank route and the scope of its token were written from memory of the documentation, none was fetched; both are settings for that reason and are the first check in `deferred-work.md`. The deployment's capacity (1) and whether it takes the content filter name and `NoAutoUpgrade` are unverified. `tools/dev.sh` was not started.

## Spec Change Log

## Review Triage Log

Review 1 (2026-10-08): blind hunter (B), edge-case hunter (E), verification-gap (V). No intent gap, no bad spec.

| # | Finding | Verdict | Route | Evidence |
| --- | --- | --- | --- | --- |
| B1, B2, E1 | The likeliest failures of the unverified call cannot be mended by configuration: the path takes no query string and no other host, and the token's scope is fixed in code | medium | patch | The path's pattern refuses `?api-version=`, it is always joined to the model endpoint, and the call reuses the Cognitive Services scope. A wrong guess would be a code change and an image build inside the one Azure session. |
| B5, E2, E3 | The Cohere deployment gets the OpenAI deployments' content filter, upgrade option and sku with no way to differ, and its format is not validated | medium | patch | All three are named as unverified for a Cohere model; a refusal at the first apply should be a change of the variables file. |
| B4 | Nothing says what to do when the Cohere model cannot be deployed | medium | patch | The LLM reranker is gone and the foundation stack demands the `rerank` key; every `r4` search would be 502 and Compare's default pair would fail. |
| B3 | The role comment still lists two deployments, and the new route is below the account, not the project | low | patch | A comment and an Azure check; no grant is written on a guess. |
| B6, B7, V1 | A refused call is not proven through the real gateway, and the path setting is never set to another value in a test | medium | patch | Filed evidence: a hard-coded path passes every test. |
| B9, E5 | Rerank calls log 0 tokens, which reads as free | low | patch | The removed chat call reported tokens; the answer's billed units are read nowhere. |
| B10 | An answer of another type from the client becomes "empty" and a 503 | low | patch | A direct correction to a reason of its own. |
| B12, B13, B14, B17, V-other 1 | Stale text: answered entries of `deferred-work.md` that still read as tasks, the hand-off note's line for 3.7, a test comment in the SPA, the epic context, a comment on capacity, the spec's note on the whole-path test, "search service" used for `retrieval`'s search | low | patch | Direct corrections. |
| B16 | Nothing records that queries and manual text now go to another provider's model | low | patch | The accepted exception in `security.md` covers Global Standard deployments of synthetic data; one entry for the owner to confirm that it covers this model too. |
| B15 | The retirement date of the model is not recorded | low | reject | The catalogue query of 2026-10-08 did not ask for it; it is an Azure check in `deferred-work.md`. |
| B11 | The frozen block says the stand-in modes are removed, and two are kept | low | reject | The two modes now act on the rerank route, which the task line asks for ("with the same two failures"); the frozen sentence is about the chat rerank task, which is gone. |
| B8, V2 | The stand-in's throttled and bad-request branches on the rerank route are run by no test | low | defer | Filed disposition. |
| B17b | The three-lists test matches file text | low | reject | As in story 3.3; the plan against a real state is an Azure check. |
| E4 | The test's transport raises an opaque error for a document it has no score for | low | reject | A test helper. |

## Design Notes

- Checked on 2026-10-08 with a read-only catalogue query (`az cognitiveservices model list --location westus3`): `Cohere-rerank-v4.0-fast` and `Cohere-rerank-v4.0-pro`, format `Cohere`, version `1`, sku `GlobalStandard`.
- **Azure checks for `deferred-work.md`:** the address and body of the rerank call on a Foundry account (the request and answer shapes are written from documentation and proven against the stand-in only); that the identity's Foundry role lets it call the deployment; that the deployment can be created from Terraform with that format, version and sku and whether it needs marketplace terms accepted; the score's range; latency for 20 chunks against the row's deadline; the largest document and the largest number of documents a call takes; the price, for the scoreboard's cost.
- The owner did not say whether the LLM reranker should stay as a fallback; the epic names one reranker, so it is removed. Git history keeps it.
- Do not commit or push. No `git stash` or other git command that changes the tree or the index. Scratch files go in the gitignored `.work/` folder. Do not run `tools/dev.sh` unless nothing else holds its ports, and stop what you start. Another agent is cutting tests under `services/web/spa`: leave that folder alone. Tests that use the scheduler emulator fail each other when two runs overlap: run them once at the end.
- Approval: Darrel chose `fast` on 2026-10-08 after the availability check; this spec was not reviewed by him before implementation.

## Verification

**Commands:**
- `docker compose up -d --wait`, then `uv sync && uv run ruff format --check . && uv run ruff check . && uv run mypy packages services evals && uv run pytest --cov` -- expected: all pass, coverage at least 80%
- `docker build -f services/retrieval/Dockerfile -t aiuw-retrieval:dev .` -- expected: builds
- `.work/bin/terraform -chdir=infra/demo/app fmt -check -recursive && .work/bin/terraform -chdir=infra/demo/app validate && .work/bin/terraform -chdir=infra/demo/foundation fmt -check -recursive && .work/bin/terraform -chdir=infra/demo/foundation validate` -- expected: clean
