import { describe, test, expect, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { ChannelDrawer } from "./ChannelDrawer";

const channels = [
  { id: "active", channel: "current_channel", title: "Current", is_default: false, is_active: true },
  { id: "default", channel: "home_channel", title: "Home", is_default: true, is_active: false },
  { id: "other", channel: "-100123", title: "Archive", is_default: false, is_active: false },
];

function renderDrawer(overrides = {}) {
  const props = {
    channels,
    busy: false,
    error: null,
    onLoad: vi.fn(),
    onMakeDefault: vi.fn(),
    onRemove: vi.fn(),
    onAdd: vi.fn(),
    onClose: vi.fn(),
    ...overrides,
  };
  return { props, ...render(<ChannelDrawer {...props} />) };
}

function findRow(title) {
  return screen.getByText(title).closest(".channel-drawer-item");
}

describe("ChannelDrawer", () => {
  test("renders channel details and badges", () => {
    renderDrawer();

    expect(findRow("Current")).toHaveTextContent("current_channel");
    expect(within(findRow("Current")).getByText("Active")).toBeInTheDocument();
    expect(within(findRow("Home")).getByText("Default")).toBeInTheDocument();
    expect(findRow("Archive")).toHaveTextContent("-100123");
  });

  test("shows only actions allowed for each row", () => {
    renderDrawer();

    expect(within(findRow("Current")).queryByRole("button", { name: "Load" })).not.toBeInTheDocument();
    expect(within(findRow("Current")).queryByRole("button", { name: "Remove" })).not.toBeInTheDocument();
    expect(within(findRow("Current")).getByRole("button", { name: "Make default" })).toBeInTheDocument();
    expect(within(findRow("Home")).queryByRole("button", { name: "Make default" })).not.toBeInTheDocument();
    expect(within(findRow("Home")).queryByRole("button", { name: "Remove" })).not.toBeInTheDocument();
    expect(within(findRow("Archive")).getAllByRole("button")).toHaveLength(3);
  });

  test("requires confirmation before loading a channel", () => {
    const { props } = renderDrawer();
    fireEvent.click(within(findRow("Archive")).getByRole("button", { name: "Load" }));

    expect(screen.getByText(/Switching wipes the cache/)).toBeInTheDocument();
    expect(props.onLoad).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    expect(props.onLoad).toHaveBeenCalledWith("other");
  });

  test("cancels loading without firing onLoad", () => {
    const { props } = renderDrawer();
    fireEvent.click(within(findRow("Archive")).getByRole("button", { name: "Load" }));
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

    expect(props.onLoad).not.toHaveBeenCalled();
    expect(screen.queryByText(/Switching wipes the cache/)).not.toBeInTheDocument();
  });

  test("adds a trimmed non-empty channel", () => {
    const { props } = renderDrawer();
    const input = screen.getByLabelText("Channel username or ID");

    fireEvent.change(input, { target: { value: "   @new_channel   " } });
    fireEvent.click(screen.getByRole("button", { name: "Add" }));
    expect(props.onAdd).toHaveBeenCalledWith("@new_channel");

    fireEvent.change(input, { target: { value: "   " } });
    fireEvent.click(screen.getByRole("button", { name: "Add" }));
    expect(props.onAdd).toHaveBeenCalledOnce();
  });

  test("disables mutation actions while busy", () => {
    renderDrawer({ busy: true });

    expect(within(findRow("Archive")).getAllByRole("button").every((button) => button.disabled)).toBe(true);
    expect(screen.getByRole("button", { name: "Add" })).toBeDisabled();
    expect(screen.getByLabelText("Channel username or ID")).toBeDisabled();
  });

  test("renders an inline error and closes from the close button", () => {
    const { props } = renderDrawer({ error: "Could not add channel" });

    expect(screen.getByRole("alert")).toHaveTextContent("Could not add channel");
    fireEvent.click(screen.getByRole("button", { name: "Close channels" }));
    expect(props.onClose).toHaveBeenCalledOnce();
  });
});
