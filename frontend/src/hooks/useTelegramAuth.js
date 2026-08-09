import { useEffect, useRef, useState } from "react";
import {
  fetchTelegramAuthStatus,
  postTelegramCode,
  postTelegramLogout,
  postTelegramPassword,
  postTelegramPhone,
} from "../api/client";

const CONCURRENT_MESSAGE = "Another Telegram action is still running";

export function useTelegramAuth() {
  const [status, setStatus] = useState(null);
  const [loading, setLoading] = useState(true);
  const [mutating, setMutating] = useState(false);
  const [error, setError] = useState(null);
  const mutationInFlight = useRef(false);

  useEffect(() => {
    let cancelled = false;
    fetchTelegramAuthStatus()
      .then((nextStatus) => {
        if (cancelled) return;
        setStatus(nextStatus);
        setError(null);
        setLoading(false);
      })
      .catch((requestError) => {
        if (cancelled) return;
        setError(requestError.message);
        setLoading(false);
      });
    return () => { cancelled = true; };
  }, []);

  const refresh = async () => {
    try {
      const nextStatus = await fetchTelegramAuthStatus();
      setStatus(nextStatus);
      setError(null);
      return nextStatus;
    } catch (requestError) {
      setError(requestError.message);
      return null;
    }
  };

  const runMutation = async (operation, value) => {
    if (mutationInFlight.current) return { success: false, message: CONCURRENT_MESSAGE };
    mutationInFlight.current = true;
    setMutating(true);
    setError(null);
    try {
      const result = await operation(value);
      if (!result.success) {
        setError(result.message);
        return result;
      }
      await refresh();
      return result;
    } catch (requestError) {
      const result = { success: false, message: requestError.message };
      setError(result.message);
      return result;
    } finally {
      mutationInFlight.current = false;
      setMutating(false);
    }
  };

  return {
    status,
    loading,
    mutating,
    busy: loading || mutating,
    error,
    refresh,
    sendCode: (phone) => runMutation(postTelegramPhone, phone),
    submitCode: (code) => runMutation(postTelegramCode, code),
    submitPassword: (password) => runMutation(postTelegramPassword, password),
    logout: () => runMutation(postTelegramLogout),
  };
}
