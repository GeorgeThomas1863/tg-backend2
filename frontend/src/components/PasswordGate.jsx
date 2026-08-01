import { useState } from "react";
import { postLogin } from "../api/client";
import flagUrl from "../assets/freedom1.jpg";

// Password form shown when the backend answers 401. Posts the password;
// on success the session cookie is set and onSuccess() tells the caller
// to refetch. Knows nothing about videos. Styled after the auth pages in
// nork-displayer-1950 / resume-builder: flag background, white card,
// eye-toggled password input, uppercase submit.
export function PasswordGate({ onSuccess }) {
  const [pw, setPw] = useState("");
  const [showPw, setShowPw] = useState(false);
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
      <img className="auth-background-pic" src={flagUrl} alt="" />
      <form className="auth-form-wrapper" onSubmit={submitPassword}>
        <label className="auth-label" htmlFor="pw-input">
          Enter the site password
        </label>
        <div className="password-input-wrapper">
          <input
            id="pw-input"
            type={showPw ? "text" : "password"}
            className="password-input"
            value={pw}
            onChange={(event) => setPw(event.target.value)}
            placeholder="Input the site password here"
            autoFocus
          />
          <button
            type="button"
            className="password-toggle-btn"
            aria-label={showPw ? "Hide password" : "Show password"}
            onClick={() => setShowPw((current) => !current)}
          >
            <EyeIcon closed={!showPw} />
          </button>
        </div>
        <button className="btn-submit" type="submit" disabled={submitting}>
          {submitting ? "Checking…" : "Submit"}
        </button>
        {message && <div className="auth-message">{message}</div>}
      </form>
    </>
  );
}

//---

const EyeIcon = ({ closed }) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" />
    <circle cx="12" cy="12" r="3" />
    {closed && <path d="M2 2l20 20" />}
  </svg>
);
