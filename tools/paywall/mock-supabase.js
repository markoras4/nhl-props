// Local test stand-in for supabase-js (served only by tools/paywall/devserver.py, never deployed).
// Pick the plan with ?as=visitor|free|pro|creator|media on any page; it sticks for the browser tab (sessionStorage).
// Tables live in localStorage. functions.invoke("pro-data") mimics the real edge function: Pro plans get a URL to the
// local Pro file, everyone else gets 403. The RLS rules in supabase/migrations/*_paywall.sql are mirrored for
// watchlist (3 for free), alert_settings (Pro only) and parlays (2 legs for free).
(function () {
  const PRO = ["pro", "creator", "media"];
  const qs = new URLSearchParams(location.search);
  const ss = { get: k => { try { return sessionStorage.getItem(k); } catch (e) { return null; } }, set: (k, v) => { try { sessionStorage.setItem(k, v); } catch (e) {} } };
  if (qs.get("as")) ss.set("devplan", qs.get("as"));
  let plan = ss.get("devplan") || "visitor";
  const user = () => plan === "visitor" ? null : { id: "dev-" + plan, email: plan + "@dev.local" };
  const listeners = [];
  const session = () => user() ? { user: user(), access_token: "dev" } : null;
  const fire = ev => listeners.forEach(cb => { try { cb(ev, session()); } catch (e) { console.error(e); } });
  const dbKey = t => "devdb:" + plan + ":" + t;
  const rows = t => { try { return JSON.parse(localStorage.getItem(dbKey(t)) || "[]"); } catch (e) { return []; } };
  const save = (t, r) => localStorage.setItem(dbKey(t), JSON.stringify(r));
  const err = m => ({ data: null, error: { message: m, code: "42501" } });

  function query(table) {
    let op = "select", payload = null, filters = [], single = false, maybe = false, order = [], limit = null, ret = false;
    const q = {
      select() { if (op !== "select") ret = true; return q; },
      insert(p) { op = "insert"; payload = p; return q; },
      upsert(p) { op = "upsert"; payload = p; return q; },
      update(p) { op = "update"; payload = p; return q; },
      delete() { op = "delete"; return q; },
      eq(k, v) { filters.push([k, v]); return q; },
      in(k, v) { filters.push([k, v, "in"]); return q; },
      gte() { return q; },
      order(k, o) { order.push([k, o?.ascending !== false]); return q; },
      limit(n) { limit = n; return q; },
      single() { single = true; return q; },
      maybeSingle() { maybe = true; return q; },
      then(res, rej) { return Promise.resolve().then(run).then(res, rej); },
    };
    const match = r => filters.every(([k, v, how]) => how === "in" ? v.includes(r[k]) : String(r[k]) === String(v));
    function run() {
      const u = user();
      if (table === "profiles") {
        const d = u ? { id: u.id, plan: plan === "visitor" ? "free" : plan, display_name: u.email } : null;
        return { data: maybe || single ? d : (d ? [d] : []), error: null };
      }
      if (!u) return err("not signed in");
      let all = rows(table);
      if (op === "select") {
        let r = all.filter(match);
        for (const [k, asc] of order.slice().reverse()) r.sort((a, b) => (a[k] > b[k] ? 1 : a[k] < b[k] ? -1 : 0) * (asc ? 1 : -1));
        if (limit) r = r.slice(0, limit);
        return { data: maybe || single ? (r[0] || null) : r, error: null };
      }
      if (op === "insert" || op === "upsert") {
        const list = (Array.isArray(payload) ? payload : [payload]).map(x => ({ id: Date.now() + Math.random(), created_at: new Date().toISOString(), user_id: u.id, ...x }));
        const pro = PRO.includes(plan);
        // the database rules from the paywall migration
        if (table === "watchlist" && !pro && all.length + list.length > 3) return err("new row violates row-level security policy for table \"watchlist\"");
        if (table === "alert_settings" && !pro) return err("new row violates row-level security policy for table \"alert_settings\"");
        if (table === "parlays" && !pro && list.some(x => (x.legs || []).length > 2)) return err("new row violates row-level security policy for table \"parlays\"");
        if (op === "upsert" && table === "alert_settings") all = all.filter(r => r.user_id !== u.id);
        all.push(...list); save(table, all);
        return { data: single || maybe ? list[0] : list, error: null };
      }
      if (op === "update") {
        if (table === "alert_settings" && !PRO.includes(plan)) return err("row-level security");
        all.forEach(r => { if (match(r)) Object.assign(r, payload); }); save(table, all); return { data: null, error: null };
      }
      if (op === "delete") { save(table, all.filter(r => !match(r))); return { data: null, error: null }; }
      return { data: null, error: null };
    }
    return q;
  }

  const client = {
    auth: {
      onAuthStateChange(cb) { listeners.push(cb); setTimeout(() => cb("INITIAL_SESSION", session()), 0); return { data: { subscription: { unsubscribe() {} } } }; },
      async getSession() { return { data: { session: session() }, error: null }; },
      async signInWithPassword() { if (plan === "visitor") { plan = "free"; ss.set("devplan", plan); } fire("SIGNED_IN"); return { data: { session: session() }, error: null }; },
      async signUp() { if (plan === "visitor") { plan = "free"; ss.set("devplan", plan); } fire("SIGNED_IN"); return { data: { session: session(), user: user() }, error: null }; },
      async signInWithOtp() { return { data: {}, error: null }; },
      async signOut() { plan = "visitor"; ss.set("devplan", plan); fire("SIGNED_OUT"); return { error: null }; },
    },
    from: query,
    rpc: async () => ({ data: null, error: null }),
    functions: {
      async invoke(name, opts) {
        if (name !== "pro-data") return { data: null, error: { message: "unknown function" } };
        if (!user()) return { data: { error: "sign in" }, error: { message: "401" } };
        if (!PRO.includes(plan)) return { data: { error: "pro", plan }, error: { message: "403" } };
        const d = opts?.body?.d || "latest";
        return { data: { url: "/__dev/pro/" + d + ".json", plan }, error: null };
      },
    },
  };
  window.supabase = { createClient: () => client };
  window.__DEV_PLAN__ = () => plan;
})();
