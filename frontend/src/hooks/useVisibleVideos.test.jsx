import { describe, test, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { useVisibleVideos } from "./useVisibleVideos";
import { postVisibleVideos } from "../api/client";

vi.mock("../api/client", () => ({
  postVisibleVideos: vi.fn(async () => ({ success: true, message: "ok" })),
}));

let instances;

class FakeObserver {
  constructor(callback) {
    this.callback = callback;
    this.observed = new Set();
    instances.push(this);
  }
  observe(node) {
    this.observed.add(node);
  }
  unobserve(node) {
    this.observed.delete(node);
  }
  disconnect() {
    this.observed.clear();
  }
}

beforeEach(() => {
  instances = [];
  vi.useFakeTimers();
  vi.stubGlobal("IntersectionObserver", FakeObserver);
  vi.mocked(postVisibleVideos).mockClear();
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

function Probe({ videos }) {
  const observeRow = useVisibleVideos(videos);
  return (
    <div>
      {videos.map((video) => (
        <div key={video.id} data-testid={`row-${video.id}`} ref={observeRow(video.id)} />
      ))}
    </div>
  );
}

function triggerIntersection(id, isIntersecting) {
  const node = screen.getByTestId(`row-${id}`);
  instances[0].callback([{ target: node, isIntersecting }]);
}

const videos = [{ id: 1 }, { id: 2 }, { id: 3 }];

describe("useVisibleVideos", () => {
  test("reports visible rows in list order after the debounce", () => {
    render(<Probe videos={videos} />);

    triggerIntersection(3, true);
    triggerIntersection(1, true);
    expect(postVisibleVideos).not.toHaveBeenCalled();

    vi.runAllTimers();

    expect(postVisibleVideos).toHaveBeenCalledTimes(1);
    expect(postVisibleVideos).toHaveBeenCalledWith([1, 3]);
  });

  test("does not resend an unchanged visible set", () => {
    render(<Probe videos={videos} />);

    triggerIntersection(1, true);
    vi.runAllTimers();
    triggerIntersection(1, false);
    triggerIntersection(1, true);
    vi.runAllTimers();

    expect(postVisibleVideos).toHaveBeenCalledTimes(1);
  });

  test("sends the updated list when a row scrolls off screen", () => {
    render(<Probe videos={videos} />);

    triggerIntersection(1, true);
    triggerIntersection(3, true);
    vi.runAllTimers();
    triggerIntersection(1, false);
    vi.runAllTimers();

    expect(postVisibleVideos).toHaveBeenNthCalledWith(1, [1, 3]);
    expect(postVisibleVideos).toHaveBeenNthCalledWith(2, [3]);
  });

  test("sends the updated list when a visible row unmounts (filter change)", () => {
    const { rerender } = render(<Probe videos={videos} />);

    triggerIntersection(1, true);
    triggerIntersection(3, true);
    vi.runAllTimers();

    rerender(<Probe videos={[{ id: 3 }]} />);
    vi.runAllTimers();

    expect(postVisibleVideos).toHaveBeenNthCalledWith(2, [3]);
  });

  test("drops pending sends and disconnects the observer on unmount", () => {
    const { unmount } = render(<Probe videos={videos} />);

    triggerIntersection(1, true);
    unmount();
    vi.runAllTimers();

    expect(postVisibleVideos).not.toHaveBeenCalled();
    expect(instances[0].observed.size).toBe(0);
  });
});
