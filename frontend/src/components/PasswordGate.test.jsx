import { describe, test, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { PasswordGate } from "./PasswordGate";
import { postLogin } from "../api/client";

vi.mock("../api/client", () => ({
  postLogin: vi.fn(),
}));

beforeEach(() => {
  postLogin.mockReset();
});

function typePassword(value) {
  fireEvent.change(screen.getByLabelText("Enter the site password"), { target: { value } });
}

function submitForm() {
  fireEvent.click(screen.getByRole("button", { name: "Submit" }));
}

describe("PasswordGate", () => {
  test("shows the failure message from postLogin and does NOT call onSuccess on wrong password", async () => {
    postLogin.mockResolvedValue({ success: false, message: "Wrong password" });
    const onSuccess = vi.fn();
    render(<PasswordGate onSuccess={onSuccess} />);

    typePassword("nope");
    submitForm();

    expect(await screen.findByText("Wrong password")).toBeInTheDocument();
    expect(onSuccess).not.toHaveBeenCalled();
    expect(postLogin).toHaveBeenCalledWith("nope");
  });

  test("calls onSuccess when postLogin resolves {success:true}", async () => {
    postLogin.mockResolvedValue({ success: true });
    const onSuccess = vi.fn();
    render(<PasswordGate onSuccess={onSuccess} />);

    typePassword("right one");
    submitForm();

    await waitFor(() => expect(onSuccess).toHaveBeenCalledTimes(1));
  });

  test("does not call postLogin at all when submitting an empty password", async () => {
    const onSuccess = vi.fn();
    render(<PasswordGate onSuccess={onSuccess} />);

    submitForm();

    // Flush any pending microtasks before asserting nothing happened.
    await waitFor(() => expect(postLogin).not.toHaveBeenCalled());
    expect(onSuccess).not.toHaveBeenCalled();
  });
});
