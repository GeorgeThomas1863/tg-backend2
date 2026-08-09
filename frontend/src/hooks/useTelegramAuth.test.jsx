import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, test, vi } from "vitest";
import { fetchTelegramAuthStatus, postTelegramCode, postTelegramLogout, postTelegramPassword, postTelegramPhone } from "../api/client";
import { useTelegramAuth } from "./useTelegramAuth";

vi.mock("../api/client", () => ({ fetchTelegramAuthStatus: vi.fn(), postTelegramCode: vi.fn(), postTelegramLogout: vi.fn(), postTelegramPassword: vi.fn(), postTelegramPhone: vi.fn() }));

const loggedOut = { authorized: false, user: null, pending_step: null };
const authorized = { authorized: true, user: { username: "alice" }, pending_step: null };

beforeEach(() => {
  for (const mock of [fetchTelegramAuthStatus, postTelegramCode, postTelegramLogout, postTelegramPassword, postTelegramPhone]) mock.mockReset();
});

describe("useTelegramAuth", () => {
  test.each([loggedOut, authorized, { ...loggedOut, pending_step: "code" }])("loads status %#", async (response) => {
    fetchTelegramAuthStatus.mockResolvedValue(response);
    const { result } = renderHook(() => useTelegramAuth());
    expect(result.current.busy).toBe(true);
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.status).toEqual(response);
    expect(result.current.error).toBeNull();
  });

  test("surfaces initial failure", async () => {
    fetchTelegramAuthStatus.mockRejectedValue(new Error("HTTP 502"));
    const { result } = renderHook(() => useTelegramAuth());
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.error).toBe("HTTP 502");
  });

  test.each([["sendCode", postTelegramPhone, "+1555"], ["submitCode", postTelegramCode, "12345"], ["submitPassword", postTelegramPassword, "secret"], ["logout", postTelegramLogout, undefined]])("%s refreshes after success", async (method, operation, value) => {
    fetchTelegramAuthStatus.mockResolvedValueOnce(loggedOut).mockResolvedValueOnce(authorized);
    operation.mockResolvedValue({ success: true, authorized: true });
    const { result } = renderHook(() => useTelegramAuth());
    await waitFor(() => expect(result.current.loading).toBe(false));
    await act(() => value === undefined ? result.current[method]() : result.current[method](value));
    expect(fetchTelegramAuthStatus).toHaveBeenCalledTimes(2);
    expect(result.current.status).toEqual(authorized);
  });

  test("treats the 2FA transition as success and refreshes into password step", async () => {
    fetchTelegramAuthStatus.mockResolvedValueOnce({ ...loggedOut, pending_step: "code" }).mockResolvedValueOnce({ ...loggedOut, pending_step: "password" });
    postTelegramCode.mockResolvedValue({ success: true, authorized: false, next_step: "password" });
    const { result } = renderHook(() => useTelegramAuth());
    await waitFor(() => expect(result.current.loading).toBe(false));
    await act(() => result.current.submitCode("12345"));
    expect(result.current.status.pending_step).toBe("password");
    expect(result.current.error).toBeNull();
  });

  test("failed mutation sets error without refresh", async () => {
    fetchTelegramAuthStatus.mockResolvedValue(loggedOut);
    postTelegramPhone.mockResolvedValue({ success: false, message: "Bad phone" });
    const { result } = renderHook(() => useTelegramAuth());
    await waitFor(() => expect(result.current.loading).toBe(false));
    await act(() => result.current.sendCode("x"));
    expect(result.current.error).toBe("Bad phone");
    expect(fetchTelegramAuthStatus).toHaveBeenCalledOnce();
  });

  test("rejects a concurrent mutation and reports busy", async () => {
    fetchTelegramAuthStatus.mockResolvedValue(loggedOut);
    let finish;
    postTelegramPhone.mockReturnValue(new Promise((resolve) => { finish = resolve; }));
    const { result } = renderHook(() => useTelegramAuth());
    await waitFor(() => expect(result.current.loading).toBe(false));
    let first;
    act(() => { first = result.current.sendCode("+1"); });
    await waitFor(() => expect(result.current.busy).toBe(true));
    await expect(result.current.submitCode("1")).resolves.toEqual({ success: false, message: "Another Telegram action is still running" });
    await act(async () => { finish({ success: false, message: "stop" }); await first; });
    expect(result.current.busy).toBe(false);
  });
});
