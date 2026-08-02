import { describe, test, expect, vi, beforeEach, afterEach } from "vitest";
import {
  fetchCacheStatus,
  fetchVideos,
  postCachePaused,
  postLogin,
  streamUrl,
  thumbUrl,
} from "./client";

// VITE_API_BASE is pinned to "http://test-api" in vitest.config.js (test.env),
// so these assertions never depend on the machine's repo-root .env.
const BASE = "http://test-api";

let fetchMock;

beforeEach(() => {
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("fetchVideos", () => {
  test("throws an Error carrying .status when the response is not ok — useVideos' 401 detection depends on this", async () => {
    fetchMock.mockResolvedValue({ ok: false, status: 401 });

    const error = await fetchVideos().catch((e) => e);

    expect(error).toBeInstanceOf(Error);
    expect(error.status).toBe(401);
    expect(error.message).toBe("HTTP 401");
  });

  test("resolves parsed JSON on ok", async () => {
    const videos = [{ id: 7, name: "clip.mp4" }];
    fetchMock.mockResolvedValue({ ok: true, json: async () => videos });

    await expect(fetchVideos()).resolves.toEqual(videos);
  });

  test("sends credentials: 'include' so the session cookie rides along", async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => [] });

    await fetchVideos(5);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, options] = fetchMock.mock.calls[0];
    expect(url).toBe(`${BASE}/api/videos?limit=5`);
    expect(options.credentials).toBe("include");
  });

  test("appends before_id only when provided", async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => [] });

    await fetchVideos(50, 999);
    await fetchVideos(50);

    expect(fetchMock.mock.calls[0][0]).toBe(`${BASE}/api/videos?limit=50&before_id=999`);
    expect(fetchMock.mock.calls[1][0]).toBe(`${BASE}/api/videos?limit=50`);
  });
});

describe("fetchCacheStatus", () => {
  test("requests cache status with credentials and returns parsed JSON", async () => {
    const status = {
      total_bytes: 128,
      max_bytes: 1024,
      paused: false,
      active: null,
      videos: { "7": 128 },
    };
    fetchMock.mockResolvedValue({ ok: true, json: async () => status });

    await expect(fetchCacheStatus()).resolves.toEqual(status);
    expect(fetchMock).toHaveBeenCalledWith(`${BASE}/api/cache/status`, {
      credentials: "include",
    });
  });

  test("throws an Error carrying .status when the response is not ok", async () => {
    fetchMock.mockResolvedValue({ ok: false, status: 503 });

    const error = await fetchCacheStatus().catch((e) => e);

    expect(error).toBeInstanceOf(Error);
    expect(error.status).toBe(503);
    expect(error.message).toBe("HTTP 503");
  });
});

describe("postLogin", () => {
  test("returns {success:false} WITHOUT calling fetch when pw is empty", async () => {
    const result = await postLogin("");

    expect(result).toEqual({ success: false, message: "No password provided" });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  test("returns {success:false, message:'HTTP <status>'} on a non-ok response instead of throwing", async () => {
    fetchMock.mockResolvedValue({ ok: false, status: 403 });

    await expect(postLogin("hunter2")).resolves.toEqual({ success: false, message: "HTTP 403" });
  });

  test("returns the backend message for a rate-limited response", async () => {
    fetchMock.mockResolvedValue({
      ok: false,
      status: 429,
      json: async () => ({ message: "Too many attempts. Try again later." }),
    });

    await expect(postLogin("hunter2")).resolves.toEqual({
      success: false,
      message: "Too many attempts. Try again later.",
    });
  });

  test("resolves {success:false, message} instead of throwing when fetch rejects (network error)", async () => {
    vi.spyOn(console, "log").mockImplementation(() => {}); // silence the source's console.log
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));

    await expect(postLogin("hunter2")).resolves.toEqual({ success: false, message: "Failed to fetch" });
  });
});

describe("postCachePaused", () => {
  test("posts the paused state with credentials and returns parsed JSON", async () => {
    const response = { success: true, message: "Cache paused" };
    fetchMock.mockResolvedValue({ ok: true, json: async () => response });

    await expect(postCachePaused(true)).resolves.toEqual(response);
    expect(fetchMock).toHaveBeenCalledWith(`${BASE}/api/cache/paused`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({ paused: true }),
    });
  });

  test("returns the server message on a non-ok response", async () => {
    fetchMock.mockResolvedValue({
      ok: false,
      status: 409,
      json: async () => ({ message: "Cache state conflict" }),
    });

    await expect(postCachePaused(false)).resolves.toEqual({
      success: false,
      message: "Cache state conflict",
    });
  });

  test("returns a failure result instead of throwing on a network error", async () => {
    vi.spyOn(console, "log").mockImplementation(() => {});
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));

    await expect(postCachePaused(true)).resolves.toEqual({
      success: false,
      message: "Failed to fetch",
    });
  });
});

describe("URL builders", () => {
  test("streamUrl builds <BASE>/stream/<id> against the pinned VITE_API_BASE", () => {
    expect(streamUrl(42)).toBe(`${BASE}/stream/42`);
  });

  test("thumbUrl builds <BASE>/thumb/<id> against the pinned VITE_API_BASE", () => {
    expect(thumbUrl(42)).toBe(`${BASE}/thumb/42`);
  });
});
