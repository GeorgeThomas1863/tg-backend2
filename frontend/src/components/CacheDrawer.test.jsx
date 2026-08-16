import { describe, test, expect, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { CacheDrawer } from "./CacheDrawer";

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
      "cached", "40% paused", "20%", "—", "60%",
    ]);

    rerender(
      <CacheDrawer videos={videos} status={{ ...status, paused: false, active: { msg_id: 3, tier: "pin" } }} speedBps={null} onClose={vi.fn()} />,
    );
    expect(container.querySelectorAll(".cache-drawer-item-state")[2].textContent).toBe("20% ↓");
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

    const inputs = [screen.getByLabelText("Max size (GB)"), screen.getByLabelText("Cache folder")];
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
