import { describe, test, expect, vi } from "vitest";
import { render, fireEvent, act } from "@testing-library/react";
import { VideoRow } from "./VideoRow";

// No api-client mock: streamUrl/thumbUrl read the VITE_API_BASE pinned in
// vitest.config.js, and jsdom never actually loads <img>/<video> sources.
const video = {
  id: 7,
  name: "clip.mp4",
  date: "2024-03-15T12:34:56+00:00",
  duration: 754,
  size: 10485760,
};

describe("VideoRow", () => {
  test("does NOT mount VideoPlayer when collapsed and mounts it when expanded — only one stream open at a time", () => {
    const { container, rerender } = render(<VideoRow video={video} isExpanded={false} onToggle={vi.fn()} />);

    // Collapsed: no <video> element may exist, or an idle Telegram stream stays open.
    expect(container.querySelector("video")).toBeNull();

    rerender(<VideoRow video={video} isExpanded={true} onToggle={vi.fn()} />);

    const player = container.querySelector("video");
    expect(player).not.toBeNull();
    expect(player.getAttribute("src")).toBe("http://test-api/stream/7");

    // Collapsing again must unmount the player, closing its stream.
    rerender(<VideoRow video={video} isExpanded={false} onToggle={vi.fn()} />);
    expect(container.querySelector("video")).toBeNull();
  });

  test("attaches rowRef to the row root so App can observe visibility", () => {
    const rowRef = vi.fn();
    const { container } = render(
      <VideoRow video={video} isExpanded={false} onToggle={vi.fn()} rowRef={rowRef} />,
    );

    expect(rowRef).toHaveBeenCalledWith(container.querySelector(".video-row"));
  });

  test("shows no cache progress with default props", () => {
    const { container, getByText } = render(<VideoRow video={video} isExpanded={false} onToggle={vi.fn()} />);

    expect(getByText("—").className).toBe("cache-strip-label");
    expect(container.querySelector(".cache-strip-fill")).toBeNull();
  });

  test("shows partial cache progress", () => {
    const { container, getByText } = render(
      <VideoRow video={video} isExpanded={false} onToggle={vi.fn()} cachedBytes={video.size * 0.42} />,
    );

    expect(getByText("42%").className).toBe("cache-strip-label");
    expect(container.querySelector(".cache-strip-fill").style.width).toBe("42%");
  });

  test("shows active download progress", () => {
    const { container, getByText } = render(
      <VideoRow video={video} isExpanded={false} onToggle={vi.fn()} cachedBytes={video.size / 4} isDownloading />,
    );

    expect(getByText("25% ↓").className).toBe("cache-strip-label");
    expect(container.querySelector(".cache-strip-fill").classList.contains("downloading")).toBe(true);
  });

  test("shows paused download progress without the downloading class", () => {
    const { container, getByText } = render(
      <VideoRow
        video={video}
        isExpanded={false}
        onToggle={vi.fn()}
        cachedBytes={video.size / 2}
        isDownloading
        paused
      />,
    );

    expect(getByText("50% paused").className).toBe("cache-strip-label");
    expect(container.querySelector(".cache-strip-fill").classList.contains("downloading")).toBe(false);
  });

  test("shows fully cached progress without the downloading class", () => {
    const { container, getByText } = render(
      <VideoRow video={video} isExpanded={false} onToggle={vi.fn()} cachedBytes={video.size} isDownloading />,
    );

    expect(getByText("cached").className).toBe("cache-strip-label");
    expect(container.querySelector(".cache-strip-fill").style.width).toBe("100%");
    expect(container.querySelector(".cache-strip-fill").classList.contains("downloading")).toBe(false);
  });

  // NOTE: React synthesizes onMouseEnter/onMouseLeave from mouseover/mouseout,
  // so fire mouseOver/mouseOut here. If the handler does not trigger, swap to
  // fireEvent.mouseEnter/mouseLeave — one of the two fires it under jsdom.
  test("hovering the header for 300ms swaps the thumb for a muted preview stream", () => {
    vi.useFakeTimers();
    const { container } = render(<VideoRow video={video} isExpanded={false} onToggle={vi.fn()} />);

    fireEvent.mouseOver(container.querySelector(".row-header"));
    act(() => vi.advanceTimersByTime(300));

    const preview = container.querySelector("video.row-thumb");
    expect(preview).not.toBeNull();
    expect(preview.getAttribute("src")).toBe("http://test-api/stream/7?preview=1#t=188");
    expect(preview.muted).toBe(true);
    expect(container.querySelector("img.row-thumb")).toBeNull();
    vi.useRealTimers();
  });

  test("unhovering tears the preview down and restores the img", () => {
    vi.useFakeTimers();
    const { container } = render(<VideoRow video={video} isExpanded={false} onToggle={vi.fn()} />);

    fireEvent.mouseOver(container.querySelector(".row-header"));
    act(() => vi.advanceTimersByTime(300));
    fireEvent.mouseOut(container.querySelector(".row-header"));

    expect(container.querySelector("video.row-thumb")).toBeNull();
    expect(container.querySelector("img.row-thumb")).not.toBeNull();
    vi.useRealTimers();
  });

  test("expanded row never shows a hover preview (real player owns the stream)", () => {
    vi.useFakeTimers();
    const { container } = render(<VideoRow video={video} isExpanded={true} onToggle={vi.fn()} />);

    fireEvent.mouseOver(container.querySelector(".row-header"));
    act(() => vi.advanceTimersByTime(1000));

    expect(container.querySelector("video.row-thumb")).toBeNull();
    vi.useRealTimers();
  });
});
