import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, test, vi } from "vitest";
import { JumpControls } from "./JumpControls";

describe("JumpControls", () => {
  test.each(["", "-1", "1.5"])("does not submit invalid position %p", (value) => {
    const onJump = vi.fn();
    render(<JumpControls total={100} onJump={onJump} />);
    const input = screen.getByLabelText("Jump to #");
    fireEvent.change(input, { target: { value } });
    fireEvent.submit(input.closest("form"));
    expect(onJump).not.toHaveBeenCalled();
  });

  test("submits a valid offset and clamps it to the last available position", () => {
    const onJump = vi.fn();
    render(<JumpControls total={100} onJump={onJump} />);
    fireEvent.change(screen.getByLabelText("Jump to #"), { target: { value: "400" } });
    fireEvent.click(screen.getByRole("button", { name: "Go" }));
    expect(onJump).toHaveBeenCalledWith(99);
  });

  test("does not render the total label itself when total is null", () => {
    render(<JumpControls total={null} onJump={() => {}} />);
    expect(screen.queryByText(/videos/)).toBeNull();
  });
});
