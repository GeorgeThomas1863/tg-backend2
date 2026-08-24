import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, test, vi } from "vitest";
import { SortControl } from "./SortControl";

describe("SortControl", () => {
  test("renders both sort options and marks the current one as pressed", () => {
    render(<SortControl value="asc" onChange={vi.fn()} disabled={false} />);

    const oldest = screen.getByRole("button", { name: "Oldest first" });
    const newest = screen.getByRole("button", { name: "Newest first" });
    expect(oldest).toHaveAttribute("aria-pressed", "true");
    expect(newest).toHaveAttribute("aria-pressed", "false");
  });

  test("clicking the other option calls onChange with the right value", () => {
    const onChange = vi.fn();
    render(<SortControl value="asc" onChange={onChange} disabled={false} />);

    fireEvent.click(screen.getByRole("button", { name: "Newest first" }));

    expect(onChange).toHaveBeenCalledWith("desc");
  });

  test("is disabled with an explanatory title when disabled is true", () => {
    render(<SortControl value="asc" onChange={vi.fn()} disabled />);

    expect(screen.getByRole("group", { name: "Sort order" })).toHaveAttribute(
      "title",
      "Search results are ordered by relevance",
    );
    expect(screen.getByRole("button", { name: "Oldest first" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Newest first" })).toBeDisabled();
  });
});
