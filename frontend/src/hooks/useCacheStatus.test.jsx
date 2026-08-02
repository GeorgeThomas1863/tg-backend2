import { beforeEach, describe, expect, test, vi } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";
import { fetchCacheStatus, postCachePaused } from "../api/client";
import { useCacheStatus } from "./useCacheStatus";

vi.mock("../api/client", () => ({ fetchCacheStatus: vi.fn(), postCachePaused: vi.fn() }));

function buildStatus(totalBytes, paused = false) {
  return {
    total_bytes: totalBytes,
    max_bytes: 1000,
    paused,
    active: null,
    videos: {},
  };
}

function buildHttpError(status) {
  const error = new Error(`HTTP ${status}`);
  error.status = status;
  return error;
}

beforeEach(() => {
  vi.useRealTimers();
  fetchCacheStatus.mockReset();
  postCachePaused.mockReset();
});

describe("useCacheStatus", () => {
  test("does not fetch while disabled", () => {
    renderHook(() => useCacheStatus(false));

    expect(fetchCacheStatus).not.toHaveBeenCalled();
  });

  test("fetches immediately and every 3000 ms while enabled", async () => {
    vi.useFakeTimers();
    fetchCacheStatus.mockResolvedValue(buildStatus(100));

    renderHook(() => useCacheStatus(true));
    expect(fetchCacheStatus).toHaveBeenCalledTimes(1);

    await act(() => vi.advanceTimersByTimeAsync(3000));
    expect(fetchCacheStatus).toHaveBeenCalledTimes(2);
  });

  test("stops polling after a 401 response", async () => {
    vi.useFakeTimers();
    fetchCacheStatus.mockRejectedValue(buildHttpError(401));

    renderHook(() => useCacheStatus(true));
    await act(() => Promise.resolve());
    await act(() => vi.advanceTimersByTimeAsync(9000));

    expect(fetchCacheStatus).toHaveBeenCalledTimes(1);
  });

  test("blocks toggle requests after a 401 stops polling", async () => {
    vi.useFakeTimers();
    fetchCacheStatus
      .mockResolvedValueOnce(buildStatus(100))
      .mockRejectedValueOnce(buildHttpError(401));

    const { result } = renderHook(() => useCacheStatus(true));
    await act(() => Promise.resolve());
    await act(() => vi.advanceTimersByTimeAsync(3000));
    await act(() => result.current.togglePaused());

    expect(postCachePaused).not.toHaveBeenCalled();
    expect(fetchCacheStatus).toHaveBeenCalledTimes(2);
  });

  test("keeps the last status and continues polling after a transient error", async () => {
    vi.useFakeTimers();
    const firstStatus = buildStatus(100);
    const nextStatus = buildStatus(200);
    fetchCacheStatus
      .mockResolvedValueOnce(firstStatus)
      .mockRejectedValueOnce(buildHttpError(500))
      .mockResolvedValueOnce(nextStatus);

    const { result } = renderHook(() => useCacheStatus(true));
    await act(() => Promise.resolve());
    expect(result.current.status).toEqual(firstStatus);

    await act(() => vi.advanceTimersByTimeAsync(3000));
    expect(result.current.status).toEqual(firstStatus);

    await act(() => vi.advanceTimersByTimeAsync(3000));
    expect(result.current.status).toEqual(nextStatus);
    expect(fetchCacheStatus).toHaveBeenCalledTimes(3);
  });

  test("derives speed from two successful samples", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-01-01T00:00:00Z"));
    fetchCacheStatus
      .mockResolvedValueOnce(buildStatus(100))
      .mockResolvedValueOnce(buildStatus(700));

    const { result } = renderHook(() => useCacheStatus(true));
    await act(() => Promise.resolve());
    expect(result.current.speedBps).toBeNull();

    await act(() => vi.advanceTimersByTimeAsync(3000));
    expect(result.current.speedBps).toBe(200);
  });

  test("posts the inverse paused state and refetches after success", async () => {
    const initialStatus = buildStatus(100, false);
    const refreshedStatus = buildStatus(100, true);
    fetchCacheStatus.mockResolvedValueOnce(initialStatus).mockResolvedValueOnce(refreshedStatus);
    postCachePaused.mockResolvedValue({ success: true });

    const { result } = renderHook(() => useCacheStatus(true));
    await waitFor(() => expect(result.current.status).toEqual(initialStatus));

    await act(() => result.current.togglePaused());

    expect(postCachePaused).toHaveBeenCalledWith(true);
    expect(fetchCacheStatus).toHaveBeenCalledTimes(2);
    expect(result.current.status).toEqual(refreshedStatus);
  });
});
