// Local contract fixture from docs/HUONG_DAN_TRIEN_KHAI_VA_TICH_HOP.md, B2.
// Fake keys select responses; no production key or machine ID is needed.
const http = require("http");
const zlib = require("zlib");

// Minimal source release for updater integration tests; launches a real Python
// process that acknowledges startup. It deliberately needs no GUI or real key.
function sourceRelease() {
  const files = {
    "main.py": "import json, os\nfrom pathlib import Path\np = os.environ.get('REUP_UPDATE_READY')\nif p: Path(p).write_text(json.dumps({'version': '1.1.0', 'app_id': 'tool_reup_video_pro'}), encoding='utf-8')\n",
    "license.py": "APP_ID = 'tool_reup_video_pro'\nAPP_VERSION = '1.1.0'\n",
    "edition.py": "# Test release module\n",
    "updater.py": "# Test release module\n", "core.py": "# Test release module\n",
    "app_paths.py": "# Test release module\n",
    "drive.py": "# Test release module\n", "tts.py": "# Test release module\n",
    "error_text.py": "# Test release module\n", "single_instance.py": "# Test release module\n"
  };
  const chunks = [];
  for (const [name, text] of Object.entries(files)) {
    const data = Buffer.from(text);
    const header = Buffer.alloc(512);
    header.write("release/" + name);
    header.write("0000644\0", 100);
    header.write("0000000\0", 108);
    header.write("0000000\0", 116);
    header.write(data.length.toString(8).padStart(11, "0") + "\0", 124);
    header.write("00000000000\0", 136);
    header.fill(32, 148, 156);
    header.write("0", 156);
    header.write("ustar\0", 257);
    header.write("00", 263);
    const sum = header.reduce((a, b) => a + b, 0);
    header.write(sum.toString(8).padStart(6, "0") + "\0 ", 148);
    chunks.push(header, data, Buffer.alloc((512 - data.length % 512) % 512));
  }
  chunks.push(Buffer.alloc(1024));
  return zlib.gzipSync(Buffer.concat(chunks));
}
const release = sourceRelease();

http.createServer((req, res) => {
  function json(status, body) {
    res.writeHead(status, { "Content-Type": "application/json" });
    res.end(JSON.stringify(body));
  }
  if (req.method === "GET" && req.url.startsWith("/releases/")) {
    if (req.url === "/releases/fail") return json(503, {message: "Unavailable"});
    const data = req.url === "/releases/corrupt" ? release.subarray(0, 40) : release;
    if (!["/releases/source.tar.gz", "/releases/unknown-size", "/releases/corrupt"].includes(req.url)) {
      return json(501, {message: "Release not modeled"});
    }
    const headers = {"Content-Type": "application/gzip"};
    if (req.url !== "/releases/unknown-size") headers["Content-Length"] = String(data.length);
    res.writeHead(200, headers);
    if (req.url === "/releases/unknown-size") { res.write(data); return res.end(); }
    return res.end(data);
  }
  if (req.method !== "POST" || req.url !== "/api/validate-key") {
    return json(501, { message: "Endpoint not modeled" });
  }
  let raw = "";
  req.on("data", chunk => { raw += chunk; });
  req.on("end", () => {
    let body;
    try { body = JSON.parse(raw); }
    catch (_) { return json(422, { message: "Invalid JSON" }); }
    if (!body.key || body.app_id !== "tool_reup_video_pro" ||
        body.deviceId !== "test-device" ||
        !/^\d+\.\d+\.\d+$/.test(body.current_version || "") ||
        !["win", "mac", "linux"].includes(body.platform)) {
      return json(422, { message: "Request does not match B2 contract" });
    }
    if (body.key === "SERVER_ERROR") {
      return json(503, { message: "Service unavailable" });
    }
    if (body.key === "BAD_JSON") {
      res.writeHead(200, { "Content-Type": "text/html" });
      return res.end("<html>Upstream unavailable</html>");
    }
    if (body.key === "BAD_BODY") { return json(200, []); }
    if (body.key === "INVALID") {
      return json(200, { status: 0, message: "Key không hợp lệ" });
    }
    if (!["CURRENT", "OPTIONAL", "FORCED"].includes(body.key)) {
      return json(200, { status: 0, message: "Key không hợp lệ" });
    }
    const update = body.key === "CURRENT" ? null : {
      has_update: true,
      force_update: body.key === "FORCED",
      current_version: body.current_version,
      latest_version: "1.1.0",
      min_version: body.key === "FORCED" ? "1.1.0" : "1.0.0",
      download_url: `https://downloads.example.com/reup/1.1.0/${body.platform}.zip`,
      changelog: "Bản cập nhật kiểm thử cục bộ.",
      published_at: "2026-10-24 00:00:00"
    };
    return json(200, {
      status: 1, message: "Key hợp lệ",
      data: { key: body.key, app_id: body.app_id, device_id: body.deviceId,
              expire_date: "2026-12-31 23:59:59" },
      update
    });
  });
}).listen(process.env.PORT || 4917);
