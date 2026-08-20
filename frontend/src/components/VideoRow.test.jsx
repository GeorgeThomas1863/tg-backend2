import { describe, test, expect, vi } from "vitest";
import { render, fireEvent, act } from "@testing-library/react";
import { VideoRow } from "./VideoRow";
import * as hoverPreviewModule from "../hooks/useHoverPreview";

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
  //
  // The popup portals to document.body (not container), so it's queried off
  // document.body directly — that's also what the portal-vs-container split
  // proves: the thumb img never leaves the row.
  test("hovering the header for 300ms opens a popup preview video in document.body", () => {
    vi.useFakeTimers();
    const { container } = render(<VideoRow video={video} isExpanded={false} onToggle={vi.fn()} />);

    fireEvent.mouseOver(container.querySelector(".row-header"));
    act(() => vi.advanceTimersByTime(300));

    const preview = document.body.querySelector(".preview-popup video");
    expect(preview).not.toBeNull();
    expect(preview.getAttribute("src")).toBe("http://test-api/stream/7?preview=1#t=188");
    expect(preview.muted).toBe(true);
    expect(container.querySelector("img.row-thumb")).not.toBeNull();
    vi.useRealTimers();
  });

  // Regression test: at narrow viewports the popup's rendered width shrinks
  // (index.css clamps to min(360px, 100vw - 24px)), so the position math must
  // shrink its width assumption too or the left clamp's bounds invert and
  // push the popup off the left edge (see computePopupPosition).
  test("narrow viewport keeps the popup's left position on-screen", () => {
    const originalInnerWidth = window.innerWidth;
    Object.defineProperty(window, "innerWidth", { writable: true, configurable: true, value: 360 });
    vi.useFakeTimers();

    const { container } = render(<VideoRow video={video} isExpanded={false} onToggle={vi.fn()} />);
    fireEvent.mouseOver(container.querySelector(".row-header"));
    act(() => vi.advanceTimersByTime(300));

    const popup = document.body.querySelector(".preview-popup");
    expect(popup).not.toBeNull();
    expect(parseFloat(popup.style.left)).toBeGreaterThanOrEqual(12);

    vi.useRealTimers();
    Object.defineProperty(window, "innerWidth", { writable: true, configurable: true, value: originalInnerWidth });
  });

  // Regression test: the popup's coordinates used to be computed only at the
  // instant showPreview flipped true, so a wheel scroll (which doesn't
  // reliably fire mouseleave) left it detached at stale viewport coordinates.
  // The fix recomputes on window "scroll" via a requestAnimationFrame-
  // throttled listener — advanceTimersToNextFrame() flushes that rAF under
  // fake timers. This must fail against the pre-fix code: computePopupPosition
  // there only ran inline during render, and a native scroll event dispatch
  // triggers no re-render, so the popup would keep its first-open position.
  test("scrolling without pointer movement repositions the popup instead of leaving it stale", () => {
    vi.useFakeTimers();
    const { container } = render(<VideoRow video={video} isExpanded={false} onToggle={vi.fn()} />);
    const header = container.querySelector(".row-header");
    const rectMock = vi.fn(() => ({ top: 100, left: 20, width: 200, height: 40, right: 220, bottom: 140 }));
    header.getBoundingClientRect = rectMock;

    fireEvent.mouseOver(header);
    act(() => vi.advanceTimersByTime(300));

    const popup = document.body.querySelector(".preview-popup");
    expect(popup.style.top).toBe("18.75px");
    expect(popup.style.left).toBe("128px");

    rectMock.mockReturnValue({ top: 500, left: 60, width: 200, height: 40, right: 260, bottom: 540 });
    act(() => {
      window.dispatchEvent(new Event("scroll"));
      vi.advanceTimersToNextFrame();
    });

    const updated = document.body.querySelector(".preview-popup");
    expect(updated.style.top).toBe("418.75px");
    expect(updated.style.left).toBe("168px");

    vi.useRealTimers();
  });

  test("unhovering removes the popup and the thumb img stays in place", () => {
    vi.useFakeTimers();
    const { container } = render(<VideoRow video={video} isExpanded={false} onToggle={vi.fn()} />);

    fireEvent.mouseOver(container.querySelector(".row-header"));
    act(() => vi.advanceTimersByTime(300));
    fireEvent.mouseOut(container.querySelector(".row-header"));

    expect(document.body.querySelector(".preview-popup")).toBeNull();
    expect(container.querySelector("img.row-thumb")).not.toBeNull();
    vi.useRealTimers();
  });

  test("expanded row never shows a hover preview popup (real player owns the stream)", () => {
    vi.useFakeTimers();
    const { container } = render(<VideoRow video={video} isExpanded={true} onToggle={vi.fn()} />);

    fireEvent.mouseOver(container.querySelector(".row-header"));
    act(() => vi.advanceTimersByTime(1000));

    expect(document.body.querySelector(".preview-popup")).toBeNull();
    vi.useRealTimers();
  });

  // The hook only tears previewing down via a deferred effect (after commit),
  // so a real hover+expand can't observe the intermediate render — by the
  // time act() returns, the effect has already self-corrected it. Freezing
  // previewing=true via the hook lets this test see what the render itself
  // would have committed, proving the guard (not the effect) is what keeps
  // the popup and the row-panel player from ever coexisting. The video count
  // is taken off document.body (not container) since the popup portals there.
  test("expanding a row never commits the preview popup alongside the panel, even if the hook still reports previewing", () => {
    const spy = vi.spyOn(hoverPreviewModule, "useHoverPreview").mockReturnValue({
      previewing: true,
      onMouseEnter: vi.fn(),
      onMouseLeave: vi.fn(),
    });

    const { container } = render(<VideoRow video={video} isExpanded={true} onToggle={vi.fn()} />);

    expect(document.body.querySelector(".preview-popup")).toBeNull();
    expect(container.querySelector(".row-panel")).not.toBeNull();
    expect(document.body.querySelectorAll("video")).toHaveLength(1);

    spy.mockRestore();
  });

  test("non-empty caption becomes the row title, and the file name moves to the metadata line", () => {
    const captioned = { ...video, caption: "A Great Clip", posted_ts: null };
    const { container, getByText } = render(<VideoRow video={captioned} isExpanded={false} onToggle={vi.fn()} />);

    expect(getByText("A Great Clip").className).toBe("row-title");
    expect(getByText("clip.mp4").className).toBe("row-meta");
    expect(container.querySelector(".row-title").getAttribute("title")).toBe("clip.mp4");
  });

  test("null caption falls back to the file name as the title, exactly like before", () => {
    const noCaption = { ...video, caption: null, posted_ts: null };
    const { getByText } = render(<VideoRow video={noCaption} isExpanded={false} onToggle={vi.fn()} />);

    expect(getByText("clip.mp4").className).toBe("row-title");
  });

  test("empty-string caption falls back to the file name as the title", () => {
    const emptyCaption = { ...video, caption: "", posted_ts: null };
    const { getByText } = render(<VideoRow video={emptyCaption} isExpanded={false} onToggle={vi.fn()} />);

    expect(getByText("clip.mp4").className).toBe("row-title");
  });

  test("posted_ts present adds a labeled posted date to the metadata line", () => {
    const posted = { ...video, caption: null, posted_ts: 1700000000 };
    const { container } = render(<VideoRow video={posted} isExpanded={false} onToggle={vi.fn()} />);

    const meta = container.querySelector(".row-meta");
    expect(meta).not.toBeNull();
    expect(meta.textContent).toBe(`posted ${new Date(1700000000 * 1000).toISOString().slice(0, 10)}`);
  });

  test("posted_ts and caption both present combine on one metadata line", () => {
    const both = { ...video, caption: "A Great Clip", posted_ts: 1700000000 };
    const { container, getByText } = render(<VideoRow video={both} isExpanded={false} onToggle={vi.fn()} />);

    expect(getByText("A Great Clip").className).toBe("row-title");
    const expectedDate = new Date(1700000000 * 1000).toISOString().slice(0, 10);
    expect(container.querySelector(".row-meta").textContent).toBe(`clip.mp4 · posted ${expectedDate}`);
  });

  test("posted_ts null omits the metadata line when there is also no caption", () => {
    const neither = { ...video, caption: null, posted_ts: null };
    const { container } = render(<VideoRow video={neither} isExpanded={false} onToggle={vi.fn()} />);

    expect(container.querySelector(".row-meta")).toBeNull();
  });

  test("videos lacking caption and posted_ts fields entirely render exactly as today — no null/undefined/NaN", () => {
    const legacyVideo = { id: 7, name: "clip.mp4", date: "2024-03-15T12:34:56+00:00", duration: 754, size: 10485760 };
    const { container, getByText } = render(<VideoRow video={legacyVideo} isExpanded={false} onToggle={vi.fn()} />);

    expect(getByText("clip.mp4").className).toBe("row-title");
    expect(container.querySelector(".row-meta")).toBeNull();
    expect(container.textContent).not.toMatch(/null|undefined|NaN/);
  });
});
