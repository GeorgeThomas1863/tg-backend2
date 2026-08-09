import { useEffect, useRef, useState } from "react";
import {
  activateChannel,
  addChannel,
  fetchChannels,
  removeChannel,
  setDefaultChannel,
} from "../api/client";

export function useChannels() {
  const [channels, setChannels] = useState([]);
  const [loading, setLoading] = useState(true);
  const [mutating, setMutating] = useState(false);
  const [error, setError] = useState(null);
  const mutationInFlight = useRef(false);

  useEffect(() => {
    let cancelled = false;

    fetchChannelList()
      .then((nextChannels) => {
        if (cancelled) return;
        setChannels(nextChannels);
        setError(null);
        setLoading(false);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err.message);
        setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, []);

  const refresh = async () => {
    try {
      const nextChannels = await fetchChannelList();
      setChannels(nextChannels);
      setError(null);
    } catch (err) {
      setError(err.message);
    }
  };

  const runMutation = async (operation, value) => {
    if (mutationInFlight.current) {
      return { success: false, message: "Another channel action is still running" };
    }
    mutationInFlight.current = true;
    setMutating(true);
    try {
      return await executeMutation(operation, value);
    } finally {
      mutationInFlight.current = false;
      setMutating(false);
    }
  };

  const executeMutation = async (operation, value) => {
    let result;
    try {
      result = await operation(value);
    } catch (err) {
      result = { success: false, message: err.message };
    }

    if (!result.success) {
      setError(result.message);
      return result;
    }

    await refresh();
    return result;
  };

  let active = null;
  for (const channel of channels) {
    if (!channel.is_active) continue;
    active = channel;
    break;
  }

  return {
    channels,
    active,
    loading,
    busy: loading || mutating,
    error,
    refresh,
    add: (raw) => runMutation(addChannel, raw),
    makeDefault: (id) => runMutation(setDefaultChannel, id),
    activate: (id) => runMutation(activateChannel, id),
    remove: (id) => runMutation(removeChannel, id),
  };
}

async function fetchChannelList() {
  const data = await fetchChannels();
  return data.channels;
}
