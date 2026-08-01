import { describe, test, expect, vi } from "vitest";
import { render } from "@testing-library/react";
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
});
