/*
 * ConstructConnect Insight harvester - runs in the browser console on an
 * authenticated app.constructconnect.com tab.
 *
 * WHY IN THE BROWSER AT ALL
 * The Insight API authenticates with a session cookie plus a rotating CSRF
 * token. Replaying it from Python means exporting live session state, which
 * breaks whenever the session refreshes. Running inside the page means the
 * browser supplies both automatically and we never handle the credentials.
 *
 * THE THREE THINGS THAT MATTER
 *   1. offset + limit is hard-capped at 10,000 and limit at 100. No single
 *      query can walk a 136k-project saved search. Partition by date instead.
 *   2. A 403 from project/getProjectDataByCrmId is per-project, not a rate
 *      limit. It means the subscription does not license that record. Verified
 *      back to back: two ids returned 200 while a third returned 403 every
 *      time. Do not treat it as a ban and do not stop the run for it.
 *   3. Nothing is durable until it is on disk. Drain every ~45 minutes.
 *
 * USAGE
 *   1. open the saved search, then paste PART 1 and interact with the page
 *      once (change page size) so a real request is captured
 *   2. paste PART 2 and call __discover(startOffset, endOffset)
 *   3. paste PART 3 and call __runDetails(ids)
 *   4. call __stage() and copy the base64 to disk periodically
 */

/* ------------------------------------------------------------------ *
 * PART 1 - capture the auth headers and the saved-search request body
 * ------------------------------------------------------------------ */
window.__HMAP = {};   // endpoint -> request headers (incl. CSRF-Token)
window.__BODY = null; // the saved search's own filter payload, reused verbatim

(function installCapture() {
  const osh = XMLHttpRequest.prototype.setRequestHeader;
  XMLHttpRequest.prototype.setRequestHeader = function (k, v) {
    (this.__h = this.__h || {})[k] = v;
    return osh.apply(this, arguments);
  };
  const oo = XMLHttpRequest.prototype.open;
  const os = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function (m, u) { this.__u = u; return oo.apply(this, arguments); };
  XMLHttpRequest.prototype.send = function (b) {
    const m = /\/api\/agent\/(.+)$/.exec(this.__u || "");
    if (m) {
      const self = this;
      self.addEventListener("load", function () {
        window.__HMAP[m[1]] = self.__h || {};
        if (/projectLeadsElastic/.test(m[1]) && typeof b === "string") {
          try { window.__BODY = JSON.parse(b); } catch (e) {}
        }
      });
    }
    return os.apply(this, arguments);
  };
  // the SPA uses fetch for some calls
  const of = window.fetch;
  window.fetch = async function (input, init) {
    const url = typeof input === "string" ? input : (input && input.url) || String(input);
    const m = /\/api\/agent\/(.+)$/.exec(url);
    if (m && init) {
      const h = {};
      try {
        const src = init.headers;
        if (src) { if (src.forEach) src.forEach((v, k) => (h[k] = v)); else Object.assign(h, src); }
      } catch (e) {}
      window.__HMAP[m[1]] = h;
      if (/projectLeadsElastic/.test(m[1]) && typeof init.body === "string") {
        try { window.__BODY = JSON.parse(init.body); } catch (e) {}
      }
    }
    return of.apply(this, arguments);
  };
})();

/* ------------------------------------------------------------------ *
 * PART 2 - discovery
 * ------------------------------------------------------------------ */
window.__post = async function (ep, body) {
  const r = await fetch("/api/agent/" + ep, {
    method: "POST",
    headers: Object.assign({}, window.__HMAP[ep]),
    body: JSON.stringify(body),
    credentials: "include",
  });
  return { status: r.status, json: r.ok ? await r.json() : null };
};

// Search with the saved search's own filters, overriding paging only.
window.__search = async function (patch) {
  const b = JSON.parse(JSON.stringify(window.__BODY));
  Object.assign(b[0], patch);
  return window.__post("searchAPI/projectLeadsElastic", b);
};

/*
 * Walk one slice of the result set. offset + limit must stay under 10,000, so
 * anything larger has to be split by date first (see __dateSlice below).
 */
window.__discover = async function (from, to, sleepMs) {
  from = from || 0; to = Math.min(to || 9900, 9900); sleepMs = sleepMs || 4200;
  const out = [], errs = [];
  for (let off = from; off <= to; off += 100) {
    let ok = false;
    for (let attempt = 0; attempt < 3 && !ok; attempt++) {
      try {
        const r = await window.__search({ limit: 100, offset: off, includeAllFacets: false });
        if (r.status === 403) { errs.push({ off, status: 403 }); return { out, errs, stopped: "403" }; }
        if (r.status !== 200) throw new Error("status " + r.status);
        for (const d of r.json.docs) {
          out.push({
            id: d.id,
            v: d.projectValue || 0,
            lu: (d.lastUpdatedDate || "").slice(0, 10),
            st: d.projectStatus || "",
            cat: d.projectCategory || "",
            csi: (d.csiCodes || []).map(c => String(c).slice(0, 4))
                   .filter((x, i, a) => a.indexOf(x) === i).join(","),
            csiN: (d.csiCodes || []).length,
            t: d.title || "",
            state: (d.address && d.address.state) || "",
          });
        }
        ok = true;
      } catch (e) {
        if (attempt === 2) errs.push({ off, err: String(e).slice(0, 80) });
        await new Promise(r => setTimeout(r, 8000));
      }
    }
    await new Promise(r => setTimeout(r, sleepMs + Math.random() * 1600));
  }
  return { out, errs, stopped: null };
};

/*
 * Date-partitioned discovery, for reaching past the 10,000 ceiling. The saved
 * search UI writes its date filter into the same body, so capture one custom
 * range through the UI first and read the field names off window.__BODY.
 */
window.__dateSlice = function (fromISO, toISO) {
  const b = JSON.parse(JSON.stringify(window.__BODY));
  b[0].filters = b[0].filters || {};
  b[0].filters.lastUpdatedDateRange = { from: fromISO, to: toISO };
  return b;
};

/* ------------------------------------------------------------------ *
 * PART 3 - detail + contact collection
 * ------------------------------------------------------------------ */
window.__D = null;

window.__initRun = function (ids) {
  window.__D = {
    queue: ids.slice(), all: [], i: 0, completed: 0, savedUpTo: 0,
    errors: [], contactsTotal: 0, requests: 0,
    stopped: false, stopReason: null, consecFail: 0, trips: 0,
  };
  // A silent oscillator keeps the tab off Chrome's background throttle. Without
  // it a backgrounded tab drops to ~1 timer per minute and the run stalls.
  try { window.__keepAwake && window.__keepAwake.ctx.close(); } catch (e) {}
  const ctx = new (window.AudioContext || window.webkitAudioContext)();
  const osc = ctx.createOscillator(), g = ctx.createGain();
  g.gain.value = 0.0001; osc.connect(g); g.connect(ctx.destination); osc.start();
  window.__keepAwake = { ctx, osc };
  return window.__D.queue.length + " queued";
};

window.__fetchOne = async function (id) {
  const D = window.__D, rec = { id };
  try {
    D.requests++;
    const p = await window.__post("project/getProjectDataByCrmId", [id]);
    if (p.status === 403) { rec.err = "403 (not licensed on this subscription)"; return rec; }
    if (p.status !== 200 || !p.json) { rec.err = "detail " + p.status; return rec; }
    rec.p = p.json;
    await new Promise(r => setTimeout(r, 2500 + Math.random() * 1200));
    D.requests++;
    // NOTE: participants keys on the INTERNAL id (p.Id), not the search id
    const dt = await window.__post("projectParticipants/getDesignTeam", [p.json.Id]);
    rec.contacts = Array.isArray(dt.json) ? dt.json : (dt.json && dt.json.contacts) || [];
    D.contactsTotal += rec.contacts.length;
  } catch (e) {
    rec.err = String(e).slice(0, 90);
  }
  return rec;
};

window.__runDetails = async function (sleepMs) {
  const D = window.__D;
  sleepMs = sleepMs || 4200;               // ~12 req/min against a 20/min ceiling
  while (D.i < D.queue.length && !D.stopped) {
    const id = D.queue[D.i++];
    const rec = await window.__fetchOne(id);
    if (rec.err) {
      D.errors.push({ id, err: rec.err });
      // A per-project 403 is a licensing gap, not a ban: skip it and continue.
      if (!/403/.test(rec.err)) {
        if (++D.consecFail >= 5) { D.stopped = true; D.stopReason = "5 consecutive failures"; }
      }
    } else {
      D.consecFail = 0;
    }
    D.all.push(rec);
    D.completed++;
    await new Promise(r => setTimeout(r, sleepMs + Math.random() * 1600));
  }
  return D.completed + "/" + D.queue.length;
};

/* ------------------------------------------------------------------ *
 * PART 4 - drain to disk
 * Base64 through the clipboard, because the page cannot write files. Only mark
 * records saved AFTER the JSON parses on the other side, so a failed transfer
 * costs a retry and never data.
 * ------------------------------------------------------------------ */
window.__slim = function (r) {
  return { id: r.id, p: r.p, contacts: r.contacts || [], s: r.s || null, err: r.err || null };
};

window.__stage = function () {
  const D = window.__D;
  const slice = D.all.slice(D.savedUpTo).map(window.__slim);
  const json = JSON.stringify(slice);
  window.__B64 = btoa(unescape(encodeURIComponent(json)));
  window.__markTo = D.savedUpTo + slice.length;

  document.getElementById("__cpWrap") && document.getElementById("__cpWrap").remove();
  const w = document.createElement("div");
  w.id = "__cpWrap";
  w.style.cssText = "position:fixed;z-index:2147483647;bottom:8px;left:8px;" +
                    "background:#fff;border:3px solid #c00;padding:8px";
  const ta = document.createElement("textarea");
  ta.style.cssText = "width:120px;height:40px";
  const b = document.createElement("button");
  b.textContent = "COPY BATCH";
  b.onclick = () => { ta.value = window.__B64; ta.select(); document.execCommand("copy"); b.textContent = "COPIED"; };
  w.appendChild(ta); w.appendChild(b); document.body.appendChild(w);
  return slice.length + " records staged (" + window.__B64.length + " b64)";
};

// call ONLY after the file on disk has parsed successfully
window.__confirmSaved = function () {
  window.__D.savedUpTo = window.__markTo;
  return "savedUpTo=" + window.__D.savedUpTo;
};

window.__status = function () {
  const D = window.__D;
  return JSON.stringify({
    completed: D.completed, of: D.queue.length, unsaved: D.all.length - D.savedUpTo,
    errors: D.errors.length, stopped: D.stopped, reason: D.stopReason,
    contactsTotal: D.contactsTotal, requests: D.requests,
    audio: (() => { try { return window.__keepAwake.ctx.state; } catch (e) { return "closed"; } })(),
  });
};
