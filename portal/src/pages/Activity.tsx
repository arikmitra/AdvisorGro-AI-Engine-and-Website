import { useEffect, useState } from "react";
import { api } from "../api";

export default function Activity() {
  const [rows, setRows] = useState<any[]>([]); const [err, setErr] = useState("");
  useEffect(() => { api("/v1/activity").then(setRows).catch(e => setErr(e.message)); }, []);
  return (<><h1>Activity log</h1><p className="mute">Append-only and hash-chained. Every approval records who, what and the content hash.</p>{err && <p className="err">{err}</p>}
    <table><thead><tr><th>#</th><th>When</th><th>Actor</th><th>Action</th><th>Resource</th></tr></thead><tbody>
      {rows.map(a => <tr key={a.seq}><td>{a.seq}</td><td>{new Date(a.at).toLocaleString()}</td><td><code>{a.actor.slice(0, 8)}</code></td><td>{a.action}</td><td><code>{a.resource}</code></td></tr>)}</tbody></table></>);
}
