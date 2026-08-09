import { describe, test, expect, vi, beforeEach, afterEach } from "vitest";
import {
  activateChannel,
  addChannel,
  fetchCacheStatus,
  fetchChannels,
  fetchVideos,
  fetchTelegramAuthStatus,
  postCachePaused,
  postCacheClear,
  postCacheSettings,
  postLogin,
  postTelegramCode,
  postTelegramLogout,
  postTelegramPassword,
  postTelegramPhone,
  removeChannel,
  setDefaultChannel,
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
    const response = { videos: [{ id: 7, name: "clip.mp4" }], total: 1 };
    fetchMock.mockResolvedValue({ ok: true, json: async () => response });

    await expect(fetchVideos()).resolves.toEqual(response);
  });

  test("sends credentials: 'include' so the session cookie rides along", async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => [] });

    await fetchVideos({ limit: 5 });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, options] = fetchMock.mock.calls[0];
    expect(url).toBe(`${BASE}/api/videos?limit=5`);
    expect(options.credentials).toBe("include");
  });

  test("appends before_id only when provided", async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => [] });

    await fetchVideos({ limit: 50, beforeId: 999 });
    await fetchVideos({ limit: 50 });

    expect(fetchMock.mock.calls[0][0]).toBe(`${BASE}/api/videos?limit=50&before_id=999`);
    expect(fetchMock.mock.calls[1][0]).toBe(`${BASE}/api/videos?limit=50`);
  });

  test("appends offset only when provided", async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({ videos: [], total: 0 }) });

    await fetchVideos({ limit: 25, offset: 100 });

    expect(fetchMock.mock.calls[0][0]).toBe(`${BASE}/api/videos?limit=25&offset=100`);
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

describe("fetchChannels", () => {
  test("requests channels with credentials and returns parsed JSON", async () => {
    const response = { channels: [{ id: "1", channel: "news" }] };
    fetchMock.mockResolvedValue({ ok: true, json: async () => response });

    await expect(fetchChannels()).resolves.toEqual(response);
    expect(fetchMock).toHaveBeenCalledWith(`${BASE}/api/channels`, {
      credentials: "include",
    });
  });

  test("throws an Error carrying .status when the response is not ok", async () => {
    fetchMock.mockResolvedValue({ ok: false, status: 500 });

    const error = await fetchChannels().catch((e) => e);

    expect(error).toBeInstanceOf(Error);
    expect(error.status).toBe(500);
    expect(error.message).toBe("HTTP 500");
  });
});

describe("Telegram auth client", () => {
  test("fetches status with credentials", async () => {
    const response = { authorized: false, user: null, pending_step: "code" };
    fetchMock.mockResolvedValue({ ok: true, json: async () => response });
    await expect(fetchTelegramAuthStatus()).resolves.toEqual(response);
    expect(fetchMock).toHaveBeenCalledWith(`${BASE}/api/telegram/status`, { credentials: "include" });
  });

  test("status throws with the HTTP status", async () => {
    fetchMock.mockResolvedValue({ ok: false, status: 502 });
    const error = await fetchTelegramAuthStatus().catch((caught) => caught);
    expect(error.message).toBe("HTTP 502");
    expect(error.status).toBe(502);
  });

  test.each([
    [postTelegramPhone, "+1555", "/api/telegram/login/start", { phone: "+1555" }],
    [postTelegramCode, "12345", "/api/telegram/login/code", { code: "12345" }],
    [postTelegramPassword, "secret", "/api/telegram/login/password", { password: "secret" }],
  ])("posts Telegram JSON to the fixed endpoint", async (operation, value, path, body) => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({ success: true }) });
    await operation(value);
    expect(fetchMock).toHaveBeenCalledWith(`${BASE}${path}`, { method: "POST", headers: { "Content-Type": "application/json" }, credentials: "include", body: JSON.stringify(body) });
  });

  test("logout posts without a body", async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({ success: true }) });
    await postTelegramLogout();
    expect(fetchMock).toHaveBeenCalledWith(`${BASE}/api/telegram/logout`, { method: "POST", headers: { "Content-Type": "application/json" }, credentials: "include" });
  });

  test("mutations use backend messages and HTTP fallback", async () => {
    fetchMock.mockResolvedValueOnce({ ok: false, status: 429, json: async () => ({ message: "Slow down" }) }).mockResolvedValueOnce({ ok: false, status: 500, json: async () => { throw new Error("bad json"); } });
    await expect(postTelegramCode("1")).resolves.toEqual({ success: false, message: "Slow down" });
    await expect(postTelegramCode("1")).resolves.toEqual({ success: false, message: "HTTP 500" });
  });

  test("mutations convert network rejection to failure", async () => {
    vi.spyOn(console, "log").mockImplementation(() => {});
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));
    await expect(postTelegramLogout()).resolves.toEqual({ success: false, message: "Failed to fetch" });
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

describe("postCacheSettings", () => {
  test("posts only cache_max_gb with credentials and returns parsed JSON", async () => {
    const response = { success: true, message: "Cache size updated" };
    fetchMock.mockResolvedValue({ ok: true, json: async () => response });

    await expect(postCacheSettings({ cache_max_gb: 50 })).resolves.toEqual(response);
    expect(fetchMock).toHaveBeenCalledWith(`${BASE}/api/cache/settings`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({ cache_max_gb: 50 }),
    });
  });

  test("posts only cache_dir when it is the only provided field", async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({ success: true, message: "Updated" }) });

    await postCacheSettings({ cache_dir: "D:\\cache" });

    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ cache_dir: "D:\\cache" });
  });

  test("posts both provided fields", async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({ success: true, message: "Updated" }) });

    await postCacheSettings({ cache_dir: "D:\\cache", cache_max_gb: 50 });

    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({
      cache_dir: "D:\\cache",
      cache_max_gb: 50,
    });
  });

  test("returns the backend message on a non-ok response", async () => {
    fetchMock.mockResolvedValue({
      ok: false,
      status: 400,
      json: async () => ({ message: "Invalid cache directory" }),
    });

    await expect(postCacheSettings({ cache_dir: "relative" })).resolves.toEqual({
      success: false,
      message: "Invalid cache directory",
    });
  });

  test("returns a failure result instead of throwing on a network error", async () => {
    vi.spyOn(console, "log").mockImplementation(() => {});
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));

    await expect(postCacheSettings({ cache_max_gb: 50 })).resolves.toEqual({
      success: false,
      message: "Failed to fetch",
    });
    expect(console.log).toHaveBeenCalledWith("CACHE SETTINGS ERROR: Failed to fetch");
  });
});

describe("postCacheClear", () => {
  test("posts without a body and returns parsed JSON", async () => {
    const response = { success: true, message: "Cache cleared" };
    fetchMock.mockResolvedValue({ ok: true, json: async () => response });

    await expect(postCacheClear()).resolves.toEqual(response);
    expect(fetchMock).toHaveBeenCalledWith(`${BASE}/api/cache/clear`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
    });
  });

  test("returns the backend message on failure", async () => {
    fetchMock.mockResolvedValue({ ok: false, status: 500, json: async () => ({ message: "Clear failed" }) });
    await expect(postCacheClear()).resolves.toEqual({ success: false, message: "Clear failed" });
  });

  test("returns a failure result on a network error", async () => {
    vi.spyOn(console, "log").mockImplementation(() => {});
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));
    await expect(postCacheClear()).resolves.toEqual({ success: false, message: "Failed to fetch" });
    expect(console.log).toHaveBeenCalledWith("CACHE CLEAR ERROR: Failed to fetch");
  });
});

describe("channel mutations", () => {
  test.each([
    ["addChannel", addChannel, "newschannel", `${BASE}/api/channels`, "POST", { channel: "newschannel" }],
    ["setDefaultChannel", setDefaultChannel, "abc", `${BASE}/api/channels/default`, "POST", { id: "abc" }],
    ["activateChannel", activateChannel, "abc", `${BASE}/api/channels/active`, "POST", { id: "abc" }],
  ])("%s sends the expected JSON request", async (_name, operation, input, url, method, body) => {
    const response = { success: true, message: "Done" };
    fetchMock.mockResolvedValue({ ok: true, json: async () => response });

    await expect(operation(input)).resolves.toEqual(response);
    expect(fetchMock).toHaveBeenCalledWith(url, {
      method,
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify(body),
    });
  });

  test("removeChannel sends DELETE without a body", async () => {
    const response = { success: true, message: "Removed" };
    fetchMock.mockResolvedValue({ ok: true, json: async () => response });

    await expect(removeChannel("abc")).resolves.toEqual(response);
    expect(fetchMock).toHaveBeenCalledWith(`${BASE}/api/channels/abc`, {
      method: "DELETE",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
    });
  });

  test("returns the backend message for a failed mutation", async () => {
    fetchMock.mockResolvedValue({
      ok: false,
      status: 409,
      json: async () => ({ message: "Channel is active" }),
    });

    await expect(removeChannel("abc")).resolves.toEqual({
      success: false,
      message: "Channel is active",
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
