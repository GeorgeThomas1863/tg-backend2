import { useRef, useState } from "react";

export function TelegramAuthDrawer({ status, busy, error, onSendCode, onSubmitCode, onSubmitPassword, onLogout, onClose }) {
  const [phone, setPhone] = useState("");
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [confirmingLogout, setConfirmingLogout] = useState(false);

  return (
    <aside className="telegram-auth-drawer channel-drawer" aria-label="Telegram account">
      <div className="channel-drawer-title">
        <span>Telegram account</span>
        <button className="channel-drawer-close" type="button" aria-label="Close Telegram account" onClick={onClose}>×</button>
      </div>
      {status?.authorized
        ? <AuthorizedAccount status={status} busy={busy} confirming={confirmingLogout} onConfirm={() => setConfirmingLogout(true)} onCancel={() => setConfirmingLogout(false)} onLogout={onLogout} />
        : <LoginForm step={status?.pending_step} phone={phone} setPhone={setPhone} code={code} setCode={setCode} password={password} setPassword={setPassword} showPassword={showPassword} setShowPassword={setShowPassword} busy={busy} onSendCode={onSendCode} onSubmitCode={onSubmitCode} onSubmitPassword={onSubmitPassword} />}
      {error && <div className="telegram-auth-error channel-drawer-error" role="alert">{error}</div>}
    </aside>
  );
}

function AuthorizedAccount({ status, busy, confirming, onConfirm, onCancel, onLogout }) {
  const identity = status.user?.username || status.user?.phone || "Telegram account";
  return (
    <section className="telegram-auth-account">
      <p className="telegram-auth-identity">{identity}</p>
      {!confirming && <button className="telegram-auth-action" type="button" disabled={busy} onClick={onConfirm}>Log out</button>}
      {confirming && (
        <div className="channel-drawer-confirm">
          <p>Log out of this Telegram account?</p>
          <div className="channel-drawer-actions">
            <button type="button" disabled={busy} onClick={onLogout}>Continue</button>
            <button type="button" disabled={busy} onClick={onCancel}>Cancel</button>
          </div>
        </div>
      )}
    </section>
  );
}

function LoginForm(props) {
  if (props.step === "code") return <CodeForm {...props} />;
  if (props.step === "password") return <PasswordForm {...props} />;
  return <PhoneForm {...props} />;
}

function PhoneForm({ phone, setPhone, busy, onSendCode }) {
  const submitting = useRef(false);
  const submit = (event) => {
    event.preventDefault();
    const value = phone.trim();
    if (!value || busy || submitting.current) return;
    submitting.current = true;
    Promise.resolve(onSendCode(value)).finally(() => { submitting.current = false; });
  };
  return <form className="telegram-auth-form" onSubmit={submit}><label htmlFor="telegram-phone">Phone number</label><input id="telegram-phone" className="channel-drawer-input" type="tel" value={phone} onChange={(event) => setPhone(event.target.value)} disabled={busy} autoFocus /><button className="telegram-auth-action" type="submit" disabled={busy}>Send code</button></form>;
}

function CodeForm({ code, setCode, phone, busy, onSubmitCode }) {
  const submitting = useRef(false);
  const submit = (event) => {
    event.preventDefault();
    const value = code.trim();
    if (!value || busy || submitting.current) return;
    submitting.current = true;
    Promise.resolve(onSubmitCode(value)).finally(() => { submitting.current = false; });
  };
  return <form className="telegram-auth-form" onSubmit={submit}><p>Enter the code sent{phone.trim() ? ` to ${phone.trim()}` : " to your phone"}.</p><label htmlFor="telegram-code">Login code</label><input id="telegram-code" className="channel-drawer-input" inputMode="numeric" value={code} onChange={(event) => setCode(event.target.value)} disabled={busy} autoFocus /><button className="telegram-auth-action" type="submit" disabled={busy}>Verify code</button></form>;
}

function PasswordForm({ password, setPassword, showPassword, setShowPassword, busy, onSubmitPassword }) {
  const submitting = useRef(false);
  const submit = (event) => {
    event.preventDefault();
    if (!password || busy || submitting.current) return;
    submitting.current = true;
    Promise.resolve(onSubmitPassword(password)).finally(() => { submitting.current = false; });
  };
  return (
    <form className="telegram-auth-form" onSubmit={submit}>
      <label htmlFor="telegram-password">2FA password</label>
      <div className="telegram-password-wrapper"><input id="telegram-password" className="channel-drawer-input" type={showPassword ? "text" : "password"} value={password} onChange={(event) => setPassword(event.target.value)} disabled={busy} autoFocus /><button type="button" aria-label={showPassword ? "Hide password" : "Show password"} disabled={busy} onClick={() => setShowPassword((current) => !current)}>{showPassword ? "Hide" : "Show"}</button></div>
      <button className="telegram-auth-action" type="submit" disabled={busy}>Log in</button>
    </form>
  );
}
