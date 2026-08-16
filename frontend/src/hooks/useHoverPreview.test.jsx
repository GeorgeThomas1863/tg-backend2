import { describe, test, expect, vi, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useHoverPreview } from "./useHoverPreview";

describe("useHoverPreview", () => {
  afterEach(() => vi.useRealTimers());

  test("previewing flips true only after the 300ms hover delay", () => {
    vi.useFakeTimers();
    const { result } = renderHook(() => useHoverPreview(false));

    act(() => result.current.onMouseEnter());
    expect(result.current.previewing).toBe(false);

    act(() => vi.advanceTimersByTime(300));
    expect(result.current.previewing).toBe(true);
  });

  test("leaving before the delay cancels the pending preview", () => {
    vi.useFakeTimers();
    const { result } = renderHook(() => useHoverPreview(false));

    act(() => result.current.onMouseEnter());
    act(() => result.current.onMouseLeave());
    act(() => vi.advanceTimersByTime(1000));

    expect(result.current.previewing).toBe(false);
  });

  test("disabled hook never starts a preview", () => {
    vi.useFakeTimers();
    const { result } = renderHook(() => useHoverPreview(true));

    act(() => result.current.onMouseEnter());
    act(() => vi.advanceTimersByTime(1000));

    expect(result.current.previewing).toBe(false);
  });

  test("disabling mid-preview stops it (row expanded while hovering)", () => {
    vi.useFakeTimers();
    const { result, rerender } = renderHook(({ d }) => useHoverPreview(d), {
      initialProps: { d: false },
    });

    act(() => result.current.onMouseEnter());
    act(() => vi.advanceTimersByTime(300));
    expect(result.current.previewing).toBe(true);

    rerender({ d: true });
    expect(result.current.previewing).toBe(false);
  });
});
