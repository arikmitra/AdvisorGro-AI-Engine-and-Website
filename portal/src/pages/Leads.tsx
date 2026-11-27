import { useEffect, useState } from "react";
import { api } from "../api";

export default function Leads() {
  const [rows, setRows] = useState<any[]>([]); const [sla, setSla] = useState<any>(null); const [err, setErr] = useState("");
  useEffect(() => { api("/v1/leads").then(setRows).catch(e => setErr(e.message)); api("/v1/leads/sla").then(setSla).catch(() => {}); }, []);
  return (<><h1>Leads</h1>{err && <p className="err">{err}</p>}{sla && <p className="mute">First reply within 90 seconds: {sla.within_sla} of {sla.first_contacts} this week.</p>}
    <p className="mute">Contact details are masked for your role where the policy requires it.</p>
    <table><thead><tr><th>Name</th><th>Email</th><th>Phone</th><th>State</th><th>Received</th></tr></thead><tbody>
      {rows.map(l => <tr key={l.id}><td>{l.name}</td><td>{l.email}</td><td>{l.phone}</td><td>{l.state}</td><td>{new Date(l.created_at).toLocaleString()}</td></tr>)}</tbody></table></>);
}
