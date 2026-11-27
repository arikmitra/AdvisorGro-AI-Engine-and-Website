// Drives the REAL page (index.html + assets/advisorgro-api.js) in jsdom against a RUNNING API. No browser needed.
//   terminal 1 (backend):  uvicorn advisorgro.api.main:app_factory --factory --port 8000      (fakes are fine)
//   terminal 2 (website):  python -m http.server 8081
//   terminal 3 (website/tests):  npm install && npm run check
// Env: SITE (default http://localhost:8081/)
const { JSDOM, VirtualConsole } = require("jsdom");
const SITE = process.env.SITE || "http://localhost:8081/";
let failed = 0;
const check = (name, ok, detail = "") => { console.log((ok ? "PASS " : "FAIL ") + name + (!ok && detail ? `   [${detail}]` : "")); failed += ok ? 0 : 1; };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

(async () => {
  const vc = new VirtualConsole();  // web-font fetches fail offline; that is irrelevant here
  vc.on("jsdomError", (e) => { if (!/fonts\.(googleapis|gstatic)|Could not load/.test(String(e.message))) console.log("PAGE ERROR:", e.message); });
  const dom = await JSDOM.fromURL(SITE, { runScripts: "dangerously", resources: "usable", pretendToBeVisual: true, virtualConsole: vc,
    beforeParse(w) { w.fetch = (u, o) => fetch(u, o); w.HTMLElement.prototype.scrollIntoView = function () {}; } });
  const w = dom.window, d = w.document;
  await new Promise((r) => w.addEventListener("load", r));
  await sleep(3500); // the client attaches its handlers after its first status check
  const form = d.getElementById("audit-form");
  const out = () => d.querySelector("[data-advisorgro=audit-results]");
  check("audit form is wired to the engine client", form && form.getAttribute("data-advisorgro") === "audit" && !!out());
  check("API base comes from site.config.js", !!w.ADVISORGRO_API, String(w.ADVISORGRO_API));
  const fill = (site, email, consent) => {
    form.elements["website"].value = site; form.elements["email"].value = email; form.elements["area"].value = "Austin, Texas";
    form.elements["firm"].value = "Browser Test Firm"; form.elements["consent"].checked = consent;
  };
  const submit = async (ms) => { form.dispatchEvent(new w.Event("submit", { bubbles: true, cancelable: true })); await sleep(ms); };

  fill("browsertest-" + Date.now() + ".com", "t@browsertest.com", false);
  await submit(2500);
  const errs = [...d.querySelectorAll(".agro-field-err")].map((e) => e.textContent);
  check("missing consent shows an inline error and flags the checkbox", errs.some((e) => /agree/i.test(e)) && form.elements["consent"].getAttribute("aria-invalid") === "true", JSON.stringify(errs));
  check("no results are shown when the form is invalid", out().textContent.trim() === "");

  fill("browsertest-" + Date.now() + ".com", "t@browsertest.com", true);
  await submit(7000);
  const text = out().textContent.replace(/\s+/g, " ");
  check("a completed audit renders a score out of 100", /\/\s*100/.test(text) && /five checks/i.test(text), text.slice(0, 160));
  check("results show the masked email the copy was sent to", /t\*\*\*@browsertest\.com/.test(text), text.slice(0, 160));
  check("the form is locked after a successful submit (no double submit)", form.elements["email"].disabled === true);
  w.close();
  console.log(failed ? `\n${failed} FAILED` : "\nALL PASSED");
  process.exit(failed ? 1 : 0);
})();
