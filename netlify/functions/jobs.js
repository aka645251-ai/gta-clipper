const REPO = process.env.GITHUB_REPO || "aka645251-ai/gta-clipper";

async function gh(path, opts = {}) {
  const r = await fetch("https://api.github.com" + path, Object.assign({}, opts, {
    headers: Object.assign({
      Authorization: "Bearer " + process.env.GITHUB_TOKEN,
      Accept: "application/vnd.github+json",
      "User-Agent": "gta-clipper-site",
    }, opts.headers || {}),
  }));
  return r;
}

exports.handler = async (event) => {
  if (!process.env.GITHUB_TOKEN) return { statusCode: 500, body: JSON.stringify({
    error: "Site pe GITHUB_TOKEN set nahi hai." }) };
  const q = event.queryStringParameters || {};

  if (q.file) {
    const safe = q.file.replace(/[^A-Za-z0-9._/-]/g, "");
    if (!safe.startsWith("out/") || safe.includes("..")) return { statusCode: 400, body: "bad path" };
    const r = await gh(`/repos/${REPO}/contents/${safe}`, { headers: { Accept: "application/vnd.github.raw" } });
    if (!r.ok) return { statusCode: 404, body: "not found" };
    const buf = Buffer.from(await r.arrayBuffer());
    return { statusCode: 200, isBase64Encoded: true,
      headers: { "Content-Type": "video/mp4", "Content-Length": String(buf.length),
        "Content-Disposition": `attachment; filename="${safe.split("/").pop()}"` },
      body: buf.toString("base64") };
  }

  const r = await gh(`/repos/${REPO}/contents/jobs`);
  if (r.status === 404) return { statusCode: 200, body: "[]" };
  if (!r.ok) return { statusCode: 502, body: JSON.stringify({ error: "GitHub se jobs nahi mile: HTTP " + r.status }) };
  const items = JSON.parse(await r.text());
  const out = [];
  for (const it of items.slice(-25).reverse()) {
    try {
      const j = await gh(`/repos/${REPO}/contents/jobs/${it.name}`);
      if (j.ok) out.push(JSON.parse(await j.text()));
    } catch (e) { /* skip a bad file */ }
  }
  return { statusCode: 200, body: JSON.stringify(out) };
};
