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

// URL builders for media the <video>/<img> elements load directly.
// The browser attaches the session cookie itself (same-site request).
export function streamUrl(id) {
  return `${BASE}/stream/${id}`;
}

export function thumbUrl(id) {
  return `${BASE}/thumb/${id}`;
}
