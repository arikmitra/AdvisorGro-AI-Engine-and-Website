import { useEffect, useState } from "react";
import { api, ApiError, Campaign } from "../api";
import Approval from "../components/Approval";

export default function CampaignDetail({ id, roles }: { id: string; roles: string[] }) {
  const [c, setC] = useState<Campaign | null>(null);
  const [msg, setMsg] = useState("");
  const load = () => api<Campaign>("/v1/campaigns/" + id).then(setC).catch(e => setMsg(e.message));
  useEffect(() => { load(); }, [id]);
  const act = async (path: string, json: unknown = {}) => {
    setMsg("");
    try { const r = await api(path, { method: "POST", json }); if (r && r.ok === false) setMsg(r.unsupported_claims ? "Fact guard blocked: " + r.unsupported_claims.map((x: any) => x.text).join("; ") : r.error ?? "Failed"); await load(); }
    catch (e) { setMsg(e instanceof ApiError && e.code === "step_up_required" ? "Please sign in again with your second factor, then retry." : (e as Error).message); }
  };
  if (!c) return <p>{msg || "Loading…"}</p>;
  const canDraft = roles.some(r => ["marketing", "firm_admin"].includes(r));
  const canPause = roles.some(r => ["firm_admin", "cco"].includes(r)) || roles.includes("marketing");
  return (<><p><a href="#/campaigns">← Campaigns</a></p><h1>{c.offer_id.replaceAll("_", " ")} <span className="pill">{c.state}</span></h1>
    {c.pause_reason && <p className="err">Paused: {c.pause_reason}</p>}{msg && <p className="err" role="alert">{msg}</p>}
    <p className="mute">Version {c.current_version} · live version {c.live_version ?? "none"} · ceiling ${(c.budget_ceiling_cents / 100).toLocaleString()}/month</p>
    <p>{canDraft && ["Draft", "ChangesRequested", "Live", "Paused", "Approved"].includes(c.state) && <button onClick={() => act(`/v1/campaigns/${id}/submit`, {})}>{c.current_version ? "Generate a new version" : "Generate and submit for CCO review"}</button>}{" "}
      {canPause && c.state === "Live" && <button className="danger" onClick={() => act(`/v1/campaigns/${id}/pause`, { reason: "Paused from portal" })}>{roles.includes("marketing") && !roles.some(r => ["firm_admin", "cco"].includes(r)) ? "Request pause" : "Pause"}</button>}{" "}
      {c.state === "Paused" && roles.some(r => ["firm_admin", "cco"].includes(r)) && <button className="sec" onClick={() => act(`/v1/campaigns/${id}/resume`)}>Resume</button>}</p>
    {c.version && <Approval c={c} v={c.version} canApprove={roles.includes("cco")} onDecide={(decision, comment) => act(`/v1/campaigns/${id}/decision`, { version: c.version!.version, content_hash: c.version!.content_hash, decision, comment })} />}</>);
}
