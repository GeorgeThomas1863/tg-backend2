import { useState } from "react";
import { postLogin } from "../api/client";
import flagUrl from "../assets/freedom1.jpg";

// Password form shown when the backend answers 401. Posts the password;
// on success the session cookie is set and onSuccess() tells the caller
// to refetch. Knows nothing about videos.
export function PasswordGate({ onSuccess }) {
  const [pw, setPw] = useState("");
  const [message, setMessage] = useState(null);
  const [submitting, setSubmitting] = useState(false);

  async function submitPassword(event) {
    event.preventDefault();
    if (!pw || submitting) return;

    setSubmitting(true);
    setMessage(null);
    const result = await postLogin(pw);
    setSubmitting(false);

    if (!result.success) {
      setMessage(result.message);
      return;
    }

    onSuccess();
  }

  return (
    <>
      <img className="pw-background" src={flagUrl} alt="" />
      <form className="pw-form" onSubmit={submitPassword}>
        <label className="pw-label" htmlFor="pw-input">
          Enter the site password
        </label>
        <input id="pw-input" type="password" className="pw-input" value={pw} onChange={(event) => setPw(event.target.value)} autoFocus />
        <button className="pw-button" type="submit" disabled={submitting}>
          {submitting ? "Checking…" : "Submit"}
        </button>
        {message && <div className="pw-message">{message}</div>}
      </form>
    </>
  );
}
