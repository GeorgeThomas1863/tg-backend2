import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, test, vi } from "vitest";
import { TelegramAuthDrawer } from "./TelegramAuthDrawer";

function renderDrawer(overrides = {}) {
  const props = { status: { authorized: false, user: null, pending_step: null }, busy: false, error: null, onSendCode: vi.fn(), onSubmitCode: vi.fn(), onSubmitPassword: vi.fn(), onLogout: vi.fn(), onClose: vi.fn(), ...overrides };
  return { props, ...render(<TelegramAuthDrawer {...props} />) };
}

describe("TelegramAuthDrawer", () => {
  test("renders identity and requires inline logout confirmation", () => {
    const { props } = renderDrawer({ status: { authorized: true, user: { username: "alice", phone: "+1" }, pending_step: null } });
    expect(screen.getByText("alice")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Log out" }));
    expect(props.onLogout).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(props.onLogout).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Log out" }));
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    expect(props.onLogout).toHaveBeenCalledOnce();
  });

  test.each([[null, "Phone number", "Send code"], ["code", "Login code", "Verify code"], ["password", "2FA password", "Log in"]])("renders %s step", (step, label, button) => {
    renderDrawer({ status: { authorized: false, user: null, pending_step: step } });
    expect(screen.getByLabelText(label)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: button })).toBeInTheDocument();
  });

  test("trims phone and ignores empty and duplicate submissions", () => {
    const pending = new Promise(() => {});
    const { props } = renderDrawer({ onSendCode: vi.fn(() => pending) });
    const input = screen.getByLabelText("Phone number");
    fireEvent.change(input, { target: { value: "   " } }); fireEvent.submit(input.closest("form"));
    fireEvent.change(input, { target: { value: "  +1555  " } }); fireEvent.submit(input.closest("form")); fireEvent.submit(input.closest("form"));
    expect(props.onSendCode).toHaveBeenCalledOnce();
    expect(props.onSendCode).toHaveBeenCalledWith("+1555");
  });

  test("toggles password visibility", () => {
    renderDrawer({ status: { authorized: false, user: null, pending_step: "password" } });
    const input = screen.getByLabelText("2FA password");
    expect(input).toHaveAttribute("type", "password");
    fireEvent.click(screen.getByRole("button", { name: "Show password" }));
    expect(input).toHaveAttribute("type", "text");
  });

  test("submits the raw password including whitespace", () => {
    const pending = new Promise(() => {});
    const { props } = renderDrawer({
      status: { authorized: false, user: null, pending_step: "password" },
      onSubmitPassword: vi.fn(() => pending),
    });
    const input = screen.getByLabelText("2FA password");

    fireEvent.submit(input.closest("form"));
    fireEvent.change(input, { target: { value: "  secret  " } });
    fireEvent.submit(input.closest("form"));
    fireEvent.submit(input.closest("form"));

    expect(props.onSubmitPassword).toHaveBeenCalledOnce();
    expect(props.onSubmitPassword).toHaveBeenCalledWith("  secret  ");
  });

  test("allows a whitespace-only password", () => {
    const { props } = renderDrawer({ status: { authorized: false, user: null, pending_step: "password" } });
    const input = screen.getByLabelText("2FA password");
    fireEvent.change(input, { target: { value: "   " } });
    fireEvent.submit(input.closest("form"));
    expect(props.onSubmitPassword).toHaveBeenCalledWith("   ");
  });

  test("shows errors, disables controls, and closes", () => {
    const { props } = renderDrawer({ busy: true, error: "Telegram unavailable" });
    expect(screen.getByRole("alert")).toHaveTextContent("Telegram unavailable");
    expect(screen.getByLabelText("Phone number")).toBeDisabled();
    expect(screen.getByRole("button", { name: "Send code" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Close Telegram account" }));
    expect(props.onClose).toHaveBeenCalledOnce();
  });
});
