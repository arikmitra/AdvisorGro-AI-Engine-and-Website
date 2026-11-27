import { useEffect, useState } from "react";
import { api } from "../api";

export default function ReviewQueue({ staff }: { staff: boolean }) {
  const [rows, setRows] = useState<any[]>([]); const [err, setErr] = useState("");
  const load = () => api("/v1/review-queue").then(setRows).catch(e => setErr(e.message));
  useEffect(() => { load(); }, []);
  const decide = async (run: string, decision: string) => { try { await api(`/v1/review-queue/${run}/decision`, { method: "POST", json: { decision } }); load(); } catch (e: any) { setErr(e.message); } };
  return (<><h1>Review queue</h1><p className="mute">{staff ? "Audit reports held for a person to check before they are released." : "Reports with compliance flags wait here for you. Release or withhold."}</p>{err && <p className="err" role="alert">{err}</p>}
    {rows.length === 0 && <p>Nothing waiting.</p>}
    {rows.map(r => <div className="card" key={r.id}><strong>Run {r.run_id.slice(0, 8)}</strong> <span className="pill warn">{r.assigned_role}</span>
      <ul>{r.flags.map((f: any, i: number) => <li key={i}>{f.title}</li>)}</ul>
      <button onClick={() => decide(r.run_id, "release")}>Release</button>{" "}<button className="sec" onClick={() => decide(r.run_id, "withhold")}>Withhold</button></div>)}</>);
}
