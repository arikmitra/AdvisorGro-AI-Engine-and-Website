import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { api, hasToken, setToken } from "./api";
import Login from "./pages/Login";
import Campaigns from "./pages/Campaigns";
import CampaignDetail from "./pages/CampaignDetail";
import ShieldPage from "./pages/Shield";
import Leads from "./pages/Leads";
import Activity from "./pages/Activity";
import ReviewQueue from "./pages/ReviewQueue";
import "./styles.css";

type Me = { user_id: string; roles: string[]; staff: boolean; tenant_id: string | null };

function useHash(): string {
  const [h, setH] = useState(location.hash.slice(1) || "/campaigns");
  useEffect(() => { const f = () => setH(location.hash.slice(1) || "/campaigns"); addEventListener("hashchange", f); return () => removeEventListener("hashchange", f); }, []);
  return h;
}

function App() {
  const [me, setMe] = useState<Me | null>(null);
  const [err, setErr] = useState("");
  const route = useHash();
  useEffect(() => { if (hasToken()) api<Me>("/v1/me").then(setMe).catch(e => { setErr(e.message); setToken(""); }); }, []);
  if (!me) return <Login error={err} onToken={(t) => { setToken(t); location.reload(); }} />;
  const can = (...r: string[]) => r.some(x => me.roles.includes(x));
  const nav: [string, string, boolean][] = [["/campaigns", "Campaigns", !me.staff], ["/shield", "Shield", !me.staff], ["/leads", "Leads", !me.staff],
    ["/review", "Review queue", me.staff ? can("reviewer") : can("cco")], ["/activity", "Activity", !me.staff && can("firm_admin", "cco", "read_only")]];
  let page = <p>Not found</p>;
  const m = route.match(/^\/campaigns\/(.+)$/);
  if (m) page = <CampaignDetail id={m[1]} roles={me.roles} />;
  else if (route === "/campaigns") page = <Campaigns roles={me.roles} />;
  else if (route === "/shield") page = <ShieldPage />;
  else if (route === "/leads") page = <Leads />;
  else if (route === "/review") page = <ReviewQueue staff={me.staff} />;
  else if (route === "/activity") page = <Activity />;
  return (<><header><strong>AdvisorGro</strong><nav>{nav.filter(n => n[2]).map(([h, l]) => <a key={h} href={"#" + h} aria-current={route.startsWith(h)}>{l}</a>)}</nav>
    <span className="who">{me.roles.join(", ")} <button className="link" onClick={() => { setToken(""); location.reload(); }}>Sign out</button></span></header><main>{page}</main></>);
}
createRoot(document.getElementById("root")!).render(<StrictMode><App /></StrictMode>);
