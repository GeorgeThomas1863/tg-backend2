import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, test, vi } from "vitest";
import {
  activateChannel,
  addChannel,
  fetchChannels,
  removeChannel,
  setDefaultChannel,
} from "../api/client";
import { useChannels } from "./useChannels";

vi.mock("../api/client", () => ({
  activateChannel: vi.fn(),
  addChannel: vi.fn(),
  fetchChannels: vi.fn(),
  removeChannel: vi.fn(),
  setDefaultChannel: vi.fn(),
}));

const CHANNELS = [
  { id: "1", channel: "news", title: "News", is_default: true, is_active: false },
  { id: "2", channel: "clips", title: "Clips", is_default: false, is_active: true },
];

beforeEach(() => {
  fetchChannels.mockReset();
  addChannel.mockReset();
  setDefaultChannel.mockReset();
  activateChannel.mockReset();
  removeChannel.mockReset();
});

describe("useChannels", () => {
  test("fetches channels and derives the active channel", async () => {
    fetchChannels.mockResolvedValue({ channels: CHANNELS });

    const { result } = renderHook(() => useChannels());

    expect(result.current.loading).toBe(true);
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.channels).toEqual(CHANNELS);
    expect(result.current.active).toEqual(CHANNELS[1]);
    expect(result.current.error).toBeNull();
  });

  test("uses null when no channel is active", async () => {
    fetchChannels.mockResolvedValue({ channels: [CHANNELS[0]] });
    const { result } = renderHook(() => useChannels());

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.active).toBeNull();
  });

  test("surfaces an initial fetch failure", async () => {
    fetchChannels.mockRejectedValue(new Error("HTTP 500"));
    const { result } = renderHook(() => useChannels());

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.error).toBe("HTTP 500");
    expect(result.current.channels).toEqual([]);
  });

  test("refresh re-fetches the list", async () => {
    fetchChannels
      .mockResolvedValueOnce({ channels: [] })
      .mockResolvedValueOnce({ channels: CHANNELS });
    const { result } = renderHook(() => useChannels());
    await waitFor(() => expect(result.current.loading).toBe(false));

    await act(() => result.current.refresh());

    await waitFor(() => expect(result.current.channels).toEqual(CHANNELS));
    expect(fetchChannels).toHaveBeenCalledTimes(2);
  });

  test.each([
    ["add", addChannel, "new-channel"],
    ["makeDefault", setDefaultChannel, "1"],
    ["activate", activateChannel, "1"],
    ["remove", removeChannel, "1"],
  ])("%s calls its client operation and refreshes after success", async (method, operation, input) => {
    fetchChannels
      .mockResolvedValueOnce({ channels: [] })
      .mockResolvedValueOnce({ channels: CHANNELS });
    const response = { success: true, message: "Done" };
    operation.mockResolvedValue(response);
    const { result } = renderHook(() => useChannels());
    await waitFor(() => expect(result.current.loading).toBe(false));

    let returned;
    await act(async () => {
      returned = await result.current[method](input);
    });

    expect(returned).toEqual(response);
    expect(operation).toHaveBeenCalledWith(input);
    expect(fetchChannels).toHaveBeenCalledTimes(2);
    expect(result.current.channels).toEqual(CHANNELS);
  });

  test("surfaces success:false and does not refresh", async () => {
    fetchChannels.mockResolvedValue({ channels: CHANNELS });
    const response = { success: false, message: "Channel is active" };
    removeChannel.mockResolvedValue(response);
    const { result } = renderHook(() => useChannels());
    await waitFor(() => expect(result.current.loading).toBe(false));

    let returned;
    await act(async () => {
      returned = await result.current.remove("2");
    });

    expect(returned).toEqual(response);
    expect(result.current.error).toBe("Channel is active");
    expect(fetchChannels).toHaveBeenCalledTimes(1);
  });

  test("rejects a second mutation while one is in flight", async () => {
    fetchChannels.mockResolvedValue({ channels: CHANNELS });
    let resolveActivate;
    activateChannel.mockReturnValue(
      new Promise((resolve) => {
        resolveActivate = resolve;
      }),
    );
    const { result } = renderHook(() => useChannels());
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.busy).toBe(false);

    let first;
    act(() => {
      first = result.current.activate("1");
    });
    await waitFor(() => expect(result.current.busy).toBe(true));

    let second;
    await act(async () => {
      second = await result.current.makeDefault("1");
    });

    expect(second).toEqual({
      success: false,
      message: "Another channel action is still running",
    });
    expect(setDefaultChannel).not.toHaveBeenCalled();

    await act(async () => {
      resolveActivate({ success: true, message: "Loaded" });
      await first;
    });
    expect(result.current.busy).toBe(false);
    expect(activateChannel).toHaveBeenCalledTimes(1);
  });

  test("surfaces a rejected mutation and does not refresh", async () => {
    fetchChannels.mockResolvedValue({ channels: CHANNELS });
    activateChannel.mockRejectedValue(new Error("Failed to fetch"));
    const { result } = renderHook(() => useChannels());
    await waitFor(() => expect(result.current.loading).toBe(false));

    let returned;
    await act(async () => {
      returned = await result.current.activate("1");
    });

    expect(returned).toEqual({ success: false, message: "Failed to fetch" });
    expect(result.current.error).toBe("Failed to fetch");
    expect(fetchChannels).toHaveBeenCalledTimes(1);
  });
});
