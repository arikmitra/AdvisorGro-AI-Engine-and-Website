import { useState } from "react";
import { Campaign, Flag } from "../api";

type V = NonNullable<Campaign["version"]>;
const FlagList = ({ title, items, cls }: { title: string; items: Flag[]; cls: string }) => items.length ? (<><h4>{title}</h4><ul>{items.map((f, i) => <li key={i}><span className={"pill " + cls}>{f.category ?? f.title}</span> <q>{f.snippet}</q><br /><span className="mute">{f.reason}</span></li>)}</ul></>) : null;

// The CCO approves exactly what is rendered here: the page below is the stored artefact whose hash is sent with the decision.
export default function Approval({ c, v, canApprove, onDecide }: { c: Campaign; v: V; canApprove: boolean; onDecide: (d: string, comment: string) => void }) {
  const [comment, setComment] = useState("");
  const pre = v.precheck;
  const g = v.creative_set?.google, pending = c.state === "PendingCCO";
  return (<div className="grid"><div><h2>What the prospect will see</h2>
      {v.page_html ? <iframe title="Landing page preview" sandbox="" srcDoc={v.page_html} /> : <p className="mute">No preview.</p>}
      {g && <div className="card"><h3>Google ad</h3><ul>{g.headlines.map((h: string) => <li key={h}>{h}</li>)}</ul><p className="mute">{g.descriptions.join(" · ")}</p></div>}
      {v.creative_set?.followup && <div className="card"><h3>Follow-up emails</h3>{v.creative_set.followup.emails.map((e: any, i: number) => <details key={i}><summary>{e.subject}</summary><p>{e.body}</p></details>)}
        <p className="mute">SMS: {v.creative_set.followup.sms.join(" / ")}</p></div>}</div>
    <div><div className="card"><h3>Pre-check (flags only, you decide)</h3>
      {!pre ? <p className="mute">Not run.</p> : <>
        <p><span className={"pill " + (pre.status === "clear" ? "good" : "warn")}>{pre.status === "clear" ? "No flags" : "Flags raised"}</span>{" "}
          {pre.ai_review === "missing" && <span className="pill bad">AI review missing</span>}</p>
        {pre.note && <p className="err">{pre.note}</p>}
        <FlagList title="Material" items={pre.material} cls="bad" /><FlagList title="Minor" items={pre.minor} cls="warn" />
        {pre.missing_disclosures.length > 0 && <p className="err">{pre.missing_disclosures.join("; ")}</p>}</>}</div>
      <div className="card"><h3>Content hash</h3><code>{v.content_hash}</code><p className="mute">Your approval is recorded against this exact hash. Any edit creates a new version that needs a new approval.</p></div>
      {pending && canApprove ? <div className="card"><h3>Your decision</h3><label>Comment (kept in the activity log)</label><textarea rows={3} value={comment} onChange={e => setComment(e.target.value)} />
          <p><button onClick={() => onDecide("approve", comment)}>Approve this version</button>{" "}<button className="sec" onClick={() => onDecide("request_changes", comment)}>Request changes</button>{" "}
          <button className="danger" onClick={() => onDecide("reject", comment)}>Reject</button></p>
          <p className="mute">Approval needs a fresh multi-factor sign-in. You cannot approve a version you drafted.</p></div>
        : pending && <p className="mute">Waiting for the firm's CCO (or named deputy) to decide.</p>}</div></div>);
}
