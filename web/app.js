const $ = (id) => document.getElementById(id);
const msg = $("msg");

function chip(s) {
  const c = { queued: "queued", running: "running", done: "done", error: "error" }[s] || "queued";
  return `<span class="chip ${c}">${s}</span>`;
}

function esc(t) {
  return String(t == null ? "" : t).replace(/[&<>"']/g, (m) =>
    ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[m]));
}

async function loadJobs() {
  try {
    const r = await fetch("/api/jobs");
    const jobs = await r.json();
    if (!Array.isArray(jobs)) throw new Error(jobs.error || "jobs nahi mile");
    if (!jobs.length) { $("jobs").innerHTML = '<p class="dim">Abhi koi job nahi. Upar link daalo.</p>'; return; }
    $("jobs").innerHTML = jobs.map((j) => {
      const bits = [];
      bits.push(`<div class="top"><div class="title">${esc(j.title || j.url)}</div>${chip(j.status)}</div>`);
      const meta = [`Mode: ${j.mode}`, j.start ? `Start: ${j.start}` : "", j.seconds ? `Length: ${j.seconds}s` : ""]
        .filter(Boolean).join(" &middot; ");
      bits.push(`<div class="meta">${meta}</div>`);
      if (j.best_moment && j.best_moment.reason) {
        bits.push(`<div class="reason">Best moment ${esc(j.best_moment.start)}-${esc(j.best_moment.end)}: ${esc(j.best_moment.reason)}</div>`);
      }
      if (j.steps && Object.keys(j.steps).length) {
        bits.push(`<div class="meta">${Object.entries(j.steps).map(([k, v]) => esc(k + ": " + v)).join(" &middot; ")}</div>`);
      }
      if (j.error) bits.push(`<div class="errline">${esc(j.error)}</div>`);
      if (j.status === "done" && j.clip && j.clip.path) {
        bits.push(`<a class="btn" href="/api/jobs?file=${encodeURIComponent(j.clip.path)}">Download clip (${esc(j.clip.size_mb || "?")} MB)</a>`);
      }
      return `<div class="job">${bits.join("")}</div>`;
    }).join("");
  } catch (e) {
    $("jobs").innerHTML = `<p class="errline">${esc(e.message)}</p>`;
  }
}

$("go").addEventListener("click", async () => {
  const url = $("url").value.trim();
  if (!url) { msg.textContent = "Pehle YouTube link daalo."; msg.className = "msg err"; return; }
  $("go").disabled = true;
  msg.textContent = "Job bhej raha hoon..."; msg.className = "msg";
  try {
    const r = await fetch("/api/create-job", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url, start: $("start").value, seconds: $("seconds").value, mode: $("mode").value }),
    });
    const data = await r.json();
    if (!r.ok) throw new Error(data.error || "HTTP " + r.status);
    msg.textContent = "Job " + data.id + " chalu. Neeche status dikh raha hoga.";
    $("url").value = "";
    loadJobs();
  } catch (e) {
    msg.textContent = e.message; msg.className = "msg err";
  }
  $("go").disabled = false;
});

loadJobs();
setInterval(loadJobs, 12000);
