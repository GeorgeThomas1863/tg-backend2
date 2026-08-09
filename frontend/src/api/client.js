// Single source of truth for the backend URL and how we talk to it.
// VITE_API_BASE is injected by vite.config.js from the repo-root .env:
// an explicit VITE_API_BASE wins, else http://localhost:<BACKEND_PORT>.
// Every request sends the session cookie — the backend gates everything on it.

const BASE = import.meta.env.VITE_API_BASE;

export async function fetchVideos(limit = 50, beforeId = null) {
  const beforeParam = beforeId ? `&before_id=${beforeId}` : "";
  const res = await fetch(`${BASE}/api/videos?limit=${limit}${beforeParam}`, { credentials: "include" });
  if (!res.ok) {
    const error = new Error(`HTTP ${res.status}`);
    error.status = res.status;
    throw error;
  }
  return res.json();
}

export async function fetchCacheStatus() {
  const res = await fetch(`${BASE}/api/cache/status`, { credentials: "include" });
  if (!res.ok) {
    const error = new Error(`HTTP ${res.status}`);
    error.status = res.status;
    throw error;
  }
  return res.json();
}

export async function fetchChannels() {
  const res = await fetch(`${BASE}/api/channels`, { credentials: "include" });
  if (!res.ok) {
    const error = new Error(`HTTP ${res.status}`);
    error.status = res.status;
    throw error;
  }
  return res.json();
}

export async function postLogin(pw) {
  if (!pw) return { success: false, message: "No password provided" };

  try {
    const res = await fetch(`${BASE}/api/auth`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({ pw }),
    });
    if (!res.ok) {
      let data = null;
      try {
        data = await res.json();
      } catch {
        // Some proxy/server errors do not include a JSON response body.
      }
      return { success: false, message: data?.message || `HTTP ${res.status}` };
    }
    return res.json();
  } catch (e) {
    console.log("LOGIN ERROR: " + e.message);
    return { success: false, message: e.message };
  }
}

export async function postCachePaused(paused) {
  try {
    const res = await fetch(`${BASE}/api/cache/paused`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({ paused }),
    });
    if (!res.ok) {
      let data = null;
      try {
        data = await res.json();
      } catch {
        // Some proxy/server errors do not include a JSON response body.
      }
      return { success: false, message: data?.message || `HTTP ${res.status}` };
    }
    return res.json();
  } catch (e) {
    console.log("CACHE PAUSED ERROR: " + e.message);
    return { success: false, message: e.message };
  }
}

export async function postCacheSettings(fields) {
  return mutateChannel("/api/cache/settings", "POST", fields, "CACHE SETTINGS");
}

export async function postCacheClear() {
  return mutateChannel("/api/cache/clear", "POST", null, "CACHE CLEAR");
}

export async function addChannel(raw) {
  return mutateChannel("/api/channels", "POST", { channel: raw }, "ADD CHANNEL");
}

export async function setDefaultChannel(id) {
  return mutateChannel("/api/channels/default", "POST", { id }, "SET DEFAULT CHANNEL");
}

export async function activateChannel(id) {
  return mutateChannel("/api/channels/active", "POST", { id }, "ACTIVATE CHANNEL");
}

export async function removeChannel(id) {
  return mutateChannel(`/api/channels/${id}`, "DELETE", null, "REMOVE CHANNEL");
}

async function mutateChannel(path, method, body, errorLabel) {
  try {
    const options = {
      method,
      headers: { "Content-Type": "application/json" },
      credentials: "include",
    };
    if (body !== null) options.body = JSON.stringify(body);

    const res = await fetch(`${BASE}${path}`, options);
    if (!res.ok) {
      let data = null;
      try {
        data = await res.json();
      } catch {
        // Some proxy/server errors do not include a JSON response body.
      }
      return { success: false, message: data?.message || `HTTP ${res.status}` };
    }
    return res.json();
  } catch (e) {
    console.log(`${errorLabel} ERROR: ${e.message}`);
    return { success: false, message: e.message };
  }
}

// URL builders for media the <video>/<img> elements load directly.
// The browser attaches the session cookie itself (same-site request).
export function streamUrl(id) {
  return `${BASE}/stream/${id}`;
}

export function thumbUrl(id) {
  return `${BASE}/thumb/${id}`;
}
