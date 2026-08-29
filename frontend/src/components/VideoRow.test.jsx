import { describe, test, expect, vi, beforeEach } from "vitest";
import { render, fireEvent, act, waitFor } from "@testing-library/react";
import { PREVIEW_POPUP_WIDTH, QUEUE_GRACE_MS, VideoRow } from "./VideoRow";
import * as hoverPreviewModule from "../hooks/useHoverPreview";
import { requestPriorityCache } from "../api/client";

// Partial mock: streamUrl/thumbUrl/previewStreamUrl keep their real
// implementation (they read the VITE_API_BASE pinned in vitest.config.js,
// and jsdom never actually loads <img>/<video> sources), but
// requestPriorityCache is replaced so the queue-button tests control it.
vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, requestPriorityCache: vi.fn() };
});

const video = {
  id: 7,
  name: "clip.mp4",
  date: "2024-03-15T12:34:56+00:00",
  duration: 754,
  size: 10485760,
};

describe("VideoRow", () => {
  beforeEach(() => {
    requestPriorityCache.mockReset();
  });

  test("uses a 720px preview popup width", () => {
    expect(PREVIEW_POPUP_WIDTH).toBe(720);
  });
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

  test("shows a download-now button instead of progress with default props", () => {
    const { container } = render(<VideoRow video={video} isExpanded={false} onToggle={vi.fn()} />);

    const button = container.querySelector(".cache-strip-queue");
    expect(button).not.toBeNull();
    expect(button.textContent).toBe("+");
    expect(container.querySelector(".cache-strip-label")).toBeNull();
    expect(container.querySelector(".cache-strip-fill")).toBeNull();
  });

  test("download-now button is absent once a video has any progress, is downloading, or is fully cached", () => {
    const partial = render(<VideoRow video={video} isExpanded={false} onToggle={vi.fn()} cachedBytes={video.size * 0.1} />);
    expect(partial.container.querySelector(".cache-strip-queue")).toBeNull();

    const downloading = render(
      <VideoRow video={video} isExpanded={false} onToggle={vi.fn()} isDownloading cachedBytes={0} />,
    );
    expect(downloading.container.querySelector(".cache-strip-queue")).toBeNull();

    const cached = render(<VideoRow video={video} isExpanded={false} onToggle={vi.fn()} cachedBytes={video.size} />);
    expect(cached.container.querySelector(".cache-strip-queue")).toBeNull();
  });

  test("clicking the download-now button queues the video for priority download and does not toggle the row", async () => {
    requestPriorityCache.mockResolvedValue({ success: true });
    const onToggle = vi.fn();
    const { container } = render(<VideoRow video={video} isExpanded={false} onToggle={onToggle} />);

    fireEvent.click(container.querySelector(".cache-strip-queue"));

    expect(requestPriorityCache).toHaveBeenCalledWith(7);
    expect(onToggle).not.toHaveBeenCalled();
  });

  test("the download-now button shows a pending state after a successful click", async () => {
    let resolveRequest;
    requestPriorityCache.mockReturnValue(new Promise((resolve) => { resolveRequest = resolve; }));
    const { container } = render(<VideoRow video={video} isExpanded={false} onToggle={vi.fn()} />);

    const button = container.querySelector(".cache-strip-queue");
    fireEvent.click(button);

    expect(button.disabled).toBe(true);
    expect(button.textContent).toBe("…");
    expect(button.className).toContain("is-queued");

    await act(async () => {
      resolveRequest({ success: true });
    });
  });

  test("a failed request re-enables the download-now button and surfaces the error message", async () => {
    requestPriorityCache.mockResolvedValue({ success: false, message: "No active channel" });
    const { container } = render(<VideoRow video={video} isExpanded={false} onToggle={vi.fn()} />);

    const button = container.querySelector(".cache-strip-queue");
    fireEvent.click(button);

    await waitFor(() => expect(button.disabled).toBe(false));
    expect(button.textContent).toBe("+");
    expect(button.title).toBe("No active channel");
  });

  test("a rejected request re-enables the download-now button and surfaces the error message", async () => {
    requestPriorityCache.mockRejectedValue(new Error("network down"));
    const { container } = render(<VideoRow video={video} isExpanded={false} onToggle={vi.fn()} />);

    const button = container.querySelector(".cache-strip-queue");
    fireEvent.click(button);

    await waitFor(() => expect(button.disabled).toBe(false));
    expect(button.title).toBe("network down");
  });

  // Regression test: the backend can report success:true for a priority job
  // it silently drops later (e.g. an oversized video — see prefetch.py's
  // select_priority_job), which used to leave this button disabled as "…"
  // forever, since only a failure response ever cleared the pending state
  // and no failure ever arrives for a silently-dropped job. This must fail
  // against the pre-fix code: reverting the CacheQueueButton grace-timer
  // change and rerunning this test leaves the button stuck at "…" past
  // QUEUE_GRACE_MS (verified by hand against the prior version).
  test("a stuck button self-resets after the grace period with no progress or download activity", async () => {
    vi.useFakeTimers();
    // Never resolves — same as a request the backend accepted but whose
    // worker job it later discarded without telling the frontend.
    requestPriorityCache.mockReturnValue(new Promise(() => {}));
    const { container } = render(<VideoRow video={video} isExpanded={false} onToggle={vi.fn()} />);

    const button = container.querySelector(".cache-strip-queue");
    fireEvent.click(button);
    expect(button.disabled).toBe(true);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(QUEUE_GRACE_MS);
    });

    const resetButton = container.querySelector(".cache-strip-queue");
    expect(resetButton.disabled).toBe(false);
    expect(resetButton.textContent).toBe("+");
    expect(resetButton.title).not.toBe("Download now — jumps to the front of the queue");
    vi.useRealTimers();
  });

  test("the button unmounts cleanly once progress appears, without its grace timer firing into an unmounted component", () => {
    vi.useFakeTimers();
    requestPriorityCache.mockReturnValue(new Promise(() => {}));
    const { container, rerender } = render(<VideoRow video={video} isExpanded={false} onToggle={vi.fn()} />);

    fireEvent.click(container.querySelector(".cache-strip-queue"));

    // Progress arrives: VideoRow itself stops rendering the queue button
    // (showQueueButton flips false), same as before this fix.
    rerender(<VideoRow video={video} isExpanded={false} onToggle={vi.fn()} isDownloading cachedBytes={0} />);
    expect(container.querySelector(".cache-strip-queue")).toBeNull();

    expect(() => act(() => vi.advanceTimersByTime(QUEUE_GRACE_MS))).not.toThrow();
    vi.useRealTimers();
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

  test("clicking at 25% seeks to 25% and does not toggle the row", () => {
    vi.useFakeTimers();
    const onToggle = vi.fn();
    const { container } = render(<VideoRow video={video} isExpanded={false} onToggle={onToggle} />);
    fireEvent.mouseOver(container.querySelector(".row-header"));
    act(() => vi.advanceTimersByTime(300));
    const preview = document.body.querySelector(".preview-popup-video");
    Object.defineProperty(preview, "duration", { configurable: true, value: 400 });
    preview.getBoundingClientRect = () => ({ left: 100, width: 720 });
    fireEvent.click(preview, { clientX: 280 });
    expect(preview.currentTime).toBe(100);
    expect(onToggle).not.toHaveBeenCalled();
    vi.useRealTimers();
  });

  test("progress bar tracks timeupdate using the video prop duration fallback", () => {
    vi.useFakeTimers();
    const { container } = render(<VideoRow video={video} isExpanded={false} onToggle={vi.fn()} />);
    fireEvent.mouseOver(container.querySelector(".row-header"));
    act(() => vi.advanceTimersByTime(300));
    const preview = document.body.querySelector(".preview-popup-video");
    Object.defineProperty(preview, "duration", { configurable: true, value: Number.NaN });
    preview.currentTime = video.duration / 4;
    fireEvent.timeUpdate(preview);
    expect(document.body.querySelector(".preview-popup-progress-fill").style.width).toBe("25%");
    vi.useRealTimers();
  });

  // Regression test: at small viewports the popup renders smaller than its
  // 720px desktop size (index.css: width min(720px, 100vw - 24px) with a 16/9
  // aspect-ratio), so the position math must shrink both its width and height
  // assumptions. Otherwise a clamp's bounds invert and push the popup off the
  // left or top edge (see computePopupPosition).
  test("small viewport clamps the popup to its rendered 336x189 size, not the 720x405 desktop size", () => {
    const restoreViewport = setViewport(360, 360);
    vi.useFakeTimers();

    const { container } = render(<VideoRow video={video} isExpanded={false} onToggle={vi.fn()} />);
    const header = container.querySelector(".row-header");
    // A row near the bottom edge: the popup must be pushed up just far enough
    // for its real 189px height to fit (360 - 12 - 189 = 159). Assuming the
    // desktop 405px height instead leaves it wrongly pinned to the top gap.
    header.getBoundingClientRect = () => ({ top: 300, left: 20, width: 200, height: 40, right: 220, bottom: 340 });
    fireEvent.mouseOver(header);
    act(() => vi.advanceTimersByTime(300));

    const popup = document.body.querySelector(".preview-popup");
    expect(popup).not.toBeNull();
    expect(popup.style.top).toBe("159px");
    expect(popup.style.left).toBe("12px");

    vi.useRealTimers();
    restoreViewport();
  });

  test("a popup taller than the viewport keeps its top edge on-screen", () => {
    // Landscape-phone shape: full 720x405 popup, but only 360px of height.
    const restoreViewport = setViewport(800, 360);
    vi.useFakeTimers();

    const { container } = render(<VideoRow video={video} isExpanded={false} onToggle={vi.fn()} />);
    const header = container.querySelector(".row-header");
    header.getBoundingClientRect = () => ({ top: 100, left: 20, width: 200, height: 40, right: 220, bottom: 140 });
    fireEvent.mouseOver(header);
    act(() => vi.advanceTimersByTime(300));

    const popup = document.body.querySelector(".preview-popup");
    expect(popup).not.toBeNull();
    expect(popup.style.top).toBe("12px");

    vi.useRealTimers();
    restoreViewport();
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
    expect(popup.style.top).toBe("12px");
    expect(popup.style.left).toBe("128px");

    rectMock.mockReturnValue({ top: 500, left: 60, width: 200, height: 40, right: 260, bottom: 540 });
    act(() => {
      window.dispatchEvent(new Event("scroll"));
      vi.advanceTimersToNextFrame();
    });

    const updated = document.body.querySelector(".preview-popup");
    expect(updated.style.top).toBe("317.5px");
    expect(updated.style.left).toBe("168px");

    vi.useRealTimers();
  });

  test("unhovering removes the popup after grace and the thumb img stays in place", () => {
    vi.useFakeTimers();
    const { container } = render(<VideoRow video={video} isExpanded={false} onToggle={vi.fn()} />);

    fireEvent.mouseOver(container.querySelector(".row-header"));
    act(() => vi.advanceTimersByTime(300));
    fireEvent.mouseOut(container.querySelector(".row-header"));

    expect(document.body.querySelector(".preview-popup")).not.toBeNull();
    act(() => vi.advanceTimersByTime(150));
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
      onPopupEnter: vi.fn(),
      onPopupLeave: vi.fn(),
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

  test("an UNKNOWN_VIDEO_* playability verdict renders the unsupported-codec badge and the unplayable class", () => {
    const unplayable = { ...video, playability: "UNKNOWN_VIDEO_mpeg4" };
    const { container, getByText } = render(<VideoRow video={unplayable} isExpanded={false} onToggle={vi.fn()} />);

    expect(container.querySelector(".video-row").className).toBe("video-row unplayable");
    expect(getByText("unsupported codec").className).toBe("row-badge-unplayable");
  });

  test("a PLAYS verdict or a missing playability field render neither the badge nor the unplayable class", () => {
    const plays = render(<VideoRow video={{ ...video, playability: "PLAYS" }} isExpanded={false} onToggle={vi.fn()} />);
    expect(plays.container.querySelector(".video-row").className).toBe("video-row");
    expect(plays.container.querySelector(".row-badge-unplayable")).toBeNull();

    const legacy = render(<VideoRow video={video} isExpanded={false} onToggle={vi.fn()} />);
    expect(legacy.container.querySelector(".video-row").className).toBe("video-row");
    expect(legacy.container.querySelector(".row-badge-unplayable")).toBeNull();
  });

  test("a row showing the unsupported-codec badge still expands into the player on click", () => {
    const onToggle = vi.fn();
    const unplayableVideo = { ...video, playability: "AUDIO_FAILS" };
    const { container, rerender } = render(
      <VideoRow video={unplayableVideo} isExpanded={false} onToggle={onToggle} />,
    );

    fireEvent.click(container.querySelector(".row-header"));
    expect(onToggle).toHaveBeenCalledWith(7);

    rerender(<VideoRow video={unplayableVideo} isExpanded={true} onToggle={onToggle} />);
    expect(container.querySelector("video")).not.toBeNull();
  });
});

//---

// Overrides window.innerWidth/innerHeight for one test; returns a restore fn.
function setViewport(width, height) {
  const originalWidth = window.innerWidth;
  const originalHeight = window.innerHeight;
  Object.defineProperty(window, "innerWidth", { writable: true, configurable: true, value: width });
  Object.defineProperty(window, "innerHeight", { writable: true, configurable: true, value: height });
  return () => {
    Object.defineProperty(window, "innerWidth", { writable: true, configurable: true, value: originalWidth });
    Object.defineProperty(window, "innerHeight", { writable: true, configurable: true, value: originalHeight });
  };
}
