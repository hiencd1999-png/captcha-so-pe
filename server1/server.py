import os
import json
import base64
import hashlib
import csv
import io
import requests
from datetime import datetime
from flask import Flask, request, jsonify
from flask_cors import CORS
import cv2
import numpy as np
from ultralytics import YOLO

app = Flask(__name__)
CORS(app)

# --- Configuration ---
PORT = int(os.environ.get("PORT", 10000))
HOST = os.environ.get("HOST", "0.0.0.0")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATASET_DIR = os.environ.get("DATASET_DIR", os.path.join(BASE_DIR, "data", "dataset"))
# IMAGES_DIR đã được loại bỏ theo yêu cầu của bạn
LABELS_PATH_LOCAL = os.path.join(DATASET_DIR, "labels.csv")
LABELS_URL = os.environ.get(
    "LABELS_URL",
    "https://raw.githubusercontent.com/hiencd1999-png/captcha-so-pe/main/server1/data/dataset/labels.csv",
)
MODEL_PATH = os.environ.get("YOLO_MODEL_PATH", os.path.join(BASE_DIR, "best.pt"))
SAVE_MODE = os.environ.get("CAPTURE_SAVE_MODE", "insert-only")

# Default YOLO offsets
YOLO_CONF_DEFAULT = float(os.environ.get("YOLO_CONF", 0.8))
OFFSET_X_DEFAULT = float(os.environ.get("YOLO_OFFSET_X", -20))
OFFSET_Y_DEFAULT = float(os.environ.get("YOLO_OFFSET_Y", -21))

# --- Initialization ---
os.makedirs(DATASET_DIR, exist_ok=True)
print(f"[Server] Loading YOLO model from {MODEL_PATH}...")
try:
    model = YOLO(MODEL_PATH)
except Exception as e:
    print(f"[Error] Failed to load YOLO model: {e}")
    model = None

# --- Helpers ---

def decode_image(image_b64):
    if not image_b64:
        return None, None
    try:
        if "," in image_b64:
            header, image_b64 = image_b64.split(",")
        image_bytes = base64.b64decode(image_b64)
        image_array = np.frombuffer(image_bytes, np.uint8)
        image = cv2.imdecode(image_array, cv2.IMREAD_COLOR)
        return image, image_bytes
    except Exception as e:
        print(f"[Error] Image decoding failed: {e}")
        return None, None

_REMOTE_LABELS_CACHE = None
_LAST_FETCH_TIME = None
CACHE_TTL = 60  # Cache for 60 seconds

def get_remote_labels():
    """Tải labels từ GitHub URL với cache 60s"""
    global _REMOTE_LABELS_CACHE, _LAST_FETCH_TIME

    now = datetime.now()
    if _REMOTE_LABELS_CACHE is not None and _LAST_FETCH_TIME is not None:
        if (now - _LAST_FETCH_TIME).total_seconds() < CACHE_TTL:
            return _REMOTE_LABELS_CACHE

    try:
        print(f"[Server] Fetching remote labels from {LABELS_URL}...")
        response = requests.get(LABELS_URL, timeout=5)
        response.raise_for_status()
        f = io.StringIO(response.text)
        reader = csv.DictReader(f)
        _REMOTE_LABELS_CACHE = list(reader)
        _LAST_FETCH_TIME = now
        print(f"[Server] Successfully fetched {len(_REMOTE_LABELS_CACHE)} labels.")
        return _REMOTE_LABELS_CACHE
    except Exception as e:
        print(f"[Error] Failed to fetch remote labels: {e}")
        # Trả về cache cũ nếu có thay vì danh sách trống khi lỗi mạng
        return _REMOTE_LABELS_CACHE if _REMOTE_LABELS_CACHE is not None else []

def get_cached_label(img_id):
    """Tìm kết quả trong labels dựa trên mã hash (ưu tiên GitHub, sau đó tới local)"""
    # 1. Kiểm tra từ Remote URL
    remote_rows = get_remote_labels()
    for row in remote_rows:
        if row.get("image") == img_id or str(row.get("image")).startswith(img_id):
            return float(row["slider_x"])

    # 2. Kiểm tra từ Local file
    if os.path.exists(LABELS_PATH_LOCAL):
        with open(LABELS_PATH_LOCAL, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row.get("image") == img_id or row.get("image").startswith(img_id):
                    return float(row["slider_x"])
    return None

def write_label(img_id, slider_x):
    """Lưu label mới vào file local"""
    rows = []
    if os.path.exists(LABELS_PATH_LOCAL):
        with open(LABELS_PATH_LOCAL, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            if reader.fieldnames:
                rows = list(reader)

    # Ở đây 'image' sẽ đóng vai trò là mã hash (vì không có file ảnh thật)
    found = False
    for row in rows:
        if row.get("image") == img_id:
            row["slider_x"] = str(slider_x)
            found = True
            break

    if not found:
        rows.append({"image": img_id, "slider_x": str(slider_x)})

    rows.sort(key=lambda x: x["image"])
    with open(LABELS_PATH_LOCAL, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["image", "slider_x"])
        writer.writeheader()
        writer.writerows(rows)

# --- Routes ---

@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "ok": True,
        "model_loaded": model is not None,
        "using_labels_only": True
    })

@app.route("/api/captures", methods=["GET"])
def list_captures():
    captures = []
    # 1. Thêm từ remote
    remote_rows = get_remote_labels()
    for row in remote_rows:
        captures.append({
            "id": row["image"],
            "sliderX": row["slider_x"],
            "source": "remote"
        })

    # 2. Thêm từ local
    if os.path.exists(LABELS_PATH_LOCAL):
        with open(LABELS_PATH_LOCAL, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                captures.append({
                    "id": row["image"],
                    "sliderX": row["slider_x"],
                    "source": "local"
                })
    return jsonify({"captures": captures})

@app.route("/api/captures", methods=["POST"])
def save_capture():
    data = request.json
    if not data or "backgroundImage" not in data or "sliderX" not in data:
        return jsonify({"ok": False, "error": "Missing data"}), 400

    img_b64 = data["backgroundImage"]
    slider_x = data["sliderX"]

    _, img_bytes = decode_image(img_b64)
    if not img_bytes:
        return jsonify({"ok": False, "error": "Invalid image"}), 400

    img_id = hashlib.sha256(img_bytes).hexdigest()

    # Lưu vào CSV (không lưu file ảnh)
    write_label(img_id, slider_x)

    return jsonify({
        "ok": True,
        "msg": "Label saved (CSV only)",
        "capture": { "id": img_id, "sliderX": slider_x }
    }), 201

@app.route("/detect", methods=["POST"])
@app.route("/api/detect", methods=["POST"])
def detect():
    data = request.json
    img_b64 = data.get("image", "")

    image, img_bytes = decode_image(img_b64)
    if image is None:
        return jsonify({"ok": False, "error": "Invalid image"}), 400

    # Bước 1: Kiểm tra trong labels.csv trước (Cache lookup)
    img_id = hashlib.sha256(img_bytes).hexdigest()
    cached_x = get_cached_label(img_id)

    if cached_x is not None:
        print(f"[Server] Cache hit for {img_id[:10]}...")
        return jsonify({
            "ok": True,
            "source": "csv_cache",
            "points": [{"x": int(cached_x), "y": 160}], # y mặc định
            "boxes": []
        })

    # Bước 2: Nếu không thấy trong CSV, mới dùng YOLO để nhận diện
    if model is None:
        return jsonify({"ok": False, "error": "Model not loaded and no cache found"}), 500

    results = model(image, conf=float(data.get("conf", YOLO_CONF_DEFAULT)), verbose=False)
    points = []
    if results:
        for box in results[0].boxes:
            x1, y1, x2, y2 = [float(v) for v in box.xyxy[0].tolist()]
            points.append({
                "x": int((x1 + x2) / 2 + float(data.get("offsetX", OFFSET_X_DEFAULT))),
                "y": int((y1 + y2) / 2 + float(data.get("offsetY", OFFSET_Y_DEFAULT)))
            })

    return jsonify({
        "ok": True,
        "source": "yolo",
        "points": sorted(points, key=lambda p: p["x"])
    })

if __name__ == "__main__":
    app.run(host=HOST, port=PORT, debug=False)
