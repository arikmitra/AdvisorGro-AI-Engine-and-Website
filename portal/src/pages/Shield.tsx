import { useEffect, useState } from "react";
import { api } from "../api";

export default function ShieldPage() {
  const [runs, setRuns] = useState<any[]>([]); const [alerts, setAlerts] = useState<any[]>([]); const [err, setErr] = useState("");
  useEffect(() => { api("/v1/shield/runs").then(setRuns).catch(e => setErr(e.message)); api("/v1/shield/alerts").then(setAlerts).catch(() => {}); }, []);
  return (<><h1>Shield</h1>{err && <p className="err">{err}</p>}
    <h2>Alerts</h2>{alerts.length === 0 ? <p className="mute">No alerts. Alerts are raised only from complete, verified runs.</p> :
      <table><tbody>{alerts.map(a => <tr key={a.id}><td>{a.payload.title ?? a.kind}</td><td className="mute">{a.payload.detail}</td><td>{new Date(a.at).toLocaleDateString()}</td></tr>)}</tbody></table>}
    <h2>Runs</h2><table><thead><tr><th>Domain</th><th>Trigger</th><th>State</th><th>Score</th></tr></thead><tbody>
      {runs.map(r => <tr key={r.id}><td>{r.domain}</td><td>{r.trigger}</td><td>{r.state}</td><td>{r.score ?? ""}{r.partial ? "+" : ""}</td></tr>)}</tbody></table></>);
}
