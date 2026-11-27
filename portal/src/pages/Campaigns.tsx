import { useEffect, useState } from "react";
import { api, Campaign } from "../api";

const tone = (s: string) => (s === "Live" ? "good" : s === "Paused" || s === "Rejected" ? "bad" : s === "PendingCCO" ? "warn" : "");

export default function Campaigns({ roles }: { roles: string[] }) {
  const [rows, setRows] = useState<Campaign[]>([]);
  const [firms, setFirms] = useState<{ id: string; name: string }[]>([]);
  const [f, setF] = useState({ firm_id: "", offer_id: "retirement_readiness", platform: "google", monthly: 3000, ceiling: 5000 });
  const [err, setErr] = useState("");
  const load = () => api<Campaign[]>("/v1/campaigns").then(setRows).catch(e => setErr(e.message));
  useEffect(() => { load(); api("/v1/firms").then(x => { setFirms(x); setF(p => ({ ...p, firm_id: x[0]?.id ?? "" })); }).catch(() => {}); }, []);
  const create = async () => {
    try { const c = await api<Campaign>("/v1/campaigns", { method: "POST", json: { firm_id: f.firm_id, offer_id: f.offer_id, platform: f.platform, monthly_budget_cents: f.monthly * 100, budget_ceiling_cents: f.ceiling * 100 } }); location.hash = "/campaigns/" + c.id; }
    catch (e: any) { setErr(e.message); }
  };
  return (<><h1>Campaigns</h1>{err && <p className="err" role="alert">{err}</p>}
    <table><thead><tr><th>Offer</th><th>Platform</th><th>State</th><th>Version</th><th>Budget / month</th></tr></thead><tbody>
      {rows.map(c => <tr key={c.id}><td><a href={"#/campaigns/" + c.id}>{c.offer_id.replaceAll("_", " ")}</a></td><td>{c.platform}</td>
        <td><span className={"pill " + tone(c.state)}>{c.state}</span></td><td>{c.current_version}</td><td>${(c.monthly_budget_cents / 100).toLocaleString()}</td></tr>)}</tbody></table>
    {roles.some(r => ["marketing", "firm_admin"].includes(r)) && <div className="card"><h2>New campaign</h2>
      <label>Firm</label><select value={f.firm_id} onChange={e => setF({ ...f, firm_id: e.target.value })}>{firms.map(x => <option key={x.id} value={x.id}>{x.name}</option>)}</select>
      <label>Offer</label><select value={f.offer_id} onChange={e => setF({ ...f, offer_id: e.target.value })}>
        {["retirement_readiness", "tax_aware_investing", "business_owner_exit", "inheritance_transition", "retirement_income", "second_opinion"].map(o => <option key={o} value={o}>{o.replaceAll("_", " ")}</option>)}</select>
      <label>Platform</label><select value={f.platform} onChange={e => setF({ ...f, platform: e.target.value })}><option>google</option><option>meta</option></select>
      <label>Monthly budget ($)</label><input type="number" value={f.monthly} onChange={e => setF({ ...f, monthly: +e.target.value })} />
      <label>Budget ceiling ($): the most the system may ever spend</label><input type="number" value={f.ceiling} onChange={e => setF({ ...f, ceiling: +e.target.value })} />
      <p><button onClick={create} disabled={!f.firm_id}>Create draft</button></p></div>}</>);
}
