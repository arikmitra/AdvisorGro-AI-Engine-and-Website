/*! AdvisorGro site <-> engine connector.
 *
 * One script for the whole site. Load it on /audit and /shield (or everywhere; it does nothing on
 * pages that have neither):
 *
 *   <script src="/advisorgro-api.js" data-api="https://api.advisorgrow.com" defer></script>
 *
 * Free audit: finds the audit form, adds a hidden spam trap, and posts the fields to /api/audit.
 *   Mark the form yourself with data-advisorgro="audit" to skip the search. If a field is picked
 *   wrongly, set data-field="website|firm_name|area|crd|contact_name|email|aum|goal" on the control.
 * Shield: renders a live check into <div data-advisorgro="shield-snapshot"></div> and posts to
 *   /api/shield/snapshot.
 * Audit results: after the form is sent the page shows progress, then the full report (all five checks,
 *   the eight Shield rows, the fix list) in <div data-advisorgro="audit-results"></div>, or just below the form.
 *   On load it asks /api/status whether the report can be produced and emailed, and says so plainly if not.
 * Shield report: renders a full report (all five checks, the eight watched areas, the fix list) into
 *   <div data-advisorgro="shield-report" data-src="/sample-shield-report.json"></div>.
 *   A client's emailed link ends in #report=<token>; with a token in the address the report is fetched
 *   from /api/report/<token>, otherwise data-src (a static example) is shown. A reviewer previews an
 *   unpublished report by adding &preview=<admin token> to the link (the hash never leaves the browser).
 * No cookies, no storage, no third-party requests. Everything the visitor typed goes only to data-api.
 */
(function () {
  'use strict';
  var script = document.currentScript;
  var RAW_API = String(window.ADVISORGRO_API || (script && script.getAttribute('data-api')) || '');
  // data-api="same-origin" means: the API is on the same address as this page (the combined MVP server).
  var API = (RAW_API === 'same-origin' || RAW_API === '/' ? location.origin : RAW_API).replace(/\/+$/, '');
  if (!API) { if (window.console) console.warn('[advisorgro] data-api is not set; forms are left as they are.'); return; }

  var AUDIT_PAGE = (script && script.getAttribute('data-audit-url')) || '/audit';
  var CSS = '' +
    '.agro-status{margin:14px 0 0;padding:12px 14px;border-radius:8px;font-size:15px;line-height:1.5;border-left:4px solid var(--agro-accent,#01dc80);background:var(--agro-note-bg,#f4f4f2);color:inherit}' +
    '.agro-status[hidden]{display:none}.agro-status.agro-warn{border-left-color:#854d0e;background:#fef3c7;color:#713f12}.agro-results{margin-top:28px}.agro-status.agro-err{border-left-color:#9b1c1c;background:#fde2e2;color:#7f1d1d}' +
    '.agro-field-err{display:block;margin-top:4px;font-size:13px;color:#9b1c1c}' +
    '.agro-hp{position:absolute!important;left:-10000px!important;width:1px;height:1px;overflow:hidden}' +
    '.agro-snap form{display:flex;gap:10px;flex-wrap:wrap;align-items:flex-end}.agro-snap label{display:block;font-weight:600;margin-bottom:4px}' +
    '.agro-snap input[type=text]{min-width:260px;padding:10px 12px;border:1px solid #c9d1ce;border-radius:8px;font:inherit}' +
    '.agro-snap button{padding:10px 18px;border:0;border-radius:8px;background:var(--agro-accent,#01dc80);color:#0a1614;font:inherit;font-weight:700;cursor:pointer}' +
    '.agro-snap button[disabled]{opacity:.6;cursor:wait}' +
    '.agro-rows{width:100%;border-collapse:collapse;margin-top:16px}.agro-rows th,.agro-rows td{padding:9px 10px;border-bottom:1px solid #dfe2e0;text-align:left;vertical-align:top}' +
    '.agro-pill{display:inline-block;padding:2px 10px;border-radius:10px;font-size:12px;font-weight:700;white-space:nowrap}' +
    '.agro-green{background:#dcfce7;color:#166534}.agro-amber{background:#fef3c7;color:#854d0e}.agro-red{background:#fde2e2;color:#9b1c1c}' +
    '.agro-review,.agro-unverified,.agro-not_run,.agro-manual{background:#eef1ef;color:#3b4a47}.agro-snap .agro-cta{margin-top:14px}' +
    '.agro-pass{background:#dcfce7;color:#166534}.agro-partial{background:#fef3c7;color:#854d0e}.agro-fail{background:#fde2e2;color:#9b1c1c}' +
    '.agro-report{max-width:960px;margin:0 auto}.agro-report h3{margin:.2em 0}.agro-report h4{margin:28px 0 10px;padding-bottom:4px;border-bottom:2px solid var(--agro-accent,#01dc80)}' +
    '.agro-eyebrow{margin:0;font-size:13px;letter-spacing:.04em;text-transform:uppercase;color:#6b7878}' +
    '.agro-score{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap;margin:6px 0}.agro-big{font-size:52px;font-weight:800;line-height:1;color:var(--agro-dark,#144b39)}' +
    '.agro-of{font-size:20px;color:#6b7878}.agro-delta{font-size:14px;font-weight:700}.agro-up{color:#166534}.agro-down{color:#9b1c1c}' +
    '.agro-groups{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:14px}' +
    '.agro-card{border:1px solid #dfe2e0;border-radius:10px;padding:14px;background:var(--agro-card,transparent)}' +
    '.agro-card h5{margin:0;font-size:16px}.agro-cardtop{display:flex;justify-content:space-between;gap:8px;align-items:center}' +
    '.agro-bar{height:10px;border-radius:6px;background:#e8ecea;margin:10px 0 6px;overflow:hidden}.agro-bar i{display:block;height:100%;border-radius:6px}' +
    '.agro-fill-green{background:#01dc80}.agro-fill-amber{background:#f5b301}.agro-fill-red{background:#d64545}.agro-fill-review,.agro-fill-unverified,.agro-fill-not_run{background:#9aa7a3}' +
    '.agro-small{font-size:13px;color:#6b7878}.agro-card details{margin-top:8px}.agro-card summary{cursor:pointer;font-size:14px;font-weight:600}' +
    '.agro-checks{list-style:none;margin:8px 0 0;padding:0}.agro-checks li{padding:7px 0;border-top:1px solid #eef1ef;font-size:14px}' +
    '.agro-working{list-style:none;padding:0;margin:0}.agro-working li{padding:8px 0 8px 28px;position:relative;border-bottom:1px solid #dfe2e0}.agro-working li:before{content:"\\2713";position:absolute;left:4px;color:#0a7d4f;font-weight:700}' +
    '.agro-checks .agro-pill{margin-right:6px}.agro-fixes{padding-left:0;list-style:none;margin:0;counter-reset:fix}' +
    '.agro-fixes li{counter-increment:fix;position:relative;padding:12px 0 12px 38px;border-bottom:1px solid #dfe2e0}' +
    '.agro-fixes li:before{content:counter(fix);position:absolute;left:0;top:12px;width:26px;height:26px;border-radius:50%;background:var(--agro-dark,#144b39);color:#fff;text-align:center;line-height:26px;font-weight:700;font-size:13px}' +
    '.agro-fixes p{margin:6px 0 0}.agro-notice{margin:14px 0;padding:12px 14px;border-radius:8px;border-left:4px solid var(--agro-accent,#01dc80);background:var(--agro-note-bg,#f4f4f2);font-size:15px}' +
    '.agro-report button.agro-print{margin-top:18px;padding:9px 16px;border:1px solid #c9d1ce;border-radius:8px;background:transparent;font:inherit;cursor:pointer}' +
    '@media print{.agro-noprint{display:none!important}.agro-card{break-inside:avoid}.agro-fixes li{break-inside:avoid}}';
  function addCss() {
    if (document.getElementById('agro-css')) return;
    var s = document.createElement('style'); s.id = 'agro-css'; s.textContent = CSS; document.head.appendChild(s);
  }
  function el(tag, attrs, text) {
    var n = document.createElement(tag);
    for (var k in (attrs || {})) n.setAttribute(k, attrs[k]);
    if (text != null) n.textContent = text;
    return n;
  }
  function post(path, body) {
    return fetch(API + path, { method: 'POST', credentials: 'omit', headers: { 'Content-Type': 'application/json', 'Idempotency-Key': (window.crypto && crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) + Math.random()) }, body: JSON.stringify(body) })
      .then(function (r) { return r.json().catch(function () { return {}; }).then(function (j) { return { status: r.status, body: j }; }); });
  }
  var NET_ERR = 'We couldn\u2019t send your request. Please try again in a moment, or contact us.';

  /* ---------------- free audit form ---------------- */
  function labelText(c) {
    var t = '';
    if (c.id) { var l = document.querySelector('label[for="' + c.id.replace(/"/g, '') + '"]'); if (l) t += ' ' + l.textContent; }
    var wrap = c.closest && c.closest('label'); if (wrap) t += ' ' + wrap.textContent;
    return [c.name, c.id, c.placeholder, c.getAttribute('aria-label'), t].join(' ').toLowerCase();
  }
  var RULES = [   // most specific first; each control is used once
    ['email', function (c, h) { return c.type === 'email' || /e-?mail/.test(h); }],
    ['website', function (c, h) { return c.type === 'url' || /web ?site|\burl\b|domain/.test(h); }],
    ['crd', function (c, h) { return /\bcrd\b/.test(h); }],
    ['aum', function (c, h) { return /\baum\b|assets under/.test(h); }],
    ['goal', function (c, h) { return /goal/.test(h); }],
    ['area', function (c, h) { return /city|area|location|serve|region/.test(h); }],
    ['firm_name', function (c, h) { return /firm|company|business|practice/.test(h); }],
    ['contact_name', function (c, h) { return /name/.test(h); }]
  ];
  function mapFields(form) {
    var map = {}, used = [];
    var controls = [].slice.call(form.querySelectorAll('input, select, textarea')).filter(function (c) {
      return !/^(hidden|submit|button|checkbox|radio)$/.test(c.type);
    });
    controls.forEach(function (c) {                                   // explicit data-field wins
      var f = c.getAttribute('data-field'); if (f && !map[f]) { map[f] = c; used.push(c); }
    });
    RULES.forEach(function (r) {
      if (map[r[0]]) return;
      for (var i = 0; i < controls.length; i++) {
        var c = controls[i]; if (used.indexOf(c) > -1) continue;
        if (r[1](c, labelText(c))) { map[r[0]] = c; used.push(c); return; }
      }
    });
    return map;
  }
  function findAuditForm() {
    var f = document.querySelector('form[data-advisorgro="audit"]'); if (f) return f;
    for (var i = 0; i < document.forms.length; i++) {
      var m = mapFields(document.forms[i]);
      if (m.email && m.website && m.firm_name) return document.forms[i];
    }
    return null;
  }
  function selectValue(c) {   // the visible label, not an internal value, so the reviewer reads it as the visitor saw it
    if (c.tagName === 'SELECT') { var o = c.options[c.selectedIndex]; return o && c.selectedIndex > 0 ? o.text : ''; }
    return c.value;
  }
  function prepare(form) {            // built on the first submit, so nothing is added to the page while it hydrates
    if (form.__agroState) return form.__agroState;
    addCss();
    var map = mapFields(form);
    var hp = el('input', { type: 'text', name: 'hp', tabindex: '-1', autocomplete: 'off', 'aria-hidden': 'true' });
    var trap = el('div', { 'class': 'agro-hp', 'aria-hidden': 'true' }); trap.appendChild(hp); form.appendChild(trap);
    var status = el('div', { 'class': 'agro-status', role: 'status', 'aria-live': 'polite', hidden: '' }); form.appendChild(status);
    return (form.__agroState = { map: map, hp: hp, status: status, btn: form.querySelector('[type=submit]') });
  }
  function onSubmit(e) {
    var form = e.target;
    if (!form || form.tagName !== 'FORM' || form !== findAuditForm()) return;
    e.preventDefault(); e.stopImmediatePropagation();                 // this form now goes to the engine, not to any older handler
    var st = prepare(form), map = st.map, hp = st.hp, btn = st.btn;
    function say(msg, err) { st.status.hidden = false; st.status.className = 'agro-status' + (err ? ' agro-err' : ''); st.status.textContent = msg; }
    if (map.website && map.website.value.trim() && !/^https?:\/\//i.test(map.website.value.trim())) map.website.value = 'https://' + map.website.value.trim();   // accept yourfirm.com
    if (!form.noValidate && form.checkValidity && !form.checkValidity()) { form.reportValidity(); return; }
    [].slice.call(form.querySelectorAll('.agro-field-err')).forEach(function (n) { n.remove(); });
    Object.keys(map).forEach(function (k) { map[k].removeAttribute('aria-invalid'); });
    var body = { hp: hp.value, page: location.pathname };
    var cb = form.querySelector('[name=consent]');                    // explicit, logged consent: text and version travel with the request
    if (cb) { body.consent = !!cb.checked; body.consent_text = cb.getAttribute('data-consent-text') || ''; body.consent_version = cb.getAttribute('data-consent-version') || '1'; }
    var ts = form.querySelector('[name=cf-turnstile-response]'); if (ts) body.turnstile_token = ts.value;
    Object.keys(map).forEach(function (k) { body[k] = selectValue(map[k]); });
    if (btn) { btn.disabled = true; btn.setAttribute('aria-busy', 'true'); }
    say('Sending your request\u2026');
    post('/api/audit', body).then(function (r) {
      if (r.body && r.body.ok) {
        say(r.body.message || 'Thanks. Your audit is on its way.');
        [].slice.call(form.querySelectorAll('input, select, textarea, button')).forEach(function (c) { c.disabled = true; });
        if (r.body.id && !/^0+$/.test(r.body.id)) watchAudit(r.body.id, form, r.body.notice);   // all zeros = the spam trap answered
        return;
      }
      if (btn) { btn.disabled = false; btn.removeAttribute('aria-busy'); }
      var errs = (r.body && r.body.errors) || {}, first = null;
      Object.keys(errs).forEach(function (k) {
        var c = map[k] || (k === 'consent' ? cb : null); if (!c) return;   // consent is not a mapped text field: show its error beside the checkbox
        c.setAttribute('aria-invalid', 'true');
        var m = el('span', { 'class': 'agro-field-err' }, errs[k]); c.parentNode.insertBefore(m, c.nextSibling);
        first = first || c;
      });
      say((r.body && r.body.message) || 'Something went wrong. Please try again.', true);
      if (first) first.focus();
    }).catch(function () {
      if (btn) { btn.disabled = false; btn.removeAttribute('aria-busy'); }
      say(NET_ERR, true);
    });
  }
  function initAudit() {
    var form = findAuditForm(); if (!form || window.__agroAudit) return; window.__agroAudit = true;
    var w = mapFields(form).website;                                  // the browser rejects yourfirm.com in a url field before we see it
    if (w && w.type === 'url') { w.type = 'text'; w.setAttribute('inputmode', 'url'); }
    document.addEventListener('submit', onSubmit, true);              // on the document, so it survives the page re-rendering the form
    preflight(form, form.querySelector('[type=submit]'));
  }

  /* ---------------- Shield live check ---------------- */
  function makeSection(kind, anchorRe, where, heading) {   // adds a section to the static page after it has hydrated
    var main = document.querySelector('main'); if (!main) return null;
    var host = null;
    if (anchorRe) { var ss = main.querySelectorAll('section'); for (var i = 0; i < ss.length; i++) if (anchorRe.test(ss[i].textContent)) { host = ss[i]; break; } if (!host) return null; }
    var sec = el('section'), wrap = el('div', { 'class': 'wrap' }), box = el('div', { 'data-advisorgro': kind });
    if (heading) wrap.appendChild(el('h2', null, heading));
    wrap.appendChild(box); sec.appendChild(wrap);
    if (where === 'top') main.insertBefore(sec, main.firstChild); else host.parentNode.insertBefore(sec, host.nextSibling);
    return box;
  }
  function initShield() {
    var box = document.querySelector('[data-advisorgro="shield-snapshot"]') || makeSection('shield-snapshot', /eight things prospects check/i, 'after', 'What would a prospect find today?');
    if (!box || box.__agro) return; box.__agro = true;
    addCss(); box.className += ' agro-snap';
    var id = 'agro-site-' + Math.random().toString(36).slice(2, 7);
    var form = el('form', { novalidate: '' });
    var wrap = el('div'); wrap.appendChild(el('label', { 'for': id }, 'Your firm\u2019s website'));
    var input = el('input', { type: 'text', id: id, name: 'website', placeholder: 'yourfirm.com', inputmode: 'url', autocomplete: 'url', required: '' });
    wrap.appendChild(input); form.appendChild(wrap);
    var btn = el('button', { type: 'submit' }, 'Check my site'); form.appendChild(btn);
    var status = el('div', { 'class': 'agro-status', role: 'status', 'aria-live': 'polite', hidden: '' });
    var out = el('div');
    box.appendChild(form); box.appendChild(status); box.appendChild(out);
    function say(msg, err) { status.hidden = !msg; status.className = 'agro-status' + (err ? ' agro-err' : ''); status.textContent = msg || ''; }
    form.addEventListener('submit', function (e) {
      e.preventDefault(); out.textContent = '';
      if (!input.value.trim()) { say('Please enter your firm\u2019s website address.', true); input.focus(); return; }
      btn.disabled = true; btn.setAttribute('aria-busy', 'true'); say('Checking your domain, certificate and email settings. This can take up to 30 seconds.');
      post('/api/shield/snapshot', { website: input.value }).then(function (r) {
        btn.disabled = false; btn.removeAttribute('aria-busy');
        if (!r.body || !r.body.ok) { var m = r.body && ((r.body.errors && r.body.errors.website) || r.body.message); say(m || 'We couldn\u2019t complete the check. Please try again.', true); return; }
        say('Here is what a prospect would find today for ' + r.body.domain + '.');
        out.appendChild(render(r.body));
      }).catch(function () { btn.disabled = false; btn.removeAttribute('aria-busy'); say(NET_ERR, true); });
    });
  }
  function rowsTable(rows) {
    var t = el('table', { 'class': 'agro-rows' }), head = el('tr');
    ['What Shield watches', 'Today', 'What we found'].forEach(function (h) { head.appendChild(el('th', { scope: 'col' }, h)); });
    t.appendChild(head);
    rows.forEach(function (r) {
      var tr = el('tr'), td = el('td');
      tr.appendChild(el('td', null, r.area)); td.appendChild(el('span', { 'class': 'agro-pill agro-' + r.status }, r.label));
      tr.appendChild(td); tr.appendChild(el('td', null, r.detail)); t.appendChild(tr);
    });
    return t;
  }
  function render(d) {
    var wrap = el('div');
    wrap.appendChild(rowsTable(d.rows));
    var p = el('p', { 'class': 'agro-cta' }, 'The rows marked \u201cIn the free audit\u201d need your Google, AI and listings data. '), a = el('a', { href: AUDIT_PAGE }, 'Get your free audit');
    p.appendChild(a); wrap.appendChild(p);
    return wrap;
  }

  /* ---------------- full report (all five checks + fixes) ---------------- */
  var STATE_LABEL = { green: 'Green', amber: 'Amber', red: 'Red', review: 'With your CCO', unverified: 'Yet to be completed', not_run: 'Yet to be completed' };
  var CHECK_LABEL = { pass: 'Pass', partial: 'Partly', fail: 'Needs work', manual: 'Being checked by our team', unverified: 'Yet to be completed' };
  var PRIORITY_CLASS = { High: 'red', Medium: 'amber', Low: 'green' };
  function num(n) { return Math.round(n * 10) / 10; }
  function groupCard(g) {
    var card = el('div', { 'class': 'agro-card' }), top = el('div', { 'class': 'agro-cardtop' });
    top.appendChild(el('h5', null, g.name));
    top.appendChild(el('span', { 'class': 'agro-pill agro-' + g.state }, STATE_LABEL[g.state] || g.state));
    card.appendChild(top);
    if (g.held) { card.appendChild(el('p', { 'class': 'agro-small' }, g.note)); return card; }
    if (g.state === 'not_run') { card.appendChild(el('p', { 'class': 'agro-small' }, 'This check is yet to be completed. It will be added to the completed report we email you.')); return card; }
    card.appendChild(el('div', null, num(g.score) + ' / ' + num(g.max) + (g.partial ? ' so far' : '')));
    if (g.partial) card.appendChild(el('p', { 'class': 'agro-small' }, 'Some checks in this area are yet to be completed.'));
    var bar = el('div', { 'class': 'agro-bar', 'aria-hidden': 'true' }), fill = el('i', { 'class': 'agro-fill-' + g.state });
    fill.style.width = (g.max ? Math.max(0, Math.min(100, 100 * g.score / g.max)) : 0) + '%'; bar.appendChild(fill); card.appendChild(bar);
    if (typeof g.delta === 'number' && g.delta !== 0) card.appendChild(el('div', { 'class': 'agro-small' }, (g.delta > 0 ? '+' : '') + num(g.delta) + ' since last time'));
    if (g.checks.length) {
      var det = el('details'); det.appendChild(el('summary', null, 'What was checked (' + g.checks.length + ')'));
      var ul = el('ul', { 'class': 'agro-checks' });
      g.checks.forEach(function (c) {
        var li = el('li'); li.appendChild(el('span', { 'class': 'agro-pill agro-' + c.state }, CHECK_LABEL[c.state] || c.state));
        li.appendChild(el('strong', null, c.label)); li.appendChild(el('div', { 'class': 'agro-small' }, c.detail)); ul.appendChild(li);
      });
      det.appendChild(ul); card.appendChild(det);
    }
    return card;
  }
  function renderReport(d) {
    var box = el('section', { 'class': 'agro-report', 'aria-label': 'Shield report' });
    var kind = d.kind === 'quarterly' ? 'Quarterly Shield report' : d.kind === 'audit' ? 'Digital audit' : 'Monthly Shield check';
    box.appendChild(el('p', { 'class': 'agro-eyebrow' }, kind + (d.example ? ' \u00b7 Example, fictional firm' : '')));
    box.appendChild(el('h3', null, d.firm + ' \u00b7 ' + d.period));
    var sc = el('div', { 'class': 'agro-score' });
    sc.appendChild(el('span', { 'class': 'agro-big' }, String(num(d.score.total)))); sc.appendChild(el('span', { 'class': 'agro-of' }, '/ ' + num(d.score.max)));
    if (typeof d.score.delta === 'number' && d.score.delta !== 0)
      sc.appendChild(el('span', { 'class': 'agro-delta ' + (d.score.delta > 0 ? 'agro-up' : 'agro-down') }, (d.score.delta > 0 ? '+' : '') + num(d.score.delta) + ' since last time'));
    box.appendChild(sc);
    if (d.counts) box.appendChild(el('p', { 'class': 'agro-small' }, num(d.counts.passed) + ' checks passed \u00b7 ' + num(d.counts.needs_work) + ' need work \u00b7 ' + num(d.counts.unverified) + ' not yet verified'));
    if (!d.score.complete) box.appendChild(el('p', { 'class': 'agro-notice' }, d.kind === 'audit'
      ? 'This audit is not complete yet. The score is based on the checks finished so far and will change when the rest are done. The parts marked \u201cyet to be completed\u201d are listed below, and the completed report will be emailed to you.'
      : 'This score is a lower bound: some checks could not be verified and are listed below.'));
    if (d.summary) box.appendChild(el('p', null, d.summary));

    box.appendChild(el('h4', null, 'The five checks'));
    var grid = el('div', { 'class': 'agro-groups' }); d.groups.forEach(function (g) { grid.appendChild(groupCard(g)); }); box.appendChild(grid);

    (d.coverage || []).forEach(function (n) { box.appendChild(el('p', { 'class': 'agro-notice' }, n.text)); });   // sample size, and what a larger one adds
    if (d.passed) {                                   // shown whatever else failed or is still to be completed
      box.appendChild(el('h4', null, 'What is working'));
      if (d.passed.length) {
        var okl = el('ul', { 'class': 'agro-working' });
        d.passed.forEach(function (p) { var li = el('li'); li.appendChild(el('strong', null, p.label)); li.appendChild(document.createTextNode(' \u00b7 ' + p.area + ': ' + p.detail)); okl.appendChild(li); });
        box.appendChild(okl);
      } else box.appendChild(el('p', null, 'No check has earned full marks yet.'));
    }

    box.appendChild(el('h4', null, 'What Shield watches'));
    var wrapT = el('div', { style: 'overflow-x:auto' }); wrapT.appendChild(rowsTable(d.rows)); box.appendChild(wrapT);

    box.appendChild(el('h4', null, 'Fixes, in priority order'));
    if (d.fixes.length) {
      var ol = el('ol', { 'class': 'agro-fixes' });
      d.fixes.forEach(function (f) {
        var li = el('li'), line = el('div');
        line.appendChild(el('span', { 'class': 'agro-pill agro-' + (PRIORITY_CLASS[f.priority] || 'review') }, f.priority)); line.appendChild(document.createTextNode(' '));
        line.appendChild(el('strong', null, f.title)); li.appendChild(line);
        li.appendChild(el('div', { 'class': 'agro-small' }, f.area + ' \u00b7 ' + num(f.lost) + ' points at stake \u00b7 effort: ' + f.effort));
        li.appendChild(el('p', null, f.text || (f.found + ' Next step: ' + f.next))); ol.appendChild(li);
      });
      box.appendChild(ol);
    } else box.appendChild(el('p', null, 'Nothing measured needs fixing.'));

    if (d.recommendation && d.recommendation.offer) {
      box.appendChild(el('h4', null, 'Our recommendation'));
      var rp = el('p'); rp.appendChild(el('strong', null, d.recommendation.offer + ': ')); rp.appendChild(document.createTextNode(d.recommendation.reason + '.')); box.appendChild(rp);
    }
    if (d.notes) { box.appendChild(el('h4', null, 'Checked by our team')); box.appendChild(el('p', null, d.notes)); }
    if (d.unverified.length) {
      box.appendChild(el('h4', null, d.kind === 'audit' ? 'Yet to be completed' : 'What could not be verified automatically'));
      var ul2 = el('ul');
      d.unverified.forEach(function (u) { ul2.appendChild(el('li', null, u.label + ' (' + u.area + '): ' + (u.manual ? 'being checked by our team.' : 'yet to be completed. ' + u.detail))); });
      box.appendChild(ul2);
    }
    var pr = el('button', { type: 'button', 'class': 'agro-print agro-noprint' }, 'Print or save as PDF');
    pr.addEventListener('click', function () { [].slice.call(box.querySelectorAll('details')).forEach(function (x) { x.open = true; }); window.print(); });
    box.appendChild(pr);
    box.appendChild(el('p', { 'class': 'agro-small' }, 'AdvisorGro provides marketing services and reviews publicly available information. It does not provide legal, compliance or investment advice. Items flagged for review are prompts for your CCO, not compliance determinations.'));
    return box;
  }
  function initReport() {
    var box = document.querySelector('[data-advisorgro="shield-report"]') || (/[#&]report=/.test(location.hash) ? makeSection('shield-report', null, 'top') : null);
    if (!box || box.__agro) return; box.__agro = true;
    addCss();
    var tok = /[#&]report=([A-Za-z0-9_-]+)/.exec(location.hash), pv = /[#&]preview=([^&]+)/.exec(location.hash);
    var src = box.getAttribute('data-src');
    if (!tok && !src) return;
    var status = el('div', { 'class': 'agro-status', role: 'status', 'aria-live': 'polite' }, 'Loading your report\u2026');
    box.textContent = ''; box.appendChild(status);
    var req = tok
      ? fetch(API + '/api/report/' + tok[1], { credentials: 'omit', headers: pv ? { Authorization: 'Bearer ' + decodeURIComponent(pv[1]) } : {} })
      : fetch(src, { credentials: 'omit' });
    req.then(function (r) { return r.json().catch(function () { return {}; }).then(function (j) { return { ok: r.ok, body: j }; }); })
      .then(function (r) {
        var d = tok ? r.body.report : r.body;
        if (!r.ok || !d || !d.groups) {
          status.className = 'agro-status agro-err';
          status.textContent = tok ? 'We couldn\u2019t find this report. The link may be incomplete, or the report is not published yet. Please use the link in your email, or contact us.' : 'The example report could not be loaded.';
          return;
        }
        box.textContent = ''; box.appendChild(renderReport(d));
        if (tok && box.scrollIntoView) box.scrollIntoView({ behavior: 'smooth', block: 'start' });
      }).catch(function () { status.className = 'agro-status agro-err'; status.textContent = NET_ERR; });
  }

  /* ---------------- audit results on the page ---------------- */
  function get(path) {
    return fetch(API + path, { credentials: 'omit' })
      .then(function (r) { return r.json().catch(function () { return {}; }).then(function (j) { return { status: r.status, body: j }; }); });
  }
  function preflight(form, btn) {       // ask the server before the visitor fills anything in
    get('/api/status').then(function (r) {
      var b = r.body || {}; if (b.ok === undefined || !b.message) return;
      var n = el('div', { 'class': 'agro-status agro-warn', role: 'status' }, b.message); form.parentNode.insertBefore(n, form);
      if (b.audit === false && btn) { btn.disabled = true; btn.setAttribute('aria-disabled', 'true'); }
    }).catch(function () { /* the form still works; the server answers each request itself */ });
  }
  function resultsBox(form) {
    var box = document.querySelector('[data-advisorgro="audit-results"]');
    if (!box) {
      box = el('div', { 'class': 'agro-results wrap', 'data-advisorgro': 'audit-results' });
      var host = form.closest && form.closest('section'), sec = el('section');
      sec.appendChild(box);
      if (host && host.parentNode) host.parentNode.insertBefore(sec, host.nextSibling); else form.parentNode.insertBefore(sec, form.nextSibling);
    }
    box.setAttribute('aria-live', 'polite'); return box;
  }
  function watchAudit(id, form, notice) {
    addCss();
    var box = resultsBox(form), started = Date.now(), fails = 0;
    var status = el('div', { 'class': 'agro-status', role: 'status' });
    box.textContent = ''; if (notice) box.appendChild(el('div', { 'class': 'agro-status agro-warn' }, notice)); box.appendChild(status);
    function wait(ms) { setTimeout(poll, ms); }
    function poll() {
      var secs = Math.round((Date.now() - started) / 1000);
      if (secs > 720) { status.textContent = 'This is taking longer than usual. We will email your report as soon as it is ready.'; return; }
      get('/api/audit/' + id).then(function (r) {
        var b = r.body || {}; fails = 0;
        if (b.report) { show(b); return; }
        if (b.status === 'failed' || r.status === 404) { status.className = 'agro-status agro-err'; status.textContent = b.message || 'We could not finish your audit automatically. A member of our team will follow up by email.'; return; }
        if (b.status === 'pending_review' || b.status === 'sent' || b.status === 'send_failed') { status.textContent = b.message; return; }   // done, but not for display (preview is off)
        status.textContent = (b.message || 'We are checking your firm now.') + ' This usually takes one to three minutes. You can leave this page; we will email you.' + (secs > 20 ? ' (' + secs + 's)' : '');
        wait(secs > 90 ? 8000 : 4000);
      }).catch(function () {
        if (++fails > 5) { status.className = 'agro-status agro-err'; status.textContent = NET_ERR; return; }
        wait(6000);
      });
    }
    function show(b) {
      box.textContent = '';
      var m = b.mail || {};
      if (m.message) box.appendChild(el('div', { 'class': 'agro-status' + (m.state === 'unavailable' ? ' agro-warn' : ''), role: 'status' }, m.message));
      if (b.report.preliminary) box.appendChild(el('div', { 'class': 'agro-status' }, 'These are preliminary results. A person reviews your audit before the final report is emailed, so a few items may be added or adjusted.'));
      box.appendChild(renderReport(b.report));
      var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
      if (box.scrollIntoView) box.scrollIntoView({ behavior: reduce ? 'auto' : 'smooth', block: 'start' });
    }
    poll();
  }

  function init() { initAudit(); initShield(); initReport(); }
  window.AdvisorGro = { init: init, api: API };
  // The pages are a static export that the browser then hydrates. Touching the page before that finishes can make it
  // discard our changes, so wait for the load event plus a short pause.
  function start() { setTimeout(init, 800); }
  if (document.readyState === 'complete') start(); else window.addEventListener('load', start);
})();