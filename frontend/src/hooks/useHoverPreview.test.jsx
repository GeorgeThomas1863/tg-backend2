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

  test("leaving an open row and entering the popup during grace keeps it open", () => {
    vi.useFakeTimers();
    const { result } = renderHook(() => useHoverPreview(false));
    act(() => result.current.onMouseEnter());
    act(() => vi.advanceTimersByTime(300));
    act(() => result.current.onMouseLeave());
    act(() => vi.advanceTimersByTime(149));
    act(() => result.current.onPopupEnter());
    act(() => vi.advanceTimersByTime(300));
    expect(result.current.previewing).toBe(true);
  });

  test("leaving the popup closes an open preview after the grace delay", () => {
    vi.useFakeTimers();
    const { result } = renderHook(() => useHoverPreview(false));
    act(() => result.current.onMouseEnter());
    act(() => vi.advanceTimersByTime(300));
    act(() => result.current.onPopupEnter());
    act(() => result.current.onPopupLeave());
    act(() => vi.advanceTimersByTime(149));
    expect(result.current.previewing).toBe(true);
    act(() => vi.advanceTimersByTime(1));
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

  test("disabled true->false re-arms the debounce when the pointer is still hovering", () => {
    vi.useFakeTimers();
    const { result, rerender } = renderHook(({ d }) => useHoverPreview(d), {
      initialProps: { d: false },
    });

    act(() => result.current.onMouseEnter());
    act(() => vi.advanceTimersByTime(300));
    expect(result.current.previewing).toBe(true);

    // Expand (pointer never moves, so no mouseleave fires) then collapse.
    rerender({ d: true });
    expect(result.current.previewing).toBe(false);
    rerender({ d: false });
    expect(result.current.previewing).toBe(false);

    act(() => vi.advanceTimersByTime(299));
    expect(result.current.previewing).toBe(false);
    act(() => vi.advanceTimersByTime(1));
    expect(result.current.previewing).toBe(true);
  });

  test("disabled true->false does NOT re-arm once the pointer has left", () => {
    vi.useFakeTimers();
    const { result, rerender } = renderHook(({ d }) => useHoverPreview(d), {
      initialProps: { d: false },
    });

    act(() => result.current.onMouseEnter());
    act(() => vi.advanceTimersByTime(300));
    expect(result.current.previewing).toBe(true);

    rerender({ d: true });
    act(() => result.current.onMouseLeave());
    rerender({ d: false });

    act(() => vi.advanceTimersByTime(1000));
    expect(result.current.previewing).toBe(false);
  });

  test("re-arming does not stack a second timer that outlives a later mouseleave", () => {
    vi.useFakeTimers();
    const { result, rerender } = renderHook(({ d }) => useHoverPreview(d), {
      initialProps: { d: true },
    });

    act(() => result.current.onMouseEnter()); // hovering while disabled: no timer starts
    rerender({ d: false }); // effect re-arms: the only timer starts here (fires at +300ms)
    act(() => vi.advanceTimersByTime(50));
    act(() => result.current.onMouseEnter()); // must be a no-op: a timer is already pending
    act(() => result.current.onMouseLeave()); // cancels the (single) pending timer

    // If mouseenter had stacked a second timer, it would have clobbered
    // timerRef's id and this leave would only cancel that second one,
    // leaving the original re-arm timer to fire anyway.
    act(() => vi.advanceTimersByTime(1000));
    expect(result.current.previewing).toBe(false);
  });
});
