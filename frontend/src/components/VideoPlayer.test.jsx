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
    const { container, getByRole } = render(<VideoPlayer video={video} />);

    const player = container.querySelector("video.player-video");
    player.currentTime = 10;

    fireEvent.click(getByRole("button", { name: /forward 5 seconds/i }));

    expect(player.currentTime).toBe(15);
  });

  test("back skip button rewinds currentTime by 5s", () => {
    const { container, getByRole } = render(<VideoPlayer video={video} />);

    const player = container.querySelector("video.player-video");
    player.currentTime = 10;

    fireEvent.click(getByRole("button", { name: /back 5 seconds/i }));

    expect(player.currentTime).toBe(5);
  });

  test("back skip button clamps at 0 instead of going negative", () => {
    const { container, getByRole } = render(<VideoPlayer video={video} />);

    const player = container.querySelector("video.player-video");
    player.currentTime = 3;

    fireEvent.click(getByRole("button", { name: /back 5 seconds/i }));

    expect(player.currentTime).toBe(0);
  });

  test("skip buttons expose accessible names for forward and back", () => {
    const { getByRole } = render(<VideoPlayer video={video} />);

    expect(getByRole("button", { name: /forward 5 seconds/i })).toBeInTheDocument();
    expect(getByRole("button", { name: /back 5 seconds/i })).toBeInTheDocument();
  });

  test("decode error (code 3) shows the unsupported-codec message", () => {
    const { container, getByRole } = render(<VideoPlayer video={video} />);

    const player = container.querySelector("video.player-video");
    Object.defineProperty(player, "error", { value: { code: 3 }, configurable: true });
    fireEvent.error(player);

    expect(getByRole("alert")).toHaveTextContent("Can't play this video — unsupported codec (error 3)");
  });

  test("src-not-supported error (code 4) shows the unsupported-codec message", () => {
    const { container, getByRole } = render(<VideoPlayer video={video} />);

    const player = container.querySelector("video.player-video");
    Object.defineProperty(player, "error", { value: { code: 4 }, configurable: true });
    fireEvent.error(player);

    expect(getByRole("alert")).toHaveTextContent("Can't play this video — unsupported codec (error 4)");
  });

  test("other error codes show the generic playback error message", () => {
    const { container, getByRole } = render(<VideoPlayer video={video} />);

    const player = container.querySelector("video.player-video");
    Object.defineProperty(player, "error", { value: { code: 2 }, configurable: true });
    fireEvent.error(player);

    expect(getByRole("alert")).toHaveTextContent("Playback error (2)");
  });

  test("shows no error message when no error has occurred", () => {
    const { queryByRole } = render(<VideoPlayer video={video} />);

    expect(queryByRole("alert")).not.toBeInTheDocument();
  });

  test("switching to a different video clears a previous playback error", () => {
    const { container, getByRole, queryByRole, rerender } = render(<VideoPlayer video={video} />);

    const player = container.querySelector("video.player-video");
    Object.defineProperty(player, "error", { value: { code: 3 }, configurable: true });
    fireEvent.error(player);

    expect(getByRole("alert")).toBeInTheDocument();

    rerender(<VideoPlayer video={{ ...video, id: 8 }} />);

    expect(queryByRole("alert")).not.toBeInTheDocument();
  });
});
