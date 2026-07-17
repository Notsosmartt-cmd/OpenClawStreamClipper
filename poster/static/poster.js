// Buffer Clip Poster — single-page UI.
// Cards are built via createElement/textContent (clip titles are arbitrary
// LLM output — never trust them inside innerHTML).

const state = {
    clips: [],
    channels: [],
    selected: new Set(),
    captions: new Map(),   // name -> edited caption (uneditied = derived)
    cards: new Map(),      // name -> {card, chip, err}
    job: null,
    ready: false,          // key + hosting both present
    pollTimer: null,
};

// Lazy preview loading: with 80+ clips, eagerly fetching metadata for every
// <video> stalls the tab for ~30s+. Only fetch when a card scrolls into view.
const previewObserver = new IntersectionObserver((entries) => {
    entries.forEach((e) => {
        if (!e.isIntersecting) return;
        const vid = e.target;
        if (vid.dataset.src) {
            vid.src = vid.dataset.src;
            vid.preload = "metadata";
            delete vid.dataset.src;
        }
        previewObserver.unobserve(vid);
    });
}, { rootMargin: "300px" });

const $ = (id) => document.getElementById(id);

// ---------- boot ----------

async function init() {
    wireButtons();
    await fetchStatus();
    fetchClips();
    fetchChannels(false).then(fetchLimits);
}

function wireButtons() {
    $("btn-refresh-clips").onclick = fetchClips;
    $("btn-refresh-channels").onclick = () => fetchChannels(true);
    $("btn-sel-all").onclick = () => selectWhere(() => true);
    $("btn-sel-none").onclick = () => selectWhere(() => false);
    $("btn-sel-unposted").onclick = () => selectWhere((c) => !c.posted);
    $("btn-post").onclick = startPost;
    $("btn-retry").onclick = startRetry;
    $("btn-cancel").onclick = cancelJob;
    $("btn-save-hosting").onclick = saveHosting;
    $("btn-verify-ledger").onclick = verifyLedger;
    // spacing selector only matters in drip mode
    const mode = $("sel-mode");
    const syncSpacing = () => {
        $("grp-spacing").style.display = mode.value === "drip" ? "" : "none";
    };
    mode.onchange = syncSpacing;
    syncSpacing();
    // hashtags survive reloads — they rarely change between batches
    const ht = $("inp-hashtags");
    ht.value = localStorage.getItem("poster_hashtags") || "";
    ht.oninput = () => localStorage.setItem("poster_hashtags", ht.value);
}

// ---------- daily quota strip ----------

async function fetchLimits() {
    const row = $("quota-row");
    try {
        const res = await fetch("/api/limits");
        const d = await res.json();
        if (!res.ok) throw new Error(d.error || res.status);
        const parts = Object.values(d).map((l) => {
            const left = l.remaining ?? "?";
            const tone = left === 0 ? "var(--danger)"
                : left !== "?" && left <= 5 ? "var(--warn)" : "var(--success)";
            return `<b style="color:${tone}">${(l.service || "?").toUpperCase()}
                ${left}</b>/${l.limit ?? "?"} left today`;
        });
        row.innerHTML = "Posting room (rolling 24 h, network caps): " +
            parts.join(" · ");
    } catch (e) {
        row.textContent = "Posting room: unavailable (" + e.message + ")";
    }
}

async function verifyLedger() {
    const btn = $("btn-verify-ledger");
    btn.disabled = true;
    btn.textContent = "Checking…";
    try {
        const res = await fetch("/api/verify-ledger", { method: "POST" });
        const d = await res.json();
        btn.textContent = d.error ? "Refresh statuses"
            : `Refresh statuses (${d.updated ?? 0} updated)`;
        await fetchClips();
        fetchLimits();
    } catch (e) {
        btn.textContent = "Refresh statuses";
    }
    btn.disabled = false;
}

// ---------- status / setup ----------

async function fetchStatus() {
    try {
        const s = await (await fetch("/api/status")).json();
        state.ready = s.key_present && s.hosting_configured;
        $("setup-panel").style.display = state.ready ? "none" : "";
        $("key-note").style.display = s.key_present ? "none" : "";
        $("hosting-setup").style.display = s.hosting_configured ? "none" : "";
        if (s.job && s.job.state === "running") {
            state.job = s.job;
            startPolling();          // adopt a batch already in flight (reload)
        }
    } catch (e) {
        console.error("status failed:", e);
    }
    updateControls();
}

async function saveHosting() {
    const btn = $("btn-save-hosting");
    btn.disabled = true;
    $("hosting-msg").textContent = "Testing credentials…";
    try {
        const res = await fetch("/api/hosting", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                cloud_name: $("inp-cloud").value,
                api_key: $("inp-ckey").value,
                api_secret: $("inp-csecret").value,
            }),
        });
        const d = await res.json();
        $("hosting-msg").textContent = d.message || (d.ok ? "Saved." : "Failed.");
        if (d.ok) await fetchStatus();
    } catch (e) {
        $("hosting-msg").textContent = "Request failed: " + e;
    }
    btn.disabled = false;
}

// ---------- channels ----------

async function fetchChannels(refresh) {
    const row = $("channels-row");
    try {
        const res = await fetch("/api/channels" + (refresh ? "?refresh=1" : ""));
        const d = await res.json();
        if (!res.ok) throw new Error(d.error || res.status);
        state.channels = d.channels || [];
        if (d.account) {
            const b = $("account-badge");
            b.style.display = "";
            b.textContent = "Buffer · " + (d.account.email || d.account.organization || "connected");
        }
        row.textContent = "";
        state.channels.forEach((ch) => {
            const chip = document.createElement("label");
            chip.className = "chan-chip on";           // default: all checked
            chip.dataset.id = ch.id;
            const box = document.createElement("input");
            box.type = "checkbox";
            box.checked = true;
            box.onchange = () => {
                chip.classList.toggle("on", box.checked);
                updateControls();
            };
            chip.appendChild(box);
            if (ch.avatar) {
                const img = document.createElement("img");
                img.src = ch.avatar;
                img.onerror = () => img.remove();
                chip.appendChild(img);
            }
            const svc = document.createElement("span");
            svc.className = "svc";
            svc.textContent = ch.service || "?";
            chip.appendChild(svc);
            const name = document.createElement("span");
            name.textContent = ch.displayName || ch.name || ch.id;
            chip.appendChild(name);
            if (ch.isQueuePaused) {
                const p = document.createElement("span");
                p.className = "svc";
                p.textContent = "· queue paused";
                chip.appendChild(p);
            }
            row.appendChild(chip);
        });
        if (!state.channels.length) {
            row.innerHTML = '<span class="hint">No channels connected to this Buffer account.</span>';
        }
    } catch (e) {
        row.textContent = "";
        const err = document.createElement("span");
        err.className = "hint";
        err.style.color = "var(--danger)";
        err.textContent = "Couldn't load channels: " + e.message;
        row.appendChild(err);
    }
    updateControls();
}

function checkedChannelIds() {
    return [...document.querySelectorAll(".chan-chip input:checked")]
        .map((b) => b.closest(".chan-chip").dataset.id);
}

// ---------- clips ----------

async function fetchClips() {
    try {
        state.clips = await (await fetch("/api/clips")).json();
    } catch (e) {
        console.error("clips failed:", e);
        return;
    }
    const present = new Set(state.clips.map((c) => c.name));
    [...state.selected].forEach((n) => { if (!present.has(n)) state.selected.delete(n); });
    renderClips();
}

function renderClips() {
    const grid = $("clips-grid");
    grid.textContent = "";
    state.cards.clear();
    if (!state.clips.length) {
        const d = document.createElement("div");
        d.className = "empty-state";
        d.textContent = "No clips found in the clips folder yet.";
        grid.appendChild(d);
        updateControls();
        return;
    }
    state.clips.forEach((c) => {
        const card = document.createElement("div");
        card.className = "pick-card" + (state.selected.has(c.name) ? " sel" : "");

        const media = document.createElement("div");
        media.style.position = "relative";
        const vid = document.createElement("video");
        vid.preload = "none";
        vid.dataset.src = "/api/clips/" + encodeURIComponent(c.name) + "#t=0.5";
        vid.onclick = () => (vid.paused ? vid.play() : vid.pause());
        previewObserver.observe(vid);
        media.appendChild(vid);

        const check = document.createElement("input");
        check.type = "checkbox";
        check.className = "pick-check";
        check.checked = state.selected.has(c.name);
        check.onchange = () => {
            if (check.checked) state.selected.add(c.name);
            else state.selected.delete(c.name);
            card.classList.toggle("sel", check.checked);
            updateControls();
        };
        media.appendChild(check);

        const badges = document.createElement("div");
        badges.className = "pick-badges";
        if (c.posted) {
            const posts = c.posted.posts || [];
            const bad = posts.filter((p) => p.status === "error" || p.status === "skipped_cap");
            const sched = posts.filter((p) => p.status === "scheduled");
            const chip = document.createElement("span");
            chip.className = "chip " + (bad.length ? "chip-err"
                : sched.length ? "chip-run" : "chip-posted");
            chip.textContent = (bad.length ? "posted ⚠"
                : sched.length ? "scheduled ⏱" : "posted") +
                (c.posted.times > 1 ? " ×" + c.posted.times : "");
            chip.title = "Posted " + (c.posted.posted_at || "") + "\n" +
                posts.map((p) =>
                    `${p.service}: ${p.status || "accepted"}` +
                    (p.status === "scheduled" && p.due_at ? ` for ${p.due_at}` : "") +
                    (p.error ? " — " + p.error : "")
                ).join("\n");
            badges.appendChild(chip);
        }
        const status = document.createElement("span");
        status.className = "chip";
        status.style.display = "none";
        badges.appendChild(status);
        media.appendChild(badges);
        card.appendChild(media);

        const cap = document.createElement("textarea");
        cap.className = "cap-input";
        cap.rows = 2;
        cap.value = state.captions.get(c.name) ?? c.caption;
        cap.title = "Caption for both networks (defaults to the clip title)";
        cap.oninput = () => state.captions.set(c.name, cap.value);
        card.appendChild(cap);

        const meta = document.createElement("div");
        meta.className = "pick-meta";
        const left = document.createElement("span");
        left.textContent = c.size_mb + " MB · " + c.modified;
        const err = document.createElement("span");
        err.className = "err";
        meta.appendChild(left);
        meta.appendChild(err);
        card.appendChild(meta);

        state.cards.set(c.name, { card, chip: status, err });
        grid.appendChild(card);
    });
    updateControls();
}

function selectWhere(pred) {
    state.selected = new Set(state.clips.filter(pred).map((c) => c.name));
    state.clips.forEach((c) => {
        const entry = state.cards.get(c.name);
        if (!entry) return;
        const on = state.selected.has(c.name);
        entry.card.classList.toggle("sel", on);
        entry.card.querySelector(".pick-check").checked = on;
    });
    updateControls();
}

// ---------- posting ----------

function jobRunning() {
    return state.job && ["running", "verifying"].includes(state.job.state);
}

function updateControls() {
    const n = state.selected.size;
    const chans = checkedChannelIds().length;
    const btn = $("btn-post");
    btn.textContent = `Post Selected (${n})`;
    btn.disabled = !state.ready || jobRunning() || n === 0 || chans === 0;
    if (!state.ready) {
        btn.title = "Finish Setup first (key + media hosting)";
    } else if (chans === 0) {
        btn.title = "Check at least one channel";
    } else {
        btn.title = n ? `${n} clip(s) → ${chans} channel(s) = ${n * chans} posts` : "Select clips below";
    }
    // Retry failed (N): ledger posts that errored OR were cap-skipped
    const failed = state.clips.reduce((s, c) =>
        s + ((c.posted && c.posted.posts) || []).filter(
            (p) => p.status === "error" || p.status === "skipped_cap").length, 0);
    const rbtn = $("btn-retry");
    rbtn.style.display = failed && !jobRunning() ? "" : "none";
    rbtn.textContent = `Retry failed (${failed})`;
    rbtn.title = "Re-posts only the clip+channel pairs Buffer reported as errored (no re-upload)";
    $("btn-cancel").style.display = jobRunning() ? "" : "none";
    $("sel-count").textContent = state.clips.length
        ? `· ${n}/${state.clips.length} selected` : "";
    const badge = $("status-badge");
    badge.classList.toggle("running", jobRunning());
    $("status-label").textContent = !jobRunning() ? "Idle"
        : (state.job.state === "verifying" ? "Verifying…" : "Posting…");
}

async function startPost() {
    if (jobRunning()) return;
    const clips = state.clips
        .filter((c) => state.selected.has(c.name))
        .map((c) => ({
            name: c.name,
            caption: (state.captions.get(c.name) ?? c.caption).trim(),
        }));
    const payload = {
        clips,
        channel_ids: checkedChannelIds(),
        mode: $("sel-mode").value,
        spacing_min: parseInt($("sel-spacing").value, 10),
        hashtags: $("inp-hashtags").value,
    };
    $("btn-post").disabled = true;
    $("job-summary").textContent = "Starting…";
    try {
        const res = await fetch("/api/post", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
        });
        const d = await res.json();
        if (!res.ok) throw new Error(d.error || res.status);
        state.job = d;
        startPolling();
    } catch (e) {
        $("job-summary").textContent = "Couldn't start: " + e.message;
    }
    updateControls();
}

async function startRetry() {
    if (jobRunning()) return;
    const btn = $("btn-retry");
    btn.disabled = true;
    $("job-summary").textContent = "Retrying failed posts…";
    try {
        const res = await fetch("/api/retry", { method: "POST" });
        const d = await res.json();
        if (!res.ok) throw new Error(d.error || res.status);
        state.job = d;
        startPolling();
    } catch (e) {
        $("job-summary").textContent = "Retry: " + e.message;
    }
    btn.disabled = false;
    updateControls();
}

async function cancelJob() {
    $("btn-cancel").disabled = true;
    try { await fetch("/api/job/cancel", { method: "POST" }); } catch (e) { /* noop */ }
    $("btn-cancel").disabled = false;
}

function startPolling() {
    if (state.pollTimer) return;
    state.pollTimer = setInterval(pollJob, 1500);
    pollJob();
    updateControls();
}

// "tt✓ ig⏱" style per-network readout for one item's posts
function netSummary(posts) {
    return posts.map((p) => {
        const svc = p.service === "tiktok" ? "tt"
            : p.service === "instagram" ? "ig" : (p.service || "?").slice(0, 2);
        const mark = p.status === "sent" ? "✓" : p.status === "error" ? "✗"
            : p.status === "scheduled" ? "⏱" : p.status === "skipped_cap" ? "⏸" : "…";
        return svc + mark;
    }).join(" ");
}

async function pollJob() {
    try {
        state.job = await (await fetch("/api/job")).json();
    } catch (e) {
        return; // transient — keep polling
    }
    const job = state.job;
    if (!job || !job.items) return;
    let sentP = 0, errP = 0, schedP = 0, skipP = 0, totP = 0, errItems = 0;
    job.items.forEach((it) => {
        (it.posts || []).forEach((p) => {
            totP++;
            if (p.status === "sent") sentP++;
            else if (p.status === "error") errP++;
            else if (p.status === "scheduled") schedP++;
            else if (p.status === "skipped_cap") skipP++;
        });
        if (it.status === "error") errItems++;
        const entry = state.cards.get(it.name);
        if (!entry) return;
        const { chip, err } = entry;
        chip.style.display = "";
        if (it.status === "pending") {
            chip.className = "chip chip-run"; chip.textContent = "queued";
        } else if (it.status === "uploading") {
            chip.className = "chip chip-run"; chip.textContent = "uploading…";
        } else if (it.status.startsWith("posting") || it.status.startsWith("scheduling")) {
            chip.className = "chip chip-run"; chip.textContent = it.status + "…";
        } else if (it.status === "done") {
            // accepted by Buffer — the per-network marks refine as we verify
            const posts = it.posts || [];
            const anyErr = posts.some((p) => p.status === "error" || p.status === "skipped_cap");
            const anyPending = posts.some(
                (p) => !["sent", "error", "scheduled", "skipped_cap"].includes(p.status));
            chip.className = "chip " + (anyErr ? "chip-err" : anyPending ? "chip-run" : "chip-done");
            chip.textContent = netSummary(posts) || "✓ accepted";
            const failedPost = posts.find((p) => p.error);
            err.textContent = failedPost ? failedPost.error : "";
            err.title = failedPost ? failedPost.error : "";
        } else if (it.status === "error") {
            chip.className = "chip chip-err"; chip.textContent = "✗ failed";
            err.textContent = it.detail || "error";
            err.title = it.detail || "";
        } else if (it.status === "cancelled") {
            chip.className = "chip chip-err"; chip.textContent = "cancelled";
        }
    });
    const pend = totP - sentP - errP - schedP - skipP;
    const bits = [];
    if (sentP) bits.push(`${sentP} live`);
    if (schedP) bits.push(`${schedP} scheduled`);
    if (skipP) bits.push(`${skipP} skipped (daily cap)`);
    if (errP) bits.push(`${errP} failed — use Retry failed`);
    if (errItems) bits.push(`${errItems} clip(s) errored before posting`);
    $("job-summary").textContent =
        job.state === "running"
            ? `${totP} post(s) created…` + (errItems ? ` · ${errItems} clip(s) failed` : "")
            : job.state === "verifying"
                ? `verifying — ${bits.join(" · ")}` + (pend ? ` · ${pend} publishing…` : "")
                : `${totP} posts: ` + (bits.join(" · ") || "none") +
                  (job.state === "cancelled" ? " · stopped" : "");
    if (!["running", "verifying"].includes(job.state)) {
        clearInterval(state.pollTimer);
        state.pollTimer = null;
        fetchClips();               // refresh posted badges from the ledger
        fetchLimits();              // quota strip just changed
        updateControls();
    }
}

init();
