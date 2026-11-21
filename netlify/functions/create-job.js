const REPO = process.env.GITHUB_REPO || "aka645251-ai/gta-clipper";

async function gh(path, opts = {}) {
  const r = await fetch("https://api.github.com" + path, Object.assign({}, opts, {
    headers: Object.assign({
      Authorization: "Bearer " + process.env.GITHUB_TOKEN,
      Accept: "application/vnd.github+json",
      "Content-Type": "application/json",
      "User-Agent": "gta-clipper-site",
    }, opts.headers || {}),
  }));
  return r;
}

exports.handler = async (event) => {
  if (event.httpMethod !== "POST") return { statusCode: 405, body: "POST only" };
  if (!process.env.GITHUB_TOKEN) return { statusCode: 500, body: JSON.stringify({
    error: "Site pe GITHUB_TOKEN set nahi hai. Netlify: Site configuration, Environment variables me daalo." }) };
  let body;
  try { body = JSON.parse(event.body || "{}"); } catch { return { statusCode: 400, body: "bad json" }; }
  const url = String(body.url || "").trim();
  if (!/youtube\.com|youtu\.be/.test(url)) {
    return { statusCode: 400, body: JSON.stringify({ error: "Sirf YouTube link chalega" }) };
  }
  const start = String(body.start || "00:00:00");
  const seconds = Math.min(Math.max(parseInt(body.seconds, 10) || 60, 10), 180);
  const mode = body.mode === "auto" ? "auto" : "manual";
  const id = new Date().toISOString().replace(/[-:.TZ]/g, "").slice(0, 14) + "-" +
    Math.random().toString(36).slice(2, 6);
  const job = { id, url, start, seconds, mode, status: "queued", created_at: new Date().toISOString() };

  const put = await gh(`/repos/${REPO}/contents/jobs/${id}.json`, { method: "PUT",
    body: JSON.stringify({ message: `clip job ${id} queued`,
      content: Buffer.from(JSON.stringify(job, null, 2)).toString("base64") }) });
  if (!put.ok) return { statusCode: 502, body: JSON.stringify({ error: "GitHub pe job file nahi likhi ja saki: HTTP " + put.status }) };

  const disp = await gh(`/repos/${REPO}/dispatches`, { method: "POST",
    body: JSON.stringify({ event_type: "clip-request", client_payload: job }) });
  if (!disp.ok && disp.status !== 204) {
    return { statusCode: 502, body: JSON.stringify({ error: "GitHub ko trigger nahi kiya ja saka: HTTP " + disp.status }) };
  }
  return { statusCode: 200, body: JSON.stringify(job) };
};
