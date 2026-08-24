import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import App from "./App";
import { useTelegramAuth } from "./hooks/useTelegramAuth";
import { useVideos } from "./hooks/useVideos";
import { useCategories } from "./hooks/useCategories";
import { useCacheStatus } from "./hooks/useCacheStatus";

vi.mock("./hooks/useTelegramAuth", () => ({ useTelegramAuth: vi.fn() }));
vi.mock("./hooks/useChannels", () => ({ useChannels: () => ({ channels: [{ id: "1" }], active: { id: "1", title: "Clips" }, loading: false, busy: false, error: null, refresh: vi.fn(), activate: vi.fn() }) }));
vi.mock("./hooks/useVideos", () => ({ useVideos: vi.fn() }));
vi.mock("./hooks/useCategories", () => ({ useCategories: vi.fn() }));
vi.mock("./hooks/useCacheStatus", async (importOriginal) => ({
  ...(await importOriginal()),
  useCacheStatus: vi.fn(),
}));
vi.mock("./hooks/useSentinel", () => ({ useSentinel: () => vi.fn() }));
vi.mock("./hooks/useVisibleVideos", () => ({ useVisibleVideos: () => () => vi.fn() }));
vi.mock("./components/PasswordGate", () => ({ PasswordGate: () => <div>Site password gate</div> }));

const telegramBase = { loading: false, mutating: false, busy: false, error: null, refresh: vi.fn(), sendCode: vi.fn(), submitCode: vi.fn(), submitPassword: vi.fn(), logout: vi.fn() };

beforeEach(() => {
  useTelegramAuth.mockReset();
  useVideos.mockReset();
  useCategories.mockReset();
  useCacheStatus.mockReset();
  useCacheStatus.mockReturnValue({ status: null });
  useVideos.mockReturnValue({ videos: [], total: 0, loading: false, loadingMore: false, error: null, unauthorized: false, refetch: vi.fn(), jumpTo: vi.fn(), loadMore: vi.fn() });
  useCategories.mockReturnValue({ categories: [], channel: null, loading: false });
});

describe("App Telegram gate", () => {
  test("shows the header trigger and logged-out state without fetching videos", () => {
    useTelegramAuth.mockReturnValue({ ...telegramBase, status: { authorized: false, user: null, pending_step: null } });
    render(<App />);
    expect(screen.getByRole("button", { name: /Telegram · logged out/ })).toBeInTheDocument();
    expect(screen.getByText("Telegram is logged out. Log in to load and stream videos.")).toBeInTheDocument();
    expect(useVideos).not.toHaveBeenCalled();
  });

  test("opens and closes the drawer from the header and login CTA", () => {
    useTelegramAuth.mockReturnValue({ ...telegramBase, status: { authorized: false, user: null, pending_step: null } });
    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: "Log in" }));
    expect(screen.getByRole("complementary", { name: "Telegram account" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Close Telegram account" }));
    expect(screen.queryByRole("complementary", { name: "Telegram account" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Telegram · logged out/ }));
    expect(screen.getByRole("complementary", { name: "Telegram account" })).toBeInTheDocument();
  });

  test("mounts the library when Telegram is authorized", () => {
    useTelegramAuth.mockReturnValue({ ...telegramBase, status: { authorized: true, user: { username: "alice" }, pending_step: null } });
    render(<App />);
    expect(screen.getByRole("button", { name: /Telegram · alice/ })).toBeInTheDocument();
    expect(useVideos).toHaveBeenCalledOnce();
  });

  test("keeps site 401 distinct by rendering PasswordGate", () => {
    useTelegramAuth.mockReturnValue({ ...telegramBase, status: null, error: "HTTP 401" });
    render(<App />);
    expect(screen.getByText("Site password gate")).toBeInTheDocument();
    expect(screen.queryByText(/Telegram is logged out/)).not.toBeInTheDocument();
    expect(useVideos).not.toHaveBeenCalled();
  });

  test("renders a status error instead of logged out after a transport failure", () => {
    useTelegramAuth.mockReturnValue({ ...telegramBase, status: null, error: "HTTP 502" });
    render(<App />);
    expect(screen.getByText("Error loading Telegram status: HTTP 502")).toBeInTheDocument();
    expect(screen.queryByText(/Telegram is logged out/)).not.toBeInTheDocument();
    expect(useVideos).not.toHaveBeenCalled();
  });
});

describe("App search input", () => {
  const authorizedStatus = { authorized: true, user: { username: "alice" }, pending_step: null };

  afterEach(() => vi.useRealTimers());

  test("typing debounces 300ms before it reaches useVideos as the search term", () => {
    vi.useFakeTimers();
    useTelegramAuth.mockReturnValue({ ...telegramBase, status: authorizedStatus });
    render(<App />);

    const search = screen.getByRole("searchbox", { name: "Search videos" });
    fireEvent.change(search, { target: { value: "sunset" } });

    expect(useVideos).toHaveBeenLastCalledWith(50, null, "", "asc");

    act(() => vi.advanceTimersByTime(299));
    expect(useVideos).toHaveBeenLastCalledWith(50, null, "", "asc");

    act(() => vi.advanceTimersByTime(1));
    expect(useVideos).toHaveBeenLastCalledWith(50, null, "sunset", "asc");
  });

  test("the clear button resets the search immediately, without waiting for the debounce", () => {
    vi.useFakeTimers();
    useTelegramAuth.mockReturnValue({ ...telegramBase, status: authorizedStatus });
    render(<App />);

    const search = screen.getByRole("searchbox", { name: "Search videos" });
    fireEvent.change(search, { target: { value: "sunset" } });
    act(() => vi.advanceTimersByTime(300));
    expect(useVideos).toHaveBeenLastCalledWith(50, null, "sunset", "asc");

    fireEvent.click(screen.getByRole("button", { name: "Clear search" }));

    expect(search.value).toBe("");
    expect(useVideos).toHaveBeenLastCalledWith(50, null, "", "asc");
  });
});

describe("App sort control", () => {
  const authorizedStatus = { authorized: true, user: { username: "alice" }, pending_step: null };

  afterEach(() => vi.useRealTimers());

  test("defaults to oldest first on first render", () => {
    useTelegramAuth.mockReturnValue({ ...telegramBase, status: authorizedStatus });
    render(<App />);

    expect(useVideos).toHaveBeenLastCalledWith(50, null, "", "asc");
    expect(screen.getByRole("button", { name: "Oldest first" })).toHaveAttribute("aria-pressed", "true");
  });

  test("changing sort direction causes a refetch with the new direction", () => {
    useTelegramAuth.mockReturnValue({ ...telegramBase, status: authorizedStatus });
    render(<App />);

    fireEvent.click(screen.getByRole("button", { name: "Newest first" }));

    expect(useVideos).toHaveBeenLastCalledWith(50, null, "", "desc");
  });

  test("keeps the selected category applied when the sort direction changes", () => {
    useTelegramAuth.mockReturnValue({ ...telegramBase, status: authorizedStatus });
    useCategories.mockReturnValue({
      categories: [{ key: "sports", name: "Sports", count: 5, subs: [] }],
      channel: null,
      loading: false,
    });
    render(<App />);

    const chronoSection = screen.getByRole("region", { name: "Video category filter" });
    fireEvent.click(within(chronoSection).getByRole("button", { name: /All videos/ }));
    fireEvent.click(within(chronoSection).getByText("Sports").closest("button"));
    expect(useVideos).toHaveBeenLastCalledWith(50, "sports", "", "asc");

    fireEvent.click(screen.getByRole("button", { name: "Newest first" }));

    expect(useVideos).toHaveBeenLastCalledWith(50, "sports", "", "desc");
  });

  test("disables the sort control while a search term is active", () => {
    vi.useFakeTimers();
    useTelegramAuth.mockReturnValue({ ...telegramBase, status: authorizedStatus });
    render(<App />);

    const search = screen.getByRole("searchbox", { name: "Search videos" });
    fireEvent.change(search, { target: { value: "sunset" } });
    act(() => vi.advanceTimersByTime(300));

    expect(screen.getByRole("button", { name: "Oldest first" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Newest first" })).toBeDisabled();
  });
});

describe("App cache status", () => {
  const loadedCacheStatus = {
    paused: false,
    total_bytes: 0,
    max_bytes: 200 * 1024 * 1024,
    videos: {},
    cache_dir: "C:\\cache",
    max_gb: 20,
    tg_connections: 8,
  };

  test("shows the cache drawer by default once cache status has loaded", () => {
    useTelegramAuth.mockReturnValue({ ...telegramBase, status: { authorized: true, user: { username: "alice" }, pending_step: null } });
    useCacheStatus.mockReturnValue({ status: loadedCacheStatus });

    const { container } = render(<App />);

    expect(container.querySelector(".cache-drawer")).not.toBeNull();
    expect(document.body.classList.contains("cache-panel-open")).toBe(true);
  });

  test("closing the cache drawer hides it and lifts the content-shift class", () => {
    useTelegramAuth.mockReturnValue({ ...telegramBase, status: { authorized: true, user: { username: "alice" }, pending_step: null } });
    useCacheStatus.mockReturnValue({ status: loadedCacheStatus });

    const { container } = render(<App />);
    fireEvent.click(container.querySelector(".cache-drawer-close"));

    expect(container.querySelector(".cache-drawer")).toBeNull();
    expect(document.body.classList.contains("cache-panel-open")).toBe(false);
  });

  test("hides the 429 badge when flood status is omitted", () => {
    useTelegramAuth.mockReturnValue({ ...telegramBase, status: { authorized: true, user: { username: "alice" } } });
    useCacheStatus.mockReturnValue({ status: { paused: false, total_bytes: 0, max_bytes: 0, videos: {} } });

    render(<App />);

    expect(screen.queryByText(/429 ×/)).not.toBeInTheDocument();
  });

  test("shows the persistent 429 badge with count and age", () => {
    useTelegramAuth.mockReturnValue({ ...telegramBase, status: { authorized: true, user: { username: "alice" } } });
    useCacheStatus.mockReturnValue({
      status: { paused: false, total_bytes: 0, max_bytes: 0, videos: {}, flood: { count: 2, last_seconds_ago: 65 } },
      togglePaused: vi.fn(),
    });

    render(<App />);

    expect(screen.getByText("429 ×2 · 1m ago")).toBeInTheDocument();
  });

  test("marks a video in the second active slot as downloading", () => {
    useTelegramAuth.mockReturnValue({
      ...telegramBase,
      status: { authorized: true, user: { username: "alice" }, pending_step: null },
    });
    useVideos.mockReturnValue({
      videos: [{ id: 7, name: "clip.mp4", date: "2024-03-15T12:34:56Z", duration: 60, size: 100 }],
      total: 1,
      loading: false,
      loadingMore: false,
      error: null,
      unauthorized: false,
      refetch: vi.fn(),
      jumpTo: vi.fn(),
      loadMore: vi.fn(),
    });
    useCacheStatus.mockReturnValue({
      status: {
        paused: false,
        active: { msg_id: 3, tier: "pin" },
        active_slots: [{ msg_id: 3, tier: "pin" }, { msg_id: 7, tier: "visible" }],
        videos: { "7": 25 },
      },
    });

    const { container } = render(<App />);

    // The cache drawer is open by default and now also echoes "25% ↓" for
    // this video in its own item list, so scope to the row's cache strip
    // (the thing this test is actually about) instead of the ambiguous text.
    expect(container.querySelector(".cache-strip-label").textContent).toBe("25% ↓");
  });
});

describe("App category filters", () => {
  const authorizedStatus = { authorized: true, user: { username: "alice" }, pending_step: null };

  test("renders both the chronological and alphabetical dropdowns, and selecting in the alphabetical one drives the same selection the chronological one reflects", () => {
    useTelegramAuth.mockReturnValue({ ...telegramBase, status: authorizedStatus });
    useCategories.mockReturnValue({
      categories: [
        { key: "sports", name: "Sports", count: 5, subs: [] },
        { key: "anime", name: "Anime", count: 8, subs: [] },
      ],
      channel: null,
      loading: false,
    });
    render(<App />);

    const chronoSection = screen.getByRole("region", { name: "Video category filter" });
    const alphaSection = screen.getByRole("region", { name: "Video category filter, alphabetical" });
    expect(chronoSection).toBeInTheDocument();
    expect(alphaSection).toBeInTheDocument();

    fireEvent.click(within(alphaSection).getByRole("button", { name: /All videos/ }));
    fireEvent.click(within(alphaSection).getByRole("option", { name: /Anime/ }));

    expect(within(alphaSection).getByRole("button", { name: /Anime/ })).toBeInTheDocument();
    expect(within(chronoSection).getByRole("button", { name: /Anime/ })).toBeInTheDocument();
    expect(useVideos).toHaveBeenLastCalledWith(50, "anime", "", "asc");
  });
});
