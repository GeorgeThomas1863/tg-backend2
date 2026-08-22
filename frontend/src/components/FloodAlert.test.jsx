import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, test, vi } from "vitest";
import { FloodAlert } from "./FloodAlert";

describe("FloodAlert", () => {
  test.each([
    [undefined, "undefined flood"],
    [{ count: 0, last_seconds_ago: null }, "zero incidents"],
    [{ count: 1, last_seconds_ago: 601 }, "stale incident"],
  ])("stays hidden for %s", (flood) => {
    render(<FloodAlert flood={flood} paused={false} onPause={vi.fn()} />);
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
  });

  test("shows a recent incident with its count and age", () => {
    render(<FloodAlert flood={{ count: 1, last_seconds_ago: 42.1 }} paused={false} onPause={vi.fn()} />);
    expect(screen.getByRole("alertdialog")).toHaveTextContent("1 incident since the backend started");
    expect(screen.getByRole("alertdialog")).toHaveTextContent("42s ago");
  });

  test("dismisses until the incident count increases", () => {
    const { rerender } = render(<FloodAlert flood={{ count: 1, last_seconds_ago: 10 }} paused={false} onPause={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();

    rerender(<FloodAlert flood={{ count: 2, last_seconds_ago: 5 }} paused={false} onPause={vi.fn()} />);
    expect(screen.getByRole("alertdialog")).toBeInTheDocument();
  });

  test("shows a new incident after a backend restart resets the count below the dismissed count", () => {
    const { rerender } = render(<FloodAlert flood={{ count: 2, last_seconds_ago: 10 }} paused={false} onPause={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));

    rerender(<FloodAlert flood={{ count: 0, last_seconds_ago: null }} paused={false} onPause={vi.fn()} />);
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();

    rerender(<FloodAlert flood={{ count: 1, last_seconds_ago: 5 }} paused={false} onPause={vi.fn()} />);
    expect(screen.getByRole("alertdialog")).toHaveTextContent("1 incident since the backend started");
  });

  test("calls the pause action", () => {
    const onPause = vi.fn();
    render(<FloodAlert flood={{ count: 1, last_seconds_ago: 10 }} paused={false} onPause={onPause} />);
    fireEvent.click(screen.getByRole("button", { name: "Pause downloads" }));
    expect(onPause).toHaveBeenCalledOnce();
  });

  test("shows disabled paused text instead of the pause button", () => {
    render(<FloodAlert flood={{ count: 1, last_seconds_ago: 10 }} paused onPause={vi.fn()} />);
    expect(screen.getByText("Downloads are paused")).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Pause downloads" })).not.toBeInTheDocument();
  });

  test("dismisses when Escape is pressed", () => {
    render(<FloodAlert flood={{ count: 1, last_seconds_ago: 10 }} paused={false} onPause={vi.fn()} />);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
  });
});
