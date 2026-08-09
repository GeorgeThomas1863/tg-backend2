import { describe, test, expect, vi, beforeEach, afterEach } from "vitest";
import { render } from "@testing-library/react";
import { useSentinel } from "./useSentinel";

let observed;
let trigger;

class FakeObserver {
  constructor(callback) {
    trigger = (isIntersecting) => callback([{ isIntersecting }]);
  }
  observe(node) {
    observed = node;
  }
  disconnect() {
    observed = null;
  }
}

beforeEach(() => {
  observed = null;
  trigger = null;
  vi.stubGlobal("IntersectionObserver", FakeObserver);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function Probe({ onVisible }) {
  const ref = useSentinel(onVisible);
  return <div data-testid="sentinel" ref={ref} />;
}

function LoadingProbe({ isLoading, onVisible }) {
  const ref = useSentinel(onVisible);
  if (isLoading) return <div>Loading…</div>;
  return <div data-testid="sentinel" ref={ref} />;
}

describe("useSentinel", () => {
  test("observes the ref'd element and fires onVisible on intersection", () => {
    const onVisible = vi.fn();
    render(<Probe onVisible={onVisible} />);

    expect(observed).not.toBeNull();
    trigger(true);
    expect(onVisible).toHaveBeenCalledTimes(1);
  });

  test("does not fire when the entry is not intersecting", () => {
    const onVisible = vi.fn();
    render(<Probe onVisible={onVisible} />);

    trigger(false);
    expect(onVisible).not.toHaveBeenCalled();
  });

  test("always calls the LATEST callback (no stale closure)", () => {
    const first = vi.fn();
    const second = vi.fn();
    const { rerender } = render(<Probe onVisible={first} />);

    rerender(<Probe onVisible={second} />);
    trigger(true);

    expect(first).not.toHaveBeenCalled();
    expect(second).toHaveBeenCalledTimes(1);
  });

  test("attaches when the sentinel mounts after the loading state", () => {
    const onVisible = vi.fn();
    const { rerender } = render(
      <LoadingProbe isLoading onVisible={onVisible} />,
    );

    expect(observed).toBeNull();

    rerender(<LoadingProbe isLoading={false} onVisible={onVisible} />);
    expect(observed).not.toBeNull();

    trigger(true);
    expect(onVisible).toHaveBeenCalledTimes(1);
  });

  test("disconnects on unmount", () => {
    const { unmount } = render(<Probe onVisible={vi.fn()} />);
    unmount();
    expect(observed).toBeNull();
  });
});
