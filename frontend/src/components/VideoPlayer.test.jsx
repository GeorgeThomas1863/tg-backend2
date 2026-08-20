import { describe, test, expect, vi } from "vitest";
import { render, fireEvent } from "@testing-library/react";
import { VideoPlayer } from "./VideoPlayer";

// No api-client mock: streamUrl/thumbUrl read the VITE_API_BASE pinned in
// vitest.config.js, and jsdom never actually loads <video> sources.
const video = {
  id: 7,
  name: "clip.mp4",
  date: "2024-03-15T12:34:56+00:00",
  duration: 754,
  size: 10485760,
};

describe("VideoPlayer", () => {
  test("sets volume to 50% on mount", () => {
    const { container } = render(<VideoPlayer video={video} />);

    const player = container.querySelector("video.player-video");
    expect(player.volume).toBe(0.5);
  });

  test("forward skip button advances currentTime by 5s", () => {
    const { container, getByText } = render(<VideoPlayer video={video} />);

    const player = container.querySelector("video.player-video");
    player.currentTime = 10;

    fireEvent.click(getByText("5s »"));

    expect(player.currentTime).toBe(15);
  });

  test("back skip button rewinds currentTime by 5s", () => {
    const { container, getByText } = render(<VideoPlayer video={video} />);

    const player = container.querySelector("video.player-video");
    player.currentTime = 10;

    fireEvent.click(getByText("« 5s"));

    expect(player.currentTime).toBe(5);
  });

  test("back skip button clamps at 0 instead of going negative", () => {
    const { container, getByText } = render(<VideoPlayer video={video} />);

    const player = container.querySelector("video.player-video");
    player.currentTime = 3;

    fireEvent.click(getByText("« 5s"));

    expect(player.currentTime).toBe(0);
  });
});
