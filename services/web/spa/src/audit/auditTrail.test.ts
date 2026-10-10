import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { setRole } from "../role/roleStore";
import { caseProgress, fakeServer, json } from "../test/server";
import { useAuditTrail } from "./auditTrail";

const CASE = "019a0000-0000-7000-8000-000000010000";
const PROGRESS_PATH = `/api/cases/${CASE}/progress`;

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("1.12 following a case's audit trail", () => {
  it("sends no queued read once the screen is gone", async () => {
    // The first read of the case stays out until the test lets it answer.
    let answer: (response: Response) => void = () => undefined;
    const held = new Promise<Response>((resolve) => {
      answer = resolve;
    });
    let first = true;
    const server = fakeServer((call) => {
      if (call.path !== PROGRESS_PATH) {
        return undefined;
      }
      if (first) {
        first = false;
        return held;
      }
      return json(200, caseProgress(CASE));
    });
    server.started.add(CASE);
    setRole("underwriter");
    const hook = renderHook(() => useAuditTrail(CASE));
    await waitFor(() => expect(server.calls).toHaveLength(1));

    // Asked for while the first read is out: it is queued behind it.
    act(() => hook.result.current.refresh());
    expect(server.calls).toHaveLength(1);
    hook.unmount();
    await act(async () => {
      answer(json(200, caseProgress(CASE)));
      await held;
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    // The read that was out ends with its own second half; nothing follows it.
    expect(server.calls.map((call) => call.path)).toEqual([
      PROGRESS_PATH,
      `/api/cases/${CASE}/audit`,
    ]);
  });

  it("sends the queued read while the screen is still there", async () => {
    let answer: (response: Response) => void = () => undefined;
    const held = new Promise<Response>((resolve) => {
      answer = resolve;
    });
    let first = true;
    const server = fakeServer((call) => {
      if (call.path !== PROGRESS_PATH) {
        return undefined;
      }
      if (first) {
        first = false;
        return held;
      }
      return json(200, caseProgress(CASE));
    });
    server.started.add(CASE);
    setRole("underwriter");
    const hook = renderHook(() => useAuditTrail(CASE));
    await waitFor(() => expect(server.calls).toHaveLength(1));

    act(() => hook.result.current.refresh());
    await act(async () => {
      answer(json(200, caseProgress(CASE)));
      await held;
    });

    await waitFor(() =>
      expect(
        server.calls.filter((call) => call.path === PROGRESS_PATH),
      ).toHaveLength(2),
    );
    expect(hook.result.current.state.kind).toBe("read");
  });
});
