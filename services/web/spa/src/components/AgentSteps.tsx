import { useEffect, useId, useRef, useState, type FormEvent } from "react";
import {
  ApiError,
  getRunSteps,
  isRuleId,
  type StepNarrowing,
} from "../api/client";
import type { AgentStep, Fact, ToolName } from "../api/contracts.gen";
import { strings } from "../strings";
import { ErrorMessage } from "./ErrorMessage";
import { LocalTime } from "./LocalTime";
import "./AgentSteps.css";

/** How many characters of a long value are shown before it is cut; the whole value opens under it. */
const CUT_AT = 80;
const EVERY_STEP: StepNarrowing = { tool: null, ruleId: null };
const TOOLS = Object.keys(strings.steps.tool) as ToolName[];

/** The words for a key of a table of strings, or the key itself when there are none. */
function worded(table: Readonly<Record<string, string>>, key: string): string {
  // Own keys only: "constructor" must not find a prototype member.
  return Object.hasOwn(table, key) ? table[key]! : key;
}

type State =
  /** The first answer, or the first after the narrowing changed, is awaited. */
  | { kind: "reading" }
  /** `more`: how the read of the steps after the last one shown stands. */
  | {
      kind: "read";
      steps: AgentStep[];
      hasMore: boolean;
      more: "idle" | "reading" | "failed";
    }
  | { kind: "failed"; error: unknown };

/**
 * A value the model wrote, as text and never as HTML (security rule 22). A
 * long one is cut, and opens whole.
 */
function Value({ text }: { text: string }) {
  // By whole characters: a cut by code units could end in half of one.
  const characters = Array.from(text);
  if (characters.length <= CUT_AT) {
    return <>{text}</>;
  }
  return (
    <details>
      <summary>
        {strings.steps.cut(characters.slice(0, CUT_AT).join(""))}
      </summary>
      {text}
    </details>
  );
}

/** What the agent called the tool with: each argument by its name, as recorded. */
function Arguments({ step }: { step: AgentStep }) {
  const given = Object.entries(step.arguments);
  if (given.length === 0) {
    return <>{strings.steps.noArguments}</>;
  }
  return (
    <ul>
      {given.map(([name, value]) => (
        <li key={name}>
          {strings.steps.argumentName(name)}{" "}
          <Value
            text={typeof value === "string" ? value : JSON.stringify(value)}
          />
        </li>
      ))}
    </ul>
  );
}

/** A call's outcome, and for one that was refused or failed why, in plain words. */
function outcomeText(step: AgentStep): string {
  const outcome = worded(strings.steps.outcome, step.outcome);
  return step.error_code === null
    ? outcome
    : strings.steps.outcomeBecause(
        outcome,
        worded(strings.audit.failure, step.error_code),
      );
}

function StepRow({
  step,
  facts,
}: {
  step: AgentStep;
  facts: readonly Fact[] | null;
}) {
  const fact = facts?.find((listed) => listed.fact_id === step.fact_id);
  return (
    <tr>
      <th scope="row">{step.step_no}</th>
      <td>{worded(strings.steps.tool, step.tool)}</td>
      <td>
        <Arguments step={step} />
      </td>
      <td>
        {step.fact_id === null ? (
          strings.steps.noFact
        ) : fact === undefined ? (
          // The screen holds no list of facts with it: shown by its id.
          <code>{step.fact_id}</code>
        ) : (
          <Value text={fact.statement} />
        )}
      </td>
      <td>
        {step.rule_ids.length === 0
          ? strings.steps.noRules
          : step.rule_ids.join(", ")}
      </td>
      <td>{outcomeText(step)}</td>
      <td>{strings.steps.latency(step.latency_ms)}</td>
      <td>
        <LocalTime at={step.occurred_at} />
      </td>
    </tr>
  );
}

/**
 * The two filters. Choosing them changes nothing by itself: the form asks,
 * and the server narrows (AD-15). A rule id of another form is refused here
 * without a call, as a convenience; the server checks it again.
 */
function Narrow({
  narrowing,
  onNarrow,
}: {
  narrowing: StepNarrowing;
  onNarrow: (narrowing: StepNarrowing) => void;
}) {
  const [tool, setTool] = useState<ToolName | "">(narrowing.tool ?? "");
  const [rule, setRule] = useState(narrowing.ruleId ?? "");
  const [refused, setRefused] = useState(false);
  const toolId = useId();
  const ruleId = useId();
  const hintId = useId();
  const errorId = useId();

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    // Spaces around it and small letters are a matter of typing.
    const typed = rule.trim().toUpperCase();
    if (typed !== "" && !isRuleId(typed)) {
      setRefused(true);
      return;
    }
    setRefused(false);
    onNarrow({
      tool: tool === "" ? null : tool,
      ruleId: typed === "" ? null : typed,
    });
  }

  return (
    <form onSubmit={submit} noValidate>
      <fieldset>
        <legend>{strings.steps.narrowLegend}</legend>
        <p>
          <label htmlFor={toolId}>{strings.steps.toolLabel}</label>{" "}
          <select
            id={toolId}
            value={tool}
            onChange={(change) => setTool(change.target.value as ToolName | "")}
          >
            <option value="">{strings.steps.anyTool}</option>
            {TOOLS.map((name) => (
              <option key={name} value={name}>
                {strings.steps.tool[name]}
              </option>
            ))}
          </select>{" "}
          <label htmlFor={ruleId}>{strings.steps.ruleLabel}</label>{" "}
          <input
            id={ruleId}
            type="text"
            size={14}
            autoComplete="off"
            spellCheck={false}
            value={rule}
            aria-invalid={refused}
            aria-describedby={refused ? `${hintId} ${errorId}` : hintId}
            onChange={(change) => setRule(change.target.value)}
          />{" "}
          <button type="submit">{strings.steps.narrow}</button>{" "}
          {(narrowing.tool !== null || narrowing.ruleId !== null) && (
            <button
              type="button"
              onClick={() => {
                setTool("");
                setRule("");
                setRefused(false);
                onNarrow(EVERY_STEP);
              }}
            >
              {strings.steps.showAll}
            </button>
          )}
        </p>
        <p id={hintId}>
          <small>{strings.steps.ruleHint}</small>
        </p>
        {refused && (
          <p id={errorId} role="alert">
            {strings.steps.notARuleId}
          </p>
        )}
      </fieldset>
    </form>
  );
}

/**
 * The agent's log of one verdict run (story 2.8, AD-15): every tool call in
 * the order the server lists them, a refused or failed one as visible as a
 * done one. The screen only reads: nothing here edits, removes or runs a
 * step again. Narrowing and "more" each ask the server; the browser filters
 * and sorts nothing.
 */
export function AgentSteps({
  runId,
  facts = null,
  headingLevel,
  onClose,
}: {
  runId: string;
  /** The case's facts, when the screen holds them: a step then names its fact by its statement. */
  facts?: readonly Fact[] | null;
  headingLevel: 3 | 4;
  onClose: () => void;
}) {
  const [narrowing, setNarrowing] = useState<StepNarrowing>(EVERY_STEP);
  // Counts the requests to read from the start again.
  const [reads, setReads] = useState(0);
  const [state, setState] = useState<State>({ kind: "reading" });
  // Counts the lists asked for: an answer with more steps of an earlier
  // list is dropped.
  const list = useRef(0);
  const headingId = useId();
  const heading = useRef<HTMLHeadingElement>(null);
  const Heading = `h${headingLevel}` as const;

  // The steps open beside what was chosen: focus goes to them.
  useEffect(() => {
    heading.current?.focus();
  }, []);

  useEffect(() => {
    let current = true;
    getRunSteps(runId, narrowing, null).then(
      (listed) => {
        if (current) {
          setState({
            kind: "read",
            steps: listed.steps,
            hasMore: listed.has_more,
            more: "idle",
          });
        }
      },
      (error: unknown) => {
        if (current) setState({ kind: "failed", error });
      },
    );
    return () => {
      current = false;
    };
  }, [runId, narrowing, reads]);

  function startOver(next: StepNarrowing) {
    list.current += 1;
    setState({ kind: "reading" });
    setNarrowing(next);
    // Also when the narrowing is the same: the user asked to read again.
    setReads((count) => count + 1);
  }

  /** The steps after the last one shown: its step number is the cursor. */
  function readMore() {
    if (state.kind !== "read" || state.more === "reading") {
      return;
    }
    const last = state.steps[state.steps.length - 1];
    if (last === undefined) {
      return;
    }
    const asked = list.current;
    setState({ ...state, more: "reading" });
    getRunSteps(runId, narrowing, last.step_no).then(
      (listed) => {
        if (list.current !== asked) return;
        setState((shown) =>
          shown.kind === "read"
            ? {
                kind: "read",
                steps: [...shown.steps, ...listed.steps],
                hasMore: listed.has_more,
                more: "idle",
              }
            : shown,
        );
      },
      () => {
        if (list.current !== asked) return;
        setState((shown) =>
          shown.kind === "read" ? { ...shown, more: "failed" } : shown,
        );
      },
    );
  }

  const narrowed = narrowing.tool !== null || narrowing.ruleId !== null;
  return (
    <section className="agent-steps" aria-labelledby={headingId}>
      <Heading id={headingId} ref={heading} tabIndex={-1}>
        {strings.steps.heading(runId)}
      </Heading>
      <p>{strings.steps.intro}</p>
      <Narrow narrowing={narrowing} onNarrow={startOver} />
      {state.kind === "reading" && <p role="status">{strings.steps.reading}</p>}
      {state.kind === "failed" &&
        (state.error instanceof ApiError && state.error.code === "not_found" ? (
          <p role="alert">{strings.steps.unknownRun}</p>
        ) : (
          <ErrorMessage error={state.error} />
        ))}
      {state.kind === "read" &&
        (state.steps.length === 0 ? (
          <p>{narrowed ? strings.steps.noneMatch : strings.steps.none}</p>
        ) : (
          <table aria-label={strings.steps.table(runId)}>
            <thead>
              <tr>
                <th scope="col">{strings.steps.stepColumn}</th>
                <th scope="col">{strings.steps.toolColumn}</th>
                <th scope="col">{strings.steps.argumentsColumn}</th>
                <th scope="col">{strings.steps.factColumn}</th>
                <th scope="col">{strings.steps.rulesColumn}</th>
                <th scope="col">{strings.steps.outcomeColumn}</th>
                <th scope="col">{strings.steps.latencyColumn}</th>
                <th scope="col">{strings.steps.timeColumn}</th>
              </tr>
            </thead>
            <tbody>
              {state.steps.map((step) => (
                // A step number is one step of a run (AD-15).
                <StepRow key={step.step_no} step={step} facts={facts} />
              ))}
            </tbody>
          </table>
        ))}
      {state.kind === "read" && state.hasMore && (
        <p>
          {strings.steps.moreExist}{" "}
          <button
            type="button"
            onClick={readMore}
            disabled={state.more === "reading"}
          >
            {strings.steps.more}
          </button>
        </p>
      )}
      {state.kind === "read" && state.more === "reading" && (
        <p role="status">{strings.steps.readingMore}</p>
      )}
      {state.kind === "read" && state.more === "failed" && (
        <p role="alert">{strings.steps.moreFault}</p>
      )}
      <p>
        <button type="button" onClick={() => startOver(narrowing)}>
          {strings.steps.readAgain}
        </button>{" "}
        <button type="button" onClick={onClose}>
          {strings.steps.close}
        </button>
      </p>
    </section>
  );
}
