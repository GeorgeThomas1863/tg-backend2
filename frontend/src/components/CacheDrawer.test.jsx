import { describe, test, expect, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { CacheDrawer } from "./CacheDrawer";
import { requestPriorityCache } from "../api/client";

vi.mock("../api/client", () => ({ requestPriorityCache: vi.fn() }));

const videos = [
  { id: 1, name: "cached.mp4", size: 100 },
  { id: 2, name: "paused.mp4", size: 100 },
  { id: 3, name: "downloading.mp4", size: 100 },
  { id: 4, name: "empty.mp4", size: 100 },
  { id: 5, name: "partial.mp4", size: 100 },
];

function buildStatus(overrides = {}) {
  return {
    total_bytes: 50 * 1024 * 1024,
    max_bytes: 200 * 1024 * 1024,
    paused: false,
    active: null,
    videos: {},
    cache_dir: "C:\\cache",
    max_gb: 20,
    tg_connections: 8,
    ...overrides,
  };
}

describe("CacheDrawer", () => {
  test("renders nothing when status is null", () => {
    const { container } = render(<CacheDrawer videos={videos} status={null} speedBps={null} onClose={vi.fn()} />);
    expect(container.firstChild).toBeNull();
  });

  test("renders totals and capped gauge width", () => {
    const status = buildStatus({ total_bytes: 250 * 1024 * 1024 });
    const { container } = render(<CacheDrawer videos={[]} status={status} speedBps={null} onClose={vi.fn()} />);

    expect(container.querySelector(".cache-drawer-total").textContent).toBe("250 MB / 200 MB used");
    expect(container.querySelector(".cache-drawer-gauge-fill").style.width).toBe("100%");
  });

  test("renders pin, visible, and prewarm active labels with names and optional speed", () => {
    const pinStatus = buildStatus({ active: { msg_id: 3, tier: "pin" }, videos: { "3": 25 } });
    const { container, rerender } = render(
      <CacheDrawer videos={videos} status={pinStatus} speedBps={2 * 1024 * 1024} onClose={vi.fn()} />,
    );
    expect(container.querySelector(".cache-drawer-active").textContent).toBe(
      "Downloading downloading.mp425% · ≈ 2.0 MB/s · finishing current video",
    );

    const visibleStatus = buildStatus({ active: { msg_id: 3, tier: "visible" }, videos: { "3": 25 } });
    rerender(<CacheDrawer videos={videos} status={visibleStatus} speedBps={null} onClose={vi.fn()} />);
    expect(container.querySelector(".cache-drawer-active").textContent).toBe(
      "Downloading downloading.mp425% · caching on-screen videos",
    );

    const prewarmStatus = buildStatus({ active: { msg_id: 99, tier: "prewarm" } });
    rerender(<CacheDrawer videos={videos} status={prewarmStatus} speedBps={null} onClose={vi.fn()} />);
    expect(container.querySelector(".cache-drawer-active").textContent).toBe(
      "Downloading video_990% · prewarming library",
    );
    expect(container.querySelector(".cache-drawer-active").textContent).not.toContain("MB/s");
  });

  test("renders a priority active label with no dangling separator", () => {
    const priorityStatus = buildStatus({ active: { msg_id: 3, tier: "priority" }, videos: { "3": 25 } });
    const { container } = render(
      <CacheDrawer videos={videos} status={priorityStatus} speedBps={null} onClose={vi.fn()} />,
    );
    const text = container.querySelector(".cache-drawer-active").textContent;
    expect(text).toBe("Downloading downloading.mp425% · caching requested video");
    expect(text).not.toMatch(/·\s*$/);
  });

  test("renders every active slot and marks a second-slot video as downloading", () => {
    const status = buildStatus({
      active: { msg_id: 2, tier: "pin" },
      active_slots: [{ msg_id: 2, tier: "pin" }, { msg_id: 3, tier: "visible" }],
      videos: { "2": 40, "3": 25 },
    });
    const { container } = render(
      <CacheDrawer videos={videos} status={status} speedBps={null} onClose={vi.fn()} />,
    );

    expect(container.querySelectorAll(".cache-drawer-active-slot")).toHaveLength(2);
    expect(container.querySelector(".cache-drawer-active").textContent).toContain("paused.mp4");
    expect(container.querySelector(".cache-drawer-active").textContent).toContain("downloading.mp4");
    expect(container.querySelectorAll(".cache-drawer-item-state")[2].textContent).toContain("25%");
  });

  test("renders paused and idle active states", () => {
    const { container, rerender } = render(
      <CacheDrawer videos={videos} status={buildStatus({ paused: true })} speedBps={null} onClose={vi.fn()} />,
    );
    expect(container.querySelector(".cache-drawer-active").textContent).toBe("Background caching is paused");

    rerender(<CacheDrawer videos={videos} status={buildStatus()} speedBps={null} onClose={vi.fn()} />);
    expect(container.querySelector(".cache-drawer-active").textContent).toBe("Idle");
  });

  test("renders all five item state labels", () => {
    const status = buildStatus({
      paused: true,
      active: { msg_id: 2, tier: "pin" },
      videos: { "1": 100, "2": 40, "3": 20, "5": 60 },
    });
    const { container, rerender } = render(
      <CacheDrawer videos={videos} status={status} speedBps={null} onClose={vi.fn()} />,
    );
    expect([...container.querySelectorAll(".cache-drawer-item-state")].map((item) => item.textContent)).toEqual([
      "cached", "40% paused", "20%", "↑", "60%",
    ]);

    rerender(
      <CacheDrawer videos={videos} status={{ ...status, paused: false, active: { msg_id: 3, tier: "pin" } }} speedBps={null} onClose={vi.fn()} />,
    );
    expect(container.querySelectorAll(".cache-drawer-item-state")[2].textContent).toBe("20% ↓");
  });

  test("shows a cache-now button for a non-cached video and requests priority caching on click", async () => {
    requestPriorityCache.mockReset();
    requestPriorityCache.mockResolvedValue({ success: true });
    const status = buildStatus({ videos: { "1": 100 } });
    const { container } = render(
      <CacheDrawer videos={videos} status={status} speedBps={null} onClose={vi.fn()} />,
    );

    const items = container.querySelectorAll(".cache-drawer-item");
    const button = items[3].querySelector(".cache-drawer-item-queue");
    expect(button).not.toBeNull();
    expect(button).toBeEnabled();

    fireEvent.click(button);
    await waitFor(() => expect(requestPriorityCache).toHaveBeenCalledWith(4));
    expect(button).toBeDisabled();
  });

  test("re-enables the cache-now button and shows the message on a failed request", async () => {
    requestPriorityCache.mockReset();
    requestPriorityCache.mockResolvedValue({ success: false, message: "No active channel" });
    const status = buildStatus({ videos: { "1": 100 } });
    const { container } = render(
      <CacheDrawer videos={videos} status={status} speedBps={null} onClose={vi.fn()} />,
    );

    const items = container.querySelectorAll(".cache-drawer-item");
    const button = items[3].querySelector(".cache-drawer-item-queue");

    fireEvent.click(button);
    await waitFor(() => expect(button).toBeEnabled());
    expect(await screen.findByRole("alert")).toHaveTextContent("No active channel");
  });

  test("re-enables the cache-now button and shows a message when the request throws", async () => {
    requestPriorityCache.mockReset();
    requestPriorityCache.mockRejectedValue(new Error("network down"));
    const status = buildStatus({ videos: { "1": 100 } });
    const { container } = render(
      <CacheDrawer videos={videos} status={status} speedBps={null} onClose={vi.fn()} />,
    );

    const items = container.querySelectorAll(".cache-drawer-item");
    const button = items[3].querySelector(".cache-drawer-item-queue");

    fireEvent.click(button);
    await waitFor(() => expect(button).toBeEnabled());
    expect(await screen.findByRole("alert")).toHaveTextContent("network down");
  });

  test("does not show a cache-now button for a cached video", () => {
    const status = buildStatus({ videos: { "1": 100 } });
    const { container } = render(
      <CacheDrawer videos={videos} status={status} speedBps={null} onClose={vi.fn()} />,
    );

    const items = container.querySelectorAll(".cache-drawer-item");
    expect(items[0].querySelector(".cache-drawer-item-queue")).toBeNull();
  });

  test("wraps the item rows in a scrollable list container", () => {
    const { container } = render(
      <CacheDrawer videos={videos} status={buildStatus()} speedBps={null} onClose={vi.fn()} />,
    );

    const list = container.querySelector(".cache-drawer-list");
    expect(list).not.toBeNull();
    expect(list.querySelectorAll(".cache-drawer-item")).toHaveLength(videos.length);
  });

  test("fires onClose from the close button", () => {
    const onClose = vi.fn();
    const { container } = render(<CacheDrawer videos={[]} status={buildStatus()} speedBps={null} onClose={onClose} />);

    fireEvent.click(container.querySelector(".cache-drawer-close"));
    expect(onClose).toHaveBeenCalledOnce();
  });

  test("renders settings inputs prefilled from status", () => {
    render(<CacheDrawer videos={[]} status={buildStatus()} speedBps={null} onClose={vi.fn()} onSaveSettings={vi.fn()} />);

    expect(screen.getByLabelText("Max size (GB)")).toHaveValue(20);
    expect(screen.getByLabelText("Cache folder")).toHaveValue("C:\\cache");
    expect(screen.getByLabelText("Telegram connections")).toHaveValue(8);
    expect(screen.getByText("0 disables the parallel download pool. Values above 8 are unused by the current 8-stripe blocks.")).toBeInTheDocument();
  });

  test("saves a valid Telegram connections value", async () => {
    const onSaveSettings = vi.fn().mockResolvedValue({ success: true });
    render(<CacheDrawer videos={[]} status={buildStatus()} speedBps={null} onClose={vi.fn()} onSaveSettings={onSaveSettings} />);

    fireEvent.change(screen.getByLabelText("Telegram connections"), { target: { value: "4" } });
    fireEvent.click(screen.getAllByRole("button", { name: "Save" })[2]);

    await waitFor(() => expect(onSaveSettings).toHaveBeenCalledWith({ tg_connections: 4 }));
  });

  test.each(["", "17", "2.5"])("rejects invalid Telegram connections value %s", async (value) => {
    const onSaveSettings = vi.fn();
    render(<CacheDrawer videos={[]} status={buildStatus()} speedBps={null} onClose={vi.fn()} onSaveSettings={onSaveSettings} />);

    fireEvent.change(screen.getByLabelText("Telegram connections"), { target: { value } });
    fireEvent.click(screen.getAllByRole("button", { name: "Save" })[2]);

    expect(await screen.findByRole("alert")).toHaveTextContent("Telegram connections must be a whole number from 0 to 16.");
    expect(onSaveSettings).not.toHaveBeenCalled();
  });

  test("saves a valid max size without confirmation", async () => {
    const onSaveSettings = vi.fn().mockResolvedValue({ success: true });
    render(<CacheDrawer videos={[]} status={buildStatus()} speedBps={null} onClose={vi.fn()} onSaveSettings={onSaveSettings} />);

    fireEvent.change(screen.getByLabelText("Max size (GB)"), { target: { value: "32" } });
    fireEvent.click(screen.getAllByRole("button", { name: "Save" })[0]);

    await waitFor(() => expect(onSaveSettings).toHaveBeenCalledWith({ cache_max_gb: 32 }));
    expect(screen.queryByText(/wipes the current cache/i)).not.toBeInTheDocument();
  });

  test.each(["0", "garbage", "1e309"])("rejects invalid max size %s", async (value) => {
    const onSaveSettings = vi.fn();
    render(<CacheDrawer videos={[]} status={buildStatus()} speedBps={null} onClose={vi.fn()} onSaveSettings={onSaveSettings} />);

    fireEvent.change(screen.getByLabelText("Max size (GB)"), { target: { value } });
    fireEvent.click(screen.getAllByRole("button", { name: "Save" })[0]);

    expect(await screen.findByRole("alert")).toHaveTextContent("greater than 0");
    expect(onSaveSettings).not.toHaveBeenCalled();
  });

  test("disables both settings rows while a save is pending", async () => {
    let resolveSave;
    const onSaveSettings = vi.fn(() => new Promise((resolve) => {
      resolveSave = resolve;
    }));
    render(<CacheDrawer videos={[]} status={buildStatus()} speedBps={null} onClose={vi.fn()} onSaveSettings={onSaveSettings} />);

    const inputs = [screen.getByLabelText("Max size (GB)"), screen.getByLabelText("Cache folder"), screen.getByLabelText("Telegram connections")];
    const saveButtons = screen.getAllByRole("button", { name: "Save" });
    fireEvent.click(saveButtons[1]);
    const confirmationButtons = [screen.getByRole("button", { name: "Continue" }), screen.getByRole("button", { name: "Cancel" })];
    fireEvent.click(saveButtons[0]);

    await waitFor(() => {
      for (const control of [...inputs, ...saveButtons, ...confirmationButtons]) expect(control).toBeDisabled();
    });

    resolveSave({ success: true });
    await waitFor(() => {
      for (const control of [...inputs, ...saveButtons, ...confirmationButtons]) expect(control).toBeEnabled();
    });
  });

  test("confirms a trimmed folder change and supports cancellation", async () => {
    const onSaveSettings = vi.fn().mockResolvedValue({ success: true });
    render(<CacheDrawer videos={[]} status={buildStatus()} speedBps={null} onClose={vi.fn()} onSaveSettings={onSaveSettings} />);

    fireEvent.change(screen.getByLabelText("Cache folder"), { target: { value: "  D:\\video-cache  " } });
    fireEvent.click(screen.getAllByRole("button", { name: "Save" })[1]);
    expect(screen.getByText(/wipes the current cache/i)).toBeInTheDocument();
    expect(onSaveSettings).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByText(/wipes the current cache/i)).not.toBeInTheDocument();
    expect(onSaveSettings).not.toHaveBeenCalled();

    fireEvent.click(screen.getAllByRole("button", { name: "Save" })[1]);
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    await waitFor(() => expect(onSaveSettings).toHaveBeenCalledWith({ cache_dir: "D:\\video-cache" }));
  });

  test("renders a failed save message inline", async () => {
    const onSaveSettings = vi.fn().mockResolvedValue({ success: false, message: "Folder is unavailable" });
    render(<CacheDrawer videos={[]} status={buildStatus()} speedBps={null} onClose={vi.fn()} onSaveSettings={onSaveSettings} />);

    fireEvent.click(screen.getAllByRole("button", { name: "Save" })[0]);

    expect(await screen.findByRole("alert")).toHaveTextContent("Folder is unavailable");
  });

  test("shows a confirmation before clearing the cache", () => {
    const onClearCache = vi.fn();
    render(<CacheDrawer videos={[]} status={buildStatus()} speedBps={null} onClose={vi.fn()} onSaveSettings={vi.fn()} onClearCache={onClearCache} />);

    fireEvent.click(screen.getByRole("button", { name: "Clear cache" }));

    expect(screen.getByText("Delete all cached data? Videos will re-download as needed.")).toBeInTheDocument();
    expect(onClearCache).not.toHaveBeenCalled();
  });

  test("continues or cancels a clear confirmation", async () => {
    const onClearCache = vi.fn().mockResolvedValue({ success: true });
    render(<CacheDrawer videos={[]} status={buildStatus()} speedBps={null} onClose={vi.fn()} onSaveSettings={vi.fn()} onClearCache={onClearCache} />);

    fireEvent.click(screen.getByRole("button", { name: "Clear cache" }));
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByText(/Delete all cached data/)).not.toBeInTheDocument();
    expect(onClearCache).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Clear cache" }));
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    await waitFor(() => expect(onClearCache).toHaveBeenCalledOnce());
  });

  test("locks all settings controls while clearing", async () => {
    let resolveClear;
    const onClearCache = vi.fn(() => new Promise((resolve) => { resolveClear = resolve; }));
    render(<CacheDrawer videos={[]} status={buildStatus()} speedBps={null} onClose={vi.fn()} onSaveSettings={vi.fn()} onClearCache={onClearCache} />);

    fireEvent.click(screen.getAllByRole("button", { name: "Save" })[1]);
    fireEvent.click(screen.getByRole("button", { name: "Clear cache" }));
    const clearConfirm = screen.getAllByRole("button", { name: "Continue" })[1];
    fireEvent.click(clearConfirm);

    const controls = [
      screen.getByLabelText("Max size (GB)"),
      screen.getByLabelText("Cache folder"),
      screen.getByLabelText("Telegram connections"),
      ...screen.getAllByRole("button", { name: "Save" }),
      screen.getByRole("button", { name: "Clear cache" }),
      ...screen.getAllByRole("button", { name: "Continue" }),
      ...screen.getAllByRole("button", { name: "Cancel" }),
    ];
    await waitFor(() => { for (const control of controls) expect(control).toBeDisabled(); });

    resolveClear({ success: true });
    await waitFor(() => { for (const control of controls) expect(control).toBeEnabled(); });
  });

  test("renders a failed clear message inline", async () => {
    const onClearCache = vi.fn().mockResolvedValue({ success: false, message: "Cache is busy" });
    render(<CacheDrawer videos={[]} status={buildStatus()} speedBps={null} onClose={vi.fn()} onSaveSettings={vi.fn()} onClearCache={onClearCache} />);

    fireEvent.click(screen.getByRole("button", { name: "Clear cache" }));
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Cache is busy");
  });
});
