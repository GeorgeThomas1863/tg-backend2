import { beforeEach, describe, expect, test, vi } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";
import { fetchCacheStatus, postCacheClear, postCachePaused, postCacheSettings } from "../api/client";
import { getActiveSlots, isVideoDownloading, useCacheStatus } from "./useCacheStatus";

vi.mock("../api/client", () => ({ fetchCacheStatus: vi.fn(), postCacheClear: vi.fn(), postCachePaused: vi.fn(), postCacheSettings: vi.fn() }));

function buildStatus(totalBytes, paused = false) {
  return {
    total_bytes: totalBytes,
    max_bytes: 1000,
    paused,
    active: null,
    videos: {},
    cache_dir: "C:\\cache",
    max_gb: 20,
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
  postCacheClear.mockReset();
  postCacheSettings.mockReset();
});

describe("useCacheStatus", () => {
  test("uses every active slot and falls back to legacy active only when slots are absent", () => {
    const status = { active: { msg_id: 1 }, active_slots: [{ msg_id: 2 }, { msg_id: 3 }] };
    expect(getActiveSlots(status)).toEqual(status.active_slots);
    expect(isVideoDownloading(status, 3)).toBe(true);
    expect(isVideoDownloading(status, 1)).toBe(false);
    expect(isVideoDownloading({ active: { msg_id: 1 } }, 1)).toBe(true);
  });
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

  test("saves settings and refetches status after success", async () => {
    const initialStatus = buildStatus(100);
    const refreshedStatus = { ...buildStatus(100), max_gb: 40 };
    const fields = { cache_max_gb: 40 };
    fetchCacheStatus.mockResolvedValueOnce(initialStatus).mockResolvedValueOnce(refreshedStatus);
    postCacheSettings.mockResolvedValue({ success: true, message: "Saved" });

    const { result } = renderHook(() => useCacheStatus(true));
    await waitFor(() => expect(result.current.status).toEqual(initialStatus));

    let saveResult;
    await act(async () => { saveResult = await result.current.saveSettings(fields); });

    expect(postCacheSettings).toHaveBeenCalledWith(fields);
    expect(fetchCacheStatus).toHaveBeenCalledTimes(2);
    expect(result.current.status).toEqual(refreshedStatus);
    expect(saveResult).toEqual({ success: true, message: "Saved" });
  });

  test("returns a failed settings result without refetching", async () => {
    const failedResult = { success: false, message: "Invalid folder" };
    fetchCacheStatus.mockResolvedValueOnce(buildStatus(100));
    postCacheSettings.mockResolvedValue(failedResult);

    const { result } = renderHook(() => useCacheStatus(true));
    await waitFor(() => expect(result.current.status).not.toBeNull());

    let saveResult;
    await act(async () => { saveResult = await result.current.saveSettings({ cache_dir: "D:\\cache" }); });

    expect(saveResult).toEqual(failedResult);
    expect(fetchCacheStatus).toHaveBeenCalledTimes(1);
  });

  test("clears the cache and refetches status after success", async () => {
    const initialStatus = buildStatus(100);
    const refreshedStatus = buildStatus(0);
    fetchCacheStatus.mockResolvedValueOnce(initialStatus).mockResolvedValueOnce(refreshedStatus);
    postCacheClear.mockResolvedValue({ success: true, message: "Cleared" });

    const { result } = renderHook(() => useCacheStatus(true));
    await waitFor(() => expect(result.current.status).toEqual(initialStatus));

    let clearResult;
    await act(async () => { clearResult = await result.current.clearCache(); });

    expect(postCacheClear).toHaveBeenCalledOnce();
    expect(fetchCacheStatus).toHaveBeenCalledTimes(2);
    expect(result.current.status).toEqual(refreshedStatus);
    expect(clearResult).toEqual({ success: true, message: "Cleared" });
  });

  test("returns a failed clear result without refetching", async () => {
    const failedResult = { success: false, message: "Clear failed" };
    fetchCacheStatus.mockResolvedValueOnce(buildStatus(100));
    postCacheClear.mockResolvedValue(failedResult);

    const { result } = renderHook(() => useCacheStatus(true));
    await waitFor(() => expect(result.current.status).not.toBeNull());

    let clearResult;
    await act(async () => { clearResult = await result.current.clearCache(); });

    expect(clearResult).toEqual(failedResult);
    expect(fetchCacheStatus).toHaveBeenCalledTimes(1);
  });
});
