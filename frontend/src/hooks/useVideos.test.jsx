import { describe, test, expect, vi, beforeEach } from "vitest";
import { renderHook, waitFor, act } from "@testing-library/react";
import { useVideos } from "./useVideos";
import { fetchVideos } from "../api/client";

vi.mock("../api/client", () => ({
  fetchVideos: vi.fn(),
}));

function buildHttpError(status) {
  const error = new Error(`HTTP ${status}`);
  error.status = status;
  return error;
}

beforeEach(() => {
  fetchVideos.mockReset();
});

describe("useVideos", () => {
  test("sets videos and loading=false on successful fetch", async () => {
    const videos = [{ id: 1 }, { id: 2 }];
    fetchVideos.mockResolvedValue(videos);

    const { result } = renderHook(() => useVideos());

    expect(result.current.loading).toBe(true);
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.videos).toEqual(videos);
    expect(result.current.error).toBeNull();
    expect(result.current.unauthorized).toBe(false);
  });

  test("sets unauthorized=true and NOT a generic error when fetch rejects with .status 401", async () => {
    fetchVideos.mockRejectedValue(buildHttpError(401));

    const { result } = renderHook(() => useVideos());

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.unauthorized).toBe(true);
    expect(result.current.error).toBeNull();
  });

  test("sets error message and unauthorized stays false on a non-401 failure", async () => {
    fetchVideos.mockRejectedValue(buildHttpError(500));

    const { result } = renderHook(() => useVideos());

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.error).toBe("HTTP 500");
    expect(result.current.unauthorized).toBe(false);
  });

  test("refetch() triggers a new fetch and recovers after a prior error", async () => {
    const videos = [{ id: 3 }];
    fetchVideos.mockRejectedValueOnce(buildHttpError(500)).mockResolvedValueOnce(videos);

    const { result } = renderHook(() => useVideos());
    await waitFor(() => expect(result.current.error).toBe("HTTP 500"));

    act(() => result.current.refetch());

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(fetchVideos).toHaveBeenCalledTimes(2);
    expect(result.current.videos).toEqual(videos);
    expect(result.current.error).toBeNull();
    expect(result.current.unauthorized).toBe(false);
  });
});

function buildPage(startId, count) {
  const page = [];
  for (let i = 0; i < count; i++) page.push({ id: startId - i });
  return page;
}

describe("useVideos pagination", () => {
  test("hasMore is true after a full first page, false after a short one", async () => {
    fetchVideos.mockResolvedValue(buildPage(100, 50));
    const { result } = renderHook(() => useVideos(50));
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.hasMore).toBe(true);

    fetchVideos.mockResolvedValue(buildPage(100, 10));
    const short = renderHook(() => useVideos(50));
    await waitFor(() => expect(short.result.current.loading).toBe(false));
    expect(short.result.current.hasMore).toBe(false);
  });

  test("loadMore fetches with the last id as beforeId and appends", async () => {
    fetchVideos.mockResolvedValueOnce(buildPage(100, 50)).mockResolvedValueOnce(buildPage(50, 50));
    const { result } = renderHook(() => useVideos(50));
    await waitFor(() => expect(result.current.loading).toBe(false));

    await act(() => result.current.loadMore());

    expect(fetchVideos).toHaveBeenLastCalledWith(50, 51);
    expect(result.current.videos).toHaveLength(100);
    expect(result.current.hasMore).toBe(true);
  });

  test("a short page flips hasMore to false and further loadMore calls do not fetch", async () => {
    fetchVideos.mockResolvedValueOnce(buildPage(100, 50)).mockResolvedValueOnce(buildPage(50, 3));
    const { result } = renderHook(() => useVideos(50));
    await waitFor(() => expect(result.current.loading).toBe(false));

    await act(() => result.current.loadMore());
    expect(result.current.hasMore).toBe(false);

    await act(() => result.current.loadMore());
    expect(fetchVideos).toHaveBeenCalledTimes(2);
  });

  test("overlapping loadMore calls fetch only once", async () => {
    let release;
    const gate = new Promise((resolve) => { release = resolve; });
    fetchVideos.mockResolvedValueOnce(buildPage(100, 50)).mockImplementationOnce(async () => {
      await gate;
      return buildPage(50, 50);
    });
    const { result } = renderHook(() => useVideos(50));
    await waitFor(() => expect(result.current.loading).toBe(false));

    let first;
    act(() => { first = result.current.loadMore(); result.current.loadMore(); });
    release();
    await act(() => first);

    expect(fetchVideos).toHaveBeenCalledTimes(2);
  });

  test("loadMore failure with 401 sets unauthorized", async () => {
    fetchVideos.mockResolvedValueOnce(buildPage(100, 50)).mockRejectedValueOnce(buildHttpError(401));
    const { result } = renderHook(() => useVideos(50));
    await waitFor(() => expect(result.current.loading).toBe(false));

    await act(() => result.current.loadMore());

    expect(result.current.unauthorized).toBe(true);
  });
});
