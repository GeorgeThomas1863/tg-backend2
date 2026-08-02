import { describe, test, expect, vi } from "vitest";
import { fireEvent, render } from "@testing-library/react";
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

  test("renders pin and prewarm active labels with names and optional speed", () => {
    const pinStatus = buildStatus({ active: { msg_id: 3, tier: "pin" }, videos: { "3": 25 } });
    const { container, rerender } = render(
      <CacheDrawer videos={videos} status={pinStatus} speedBps={2 * 1024 * 1024} onClose={vi.fn()} />,
    );
    expect(container.querySelector(".cache-drawer-active").textContent).toBe(
      "Downloading downloading.mp425% · ≈ 2.0 MB/s · finishing current video",
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
});
