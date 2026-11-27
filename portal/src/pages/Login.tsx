import { useState } from "react";

// Production: redirect to Keycloak (OIDC, authorization code + PKCE) and store the access token. Step-up (fresh MFA) is
// requested by re-running the flow with `max_age=300` when the API answers `step_up_required`.
// Development: paste a token minted by `scripts/seed_dev.py`.
export default function Login({ onToken, error }: { onToken: (t: string) => void; error: string }) {
  const [t, setT] = useState("");
  return (<main style={{ maxWidth: 520 }}><h1>AdvisorGro</h1>
    <div className="card"><label htmlFor="tok">Access token (development sign-in)</label>
      <textarea id="tok" rows={5} value={t} onChange={e => setT(e.target.value.trim())} placeholder="Paste a token from scripts/seed_dev.py" />
      {error && <p className="err" role="alert">{error}</p>}
      <p><button disabled={!t} onClick={() => onToken(t)}>Sign in</button></p>
      <p className="mute">In production this page redirects to your firm's single sign-on (MFA required).</p></div></main>);
}
