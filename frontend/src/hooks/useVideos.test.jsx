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
    fetchVideos.mockResolvedValue({ videos, total: 2 });

    const { result } = renderHook(() => useVideos());

    expect(result.current.loading).toBe(true);
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.videos).toEqual(videos);
    expect(result.current.total).toBe(2);
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
    fetchVideos.mockRejectedValueOnce(buildHttpError(500)).mockResolvedValueOnce({ videos, total: 1 });

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
    fetchVideos.mockResolvedValue({ videos: buildPage(100, 50), total: null });
    const { result } = renderHook(() => useVideos(50));
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.hasMore).toBe(true);

    fetchVideos.mockResolvedValue({ videos: buildPage(100, 10), total: null });
    const short = renderHook(() => useVideos(50));
    await waitFor(() => expect(short.result.current.loading).toBe(false));
    expect(short.result.current.hasMore).toBe(false);
  });

  test("loadMore fetches with the last id as beforeId and appends", async () => {
    fetchVideos.mockResolvedValueOnce({ videos: buildPage(100, 50), total: null })
      .mockResolvedValueOnce({ videos: buildPage(50, 50), total: null });
    const { result } = renderHook(() => useVideos(50));
    await waitFor(() => expect(result.current.loading).toBe(false));

    await act(() => result.current.loadMore());

    expect(fetchVideos).toHaveBeenLastCalledWith({ limit: 50, beforeId: 51 });
    expect(result.current.videos).toHaveLength(100);
    expect(result.current.hasMore).toBe(true);
  });

  test("a short page flips hasMore to false and further loadMore calls do not fetch", async () => {
    fetchVideos.mockResolvedValueOnce({ videos: buildPage(100, 50), total: null })
      .mockResolvedValueOnce({ videos: buildPage(50, 3), total: null });
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
    fetchVideos.mockResolvedValueOnce({ videos: buildPage(100, 50), total: null }).mockImplementationOnce(async () => {
      await gate;
      return { videos: buildPage(50, 50), total: null };
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
    fetchVideos.mockResolvedValueOnce({ videos: buildPage(100, 50), total: null })
      .mockRejectedValueOnce(buildHttpError(401));
    const { result } = renderHook(() => useVideos(50));
    await waitFor(() => expect(result.current.loading).toBe(false));

    await act(() => result.current.loadMore());

    expect(result.current.unauthorized).toBe(true);
  });

  test("jumpTo replaces videos and loadMore resumes with the last id without offset", async () => {
    fetchVideos.mockResolvedValueOnce({ videos: buildPage(100, 50), total: 300 })
      .mockResolvedValueOnce({ videos: buildPage(200, 50), total: 300 })
      .mockResolvedValueOnce({ videos: buildPage(150, 50), total: 300 });
    const { result } = renderHook(() => useVideos(50));
    await waitFor(() => expect(result.current.loading).toBe(false));

    await act(() => result.current.jumpTo(100));
    expect(result.current.videos[0].id).toBe(200);
    expect(result.current.total).toBe(300);

    await act(() => result.current.loadMore());
    expect(fetchVideos).toHaveBeenNthCalledWith(2, { limit: 50, offset: 100 });
    expect(fetchVideos).toHaveBeenNthCalledWith(3, { limit: 50, beforeId: 151 });
    expect(result.current.videos).toHaveLength(100);
  });

  test("a stale loadMore response cannot append after a newer jump", async () => {
    let releaseLoadMore;
    const stalePage = new Promise((resolve) => { releaseLoadMore = resolve; });
    fetchVideos.mockResolvedValueOnce({ videos: buildPage(100, 50), total: null })
      .mockReturnValueOnce(stalePage)
      .mockResolvedValueOnce({ videos: buildPage(500, 10), total: 510 });
    const { result } = renderHook(() => useVideos(50));
    await waitFor(() => expect(result.current.loading).toBe(false));

    let loadMoreRequest;
    act(() => { loadMoreRequest = result.current.loadMore(); });
    await act(() => result.current.jumpTo(500));
    releaseLoadMore({ videos: buildPage(50, 50), total: null });
    await act(() => loadMoreRequest);

    expect(result.current.videos).toEqual(buildPage(500, 10));
  });
});

describe("useVideos search mode", () => {
  test("initial fetch sends search without category or before_id", async () => {
    fetchVideos.mockResolvedValue({ videos: buildPage(100, 3), total: 3 });

    renderHook(() => useVideos(50, "kink", "sunset"));

    await waitFor(() => expect(fetchVideos).toHaveBeenCalledWith({ limit: 50, search: "sunset" }));
  });

  test("loadMore pages by offset (falls back to sentOffset + limit when next_offset is absent)", async () => {
    fetchVideos.mockResolvedValueOnce({ videos: buildPage(100, 50), total: 100 })
      .mockResolvedValueOnce({ videos: buildPage(50, 20), total: 100 });
    const { result } = renderHook(() => useVideos(50, null, "sunset"));
    await waitFor(() => expect(result.current.loading).toBe(false));

    await act(() => result.current.loadMore());

    expect(fetchVideos).toHaveBeenLastCalledWith({ limit: 50, offset: 50, search: "sunset" });
    expect(result.current.videos).toHaveLength(70);
  });

  test("changing the search term resets the list and refetches, like changing category", async () => {
    fetchVideos.mockResolvedValueOnce({ videos: buildPage(100, 50), total: null })
      .mockResolvedValueOnce({ videos: buildPage(200, 5), total: 5 });
    const { result, rerender } = renderHook(({ search }) => useVideos(50, null, search), {
      initialProps: { search: "sunset" },
    });
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.videos).toHaveLength(50);

    rerender({ search: "sunrise" });

    expect(result.current.loading).toBe(true);
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(fetchVideos).toHaveBeenLastCalledWith({ limit: 50, search: "sunrise" });
    expect(result.current.videos).toEqual(buildPage(200, 5));
  });

  test("an empty search restores today's behavior exactly: no search param, loadMore uses beforeId", async () => {
    fetchVideos.mockResolvedValueOnce({ videos: buildPage(100, 50), total: null })
      .mockResolvedValueOnce({ videos: buildPage(50, 50), total: null });
    const { result } = renderHook(() => useVideos(50, "kink", ""));
    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(fetchVideos).toHaveBeenCalledWith({ limit: 50, category: "kink" });

    await act(() => result.current.loadMore());

    expect(fetchVideos).toHaveBeenLastCalledWith({ limit: 50, category: "kink", beforeId: 51 });
  });

  test("search cursor uses the server's next_offset, not videos.length, when Telegram drops ids", async () => {
    fetchVideos.mockResolvedValueOnce({ videos: buildPage(100, 47), total: 100, next_offset: 50 })
      .mockResolvedValueOnce({ videos: buildPage(50, 50), total: 100, next_offset: 100 });
    const { result } = renderHook(() => useVideos(50, null, "sunset"));
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.videos).toHaveLength(47);

    await act(() => result.current.loadMore());

    expect(fetchVideos).toHaveBeenLastCalledWith({ limit: 50, offset: 50, search: "sunset" });
    expect(result.current.videos).toHaveLength(97);
  });

  test("an all-dropped page still advances the cursor so the next loadMore does not repeat it", async () => {
    fetchVideos.mockResolvedValueOnce({ videos: buildPage(100, 50), total: 200, next_offset: 50 })
      .mockResolvedValueOnce({ videos: [], total: 200, next_offset: 100 })
      .mockResolvedValueOnce({ videos: buildPage(50, 20), total: 200, next_offset: 120 });
    const { result } = renderHook(() => useVideos(50, null, "sunset"));
    await waitFor(() => expect(result.current.loading).toBe(false));

    await act(() => result.current.loadMore());
    expect(fetchVideos).toHaveBeenLastCalledWith({ limit: 50, offset: 50, search: "sunset" });
    expect(result.current.hasMore).toBe(true);
    expect(result.current.videos).toHaveLength(50);

    await act(() => result.current.loadMore());
    expect(fetchVideos).toHaveBeenLastCalledWith({ limit: 50, offset: 100, search: "sunset" });
    expect(result.current.videos).toHaveLength(70);
  });

  test("hasMore is false once next_offset reaches total, even though dropped ids leave loadedCount short of total", async () => {
    // 4 ids drop out of page 1 (47 loaded, next_offset 50) and 4 more drop out of page
    // 2 (46 loaded, next_offset 98 == total). The old loadedCount-based formula computed
    // startOffset.current(0) + loadedCount(93) < total(98) => true, wrongly reporting more
    // results. The next_offset-based formula correctly reports false since the cursor has
    // reached total.
    fetchVideos.mockResolvedValueOnce({ videos: buildPage(100, 47), total: 98, next_offset: 50 })
      .mockResolvedValueOnce({ videos: buildPage(50, 46), total: 98, next_offset: 98 });
    const { result } = renderHook(() => useVideos(50, null, "sunset"));
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.hasMore).toBe(true);

    await act(() => result.current.loadMore());

    expect(result.current.videos).toHaveLength(93);
    expect(result.current.hasMore).toBe(false);
  });

  test("hasMore is false when next_offset does not advance past the sent offset", async () => {
    fetchVideos.mockResolvedValueOnce({ videos: buildPage(100, 50), total: 200, next_offset: 50 })
      .mockResolvedValueOnce({ videos: [], total: 200, next_offset: 50 });
    const { result } = renderHook(() => useVideos(50, null, "sunset"));
    await waitFor(() => expect(result.current.loading).toBe(false));

    await act(() => result.current.loadMore());

    expect(result.current.hasMore).toBe(false);
  });

  test("a response repeating an already-loaded id does not append a duplicate row", async () => {
    fetchVideos.mockResolvedValueOnce({ videos: buildPage(100, 50), total: 200, next_offset: 50 })
      .mockResolvedValueOnce({ videos: [{ id: 51 }, ...buildPage(50, 10)], total: 200, next_offset: 60 });
    const { result } = renderHook(() => useVideos(50, null, "sunset"));
    await waitFor(() => expect(result.current.loading).toBe(false));

    await act(() => result.current.loadMore());

    expect(result.current.videos).toEqual([...buildPage(100, 50), ...buildPage(50, 10)]);
  });

  test("jumpTo in search mode sets the cursor from next_offset so the following loadMore resumes from there", async () => {
    fetchVideos.mockResolvedValueOnce({ videos: buildPage(100, 50), total: 200, next_offset: 50 })
      .mockResolvedValueOnce({ videos: buildPage(60, 15), total: 200, next_offset: 75 })
      .mockResolvedValueOnce({ videos: buildPage(40, 20), total: 200, next_offset: 100 });
    const { result } = renderHook(() => useVideos(50, null, "sunset"));
    await waitFor(() => expect(result.current.loading).toBe(false));

    await act(() => result.current.jumpTo(60));
    expect(fetchVideos).toHaveBeenNthCalledWith(2, { limit: 50, offset: 60, search: "sunset" });
    expect(result.current.hasMore).toBe(true);

    await act(() => result.current.loadMore());

    expect(fetchVideos).toHaveBeenNthCalledWith(3, { limit: 50, offset: 75, search: "sunset" });
    expect(result.current.hasMore).toBe(true);
  });
});
