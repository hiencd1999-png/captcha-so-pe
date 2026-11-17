const http = require("http");
const fs = require("fs");
const path = require("path");
const crypto = require("crypto");
const { spawn } = require("child_process");
const readline = require("readline");

const PORT = Number(process.env.PORT || 10000);
const HOST = process.env.HOST || "0.0.0.0";
const CAPTURE_SAVE_MODE = normalizeSaveMode(process.env.CAPTURE_SAVE_MODE || "insert-only"); // update/insert-only
const DATASET_DIR = path.join(__dirname, "data", "dataset");
const IMAGES_DIR = path.join(DATASET_DIR, "images");
const LABELS_PATH = path.join(DATASET_DIR, "labels.csv");
const PYTHON_BIN = process.env.PYTHON_BIN || "python";
const YOLO_MODEL_PATH = path.resolve(
  process.env.YOLO_MODEL_PATH || path.join(__dirname, "best.pt"),
);
const YOLO_CONF = Number(process.env.YOLO_CONF || 0.8);
const YOLO_OFFSET_X = Number(process.env.YOLO_OFFSET_X || -20);
const YOLO_OFFSET_Y = Number(process.env.YOLO_OFFSET_Y || -21);

let yoloWorker = null;
let yoloRequestId = 0;
const yoloPending = new Map();

function normalizeSaveMode(value) {
  const mode = String(value || "").trim().toLowerCase();
  if (["insert-only", "insert_only", "new-only", "new_only", "append-only", "append_only", "2"].includes(mode)) {
    return "insert-only";
  }
  return "update";
}

function ensureDir(dir) {
  fs.mkdirSync(dir, { recursive: true });
}

function sendJson(res, statusCode, payload) {
  const body = JSON.stringify(payload, null, 2);
  res.writeHead(statusCode, {
    "Content-Type": "application/json; charset=utf-8",
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type, bypass-tunnel-reminder",
  });
  res.end(body);
}

function requestPath(req) {
  return new URL(req.url, `http://${req.headers.host || "localhost"}`).pathname;
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    let body = "";
    req.on("data", (chunk) => {
      body += chunk;
      if (body.length > 30 * 1024 * 1024) {
        reject(new Error("Request body is too large"));
        req.destroy();
      }
    });
    req.on("end", () => resolve(body));
    req.on("error", reject);
  });
}

function parseDataUrl(value) {
  if (typeof value !== "string") return null;
  const match = value.match(/^data:([^;,]+);base64,([\s\S]+)$/);
  if (!match) return null;

  const mime = match[1].toLowerCase();
  const buffer = Buffer.from(match[2], "base64");
  const extension = detectImageExtension(buffer, mime);
  if (!extension) return null;

  return { mime, extension, buffer };
}

function detectImageExtension(buffer, mime) {
  if (buffer.subarray(0, 8).equals(Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]))) {
    return "png";
  }
  if (buffer[0] === 0xff && buffer[1] === 0xd8 && buffer[2] === 0xff) {
    return "jpg";
  }
  if (
    buffer.subarray(0, 4).toString("ascii") === "RIFF" &&
    buffer.subarray(8, 12).toString("ascii") === "WEBP"
  ) {
    return "webp";
  }

  const extensionByMime = {
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
  };
  return extensionByMime[mime] || null;
}

function numericOrNull(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function sanitizeCapture(payload) {
  const background = parseDataUrl(payload.backgroundImage);

  if (!background) throw new Error("Missing or invalid backgroundImage data URL");
  const sliderX = numericOrNull(payload.sliderX);
  if (sliderX === null) throw new Error("Missing or invalid sliderX");

  return {
    sliderX,
    background,
  };
}

function saveCapture(payload) {
  ensureDir(IMAGES_DIR);

  const capture = sanitizeCapture(payload);
  const id = crypto
    .createHash("sha256")
    .update(capture.background.buffer)
    .digest("hex");
  const imageFile = `${id}.${capture.background.extension}`;
  const imagePath = path.join(IMAGES_DIR, imageFile);
  const existed = fs.existsSync(imagePath);
  const labelExisted = hasLabel(imageFile);
  const skipExisting = CAPTURE_SAVE_MODE === "insert-only" && (existed || labelExisted);

  if (!existed && !skipExisting) {
    fs.writeFileSync(imagePath, capture.background.buffer);
  }

  if (!skipExisting) {
    upsertLabel(imageFile, capture.sliderX);
  }

  return {
    id,
    existed,
    labelExisted,
    saved: !skipExisting,
    skipped: skipExisting,
    mode: CAPTURE_SAVE_MODE,
    sliderX: capture.sliderX,
    files: {
      image: path.join("images", imageFile),
      labels: "labels.csv",
    },
  };
}

function parseLabels() {
  if (!fs.existsSync(LABELS_PATH)) return [];

  return fs
    .readFileSync(LABELS_PATH, "utf8")
    .trim()
    .split(/\r?\n/)
    .slice(1)
    .filter(Boolean)
    .map((line) => {
      const [image, sliderX] = line.split(",");
      return { image, sliderX: Number(sliderX) };
    });
}

function writeLabels(rows) {
  ensureDir(DATASET_DIR);
  const body = ["image,slider_x", ...rows.map((row) => `${row.image},${row.sliderX}`)].join("\n");
  fs.writeFileSync(LABELS_PATH, `${body}\n`);
}

function hasLabel(image) {
  return parseLabels().some((row) => row.image === image);
}

function upsertLabel(image, sliderX) {
  const rows = parseLabels();
  const existing = rows.find((row) => row.image === image);

  if (existing) {
    existing.sliderX = sliderX;
  } else {
    rows.push({ image, sliderX });
  }

  rows.sort((a, b) => a.image.localeCompare(b.image));
  writeLabels(rows);
}

function listCaptures() {
  ensureDir(IMAGES_DIR);
  return parseLabels().map((row) => ({
    id: path.parse(row.image).name,
    sliderX: row.sliderX,
    files: {
      image: path.join("images", row.image),
      labels: "labels.csv",
    },
  }));
}

function getYoloWorker() {
  if (yoloWorker && !yoloWorker.killed) return yoloWorker;

  if (!fs.existsSync(YOLO_MODEL_PATH)) {
    throw new Error(`YOLO model not found: ${YOLO_MODEL_PATH}`);
  }

  const workerPath = path.join(__dirname, "yolo_worker.py");
  console.log(`[Server] Starting YOLO worker with: ${PYTHON_BIN}`);

  yoloWorker = spawn(PYTHON_BIN, [workerPath], {
    cwd: __dirname,
    env: {
      ...process.env,
      YOLO_MODEL_PATH,
    },
    stdio: ["pipe", "pipe", "pipe"],
  });

  const rl = readline.createInterface({ input: yoloWorker.stdout });
  rl.on("line", (line) => {
    let payload;
    try {
      payload = JSON.parse(line);
    } catch (error) {
      console.warn(`[YOLO] Non-JSON stdout: ${line}`);
      return;
    }

    const pending = yoloPending.get(payload.id);
    if (!pending) return;
    yoloPending.delete(payload.id);

    if (payload.ok) {
      pending.resolve(payload);
    } else {
      pending.reject(new Error(payload.error || "YOLO worker failed"));
    }
  });

  yoloWorker.stderr.on("data", (chunk) => {
    const text = chunk.toString().trim();
    if (text) console.warn(`[YOLO] ${text}`);
  });

  yoloWorker.on("exit", (code, signal) => {
    const error = new Error(`YOLO worker exited: code=${code} signal=${signal}`);
    for (const pending of yoloPending.values()) {
      pending.reject(error);
    }
    yoloPending.clear();
    yoloWorker = null;
  });

  return yoloWorker;
}

function detectPoints(payload) {
  const image = typeof payload.image === "string"
    ? payload.image.replace(/^data:[^;,]+;base64,/, "").replace(/\s/g, "")
    : "";

  if (!image) {
    throw new Error("Missing image");
  }

  const worker = getYoloWorker();
  const id = String(++yoloRequestId);
  const conf = numericOrNull(payload.conf);
  const offsetX = numericOrNull(payload.offsetX);
  const offsetY = numericOrNull(payload.offsetY);
  const patch = typeof payload.patch === "string"
    ? payload.patch.replace(/^data:[^;,]+;base64,/, "").replace(/\s/g, "")
    : "";

  const request = {
    id,
    image,
    patch,
    conf: conf === null ? YOLO_CONF : conf,
    offsetX: offsetX === null ? YOLO_OFFSET_X : offsetX,
    offsetY: offsetY === null ? YOLO_OFFSET_Y : offsetY,
    captchaType: payload.captchaType || "Puzzle",
  };

  return new Promise((resolve, reject) => {
    yoloPending.set(id, { resolve, reject });
    worker.stdin.write(`${JSON.stringify(request)}\n`, (error) => {
      if (error) {
        yoloPending.delete(id);
        reject(error);
      }
    });

    setTimeout(() => {
      if (!yoloPending.has(id)) return;
      yoloPending.delete(id);
      reject(new Error("YOLO detect timeout"));
    }, 30000);
  });
}

const server = http.createServer(async (req, res) => {
  const pathname = requestPath(req);

  if (req.method === "OPTIONS") {
    return sendJson(res, 204, {});
  }

  if (req.method === "GET" && pathname === "/health") {
    return sendJson(res, 200, {
      ok: true,
      captureSaveMode: CAPTURE_SAVE_MODE,
      yolo: {
        model: YOLO_MODEL_PATH,
        modelExists: fs.existsSync(YOLO_MODEL_PATH),
        conf: YOLO_CONF,
        offsetX: YOLO_OFFSET_X,
        offsetY: YOLO_OFFSET_Y,
      },
    });
  }

  if (req.method === "GET" && pathname === "/api/captures") {
    return sendJson(res, 200, { captures: listCaptures() });
  }

  if (req.method === "POST" && pathname === "/api/captures") {
    try {
      const body = await readBody(req);
      const payload = JSON.parse(body || "{}");
      const metadata = saveCapture(payload);
      return sendJson(res, 201, { ok: true, capture: metadata });
    } catch (error) {
      return sendJson(res, 400, { ok: false, error: error.message });
    }
  }

  if (req.method === "POST" && (pathname === "/detect" || pathname === "/api/detect" || pathname === "/detech")) {
    try {
      const body = await readBody(req);
      const payload = JSON.parse(body || "{}");
      const result = await detectPoints(payload);
      return sendJson(res, 200, {
        ok: true,
        points: result.points,
        boxes: result.boxes,
        image: result.image,
      });
    } catch (error) {
      return sendJson(res, 500, { ok: false, error: error.message });
    }
  }

  return sendJson(res, 404, { ok: false, error: "Not found" });
});

server.listen(PORT, HOST, () => {
  console.log(`Capture server listening on http://${HOST}:${PORT}`);
  console.log(`Local URL: http://localhost:${PORT}`);
  console.log(`Saving dataset to ${DATASET_DIR}`);
  console.log(`Capture save mode: ${CAPTURE_SAVE_MODE}`);
  console.log(`YOLO detect endpoint: http://localhost:${PORT}/detect`);
  console.log(`YOLO model: ${YOLO_MODEL_PATH}`);
});

process.on("exit", () => {
  if (yoloWorker && !yoloWorker.killed) {
    yoloWorker.kill();
  }
});
