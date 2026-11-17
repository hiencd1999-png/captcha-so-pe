import base64
import json
import os
import sys
from contextlib import redirect_stdout


REAL_STDOUT = sys.stdout


def emit(payload):
    REAL_STDOUT.write(json.dumps(payload, separators=(",", ":")) + "\n")
    REAL_STDOUT.flush()


def load_model():
    model_path = os.environ.get("YOLO_MODEL_PATH")
    if not model_path:
        raise RuntimeError("YOLO_MODEL_PATH is not set")

    with redirect_stdout(sys.stderr):
        from ultralytics import YOLO

        return YOLO(model_path)


def decode_image(image_b64):
    import cv2
    import numpy as np

    image_bytes = base64.b64decode(image_b64)
    image_array = np.frombuffer(image_bytes, np.uint8)
    image = cv2.imdecode(image_array, cv2.IMREAD_COLOR)
    if image is None:
        raise RuntimeError("Cannot decode image")
    return image


def detect(model, request):
    image = decode_image(request.get("image", ""))
    patch_b64 = request.get("patch", "")
    captcha_type = request.get("captchaType", "Puzzle")

    height, width = image.shape[:2]
    channels = image.shape[2] if len(image.shape) > 2 else 1
    conf_threshold = float(request.get("conf", 0.8))
    offset_x = float(request.get("offsetX", -20))
    offset_y = float(request.get("offsetY", -21))

    points = []
    boxes = []

    # Try YOLO first
    with redirect_stdout(sys.stderr):
        result = model(image, conf=conf_threshold, verbose=False)[0]

    for box in result.boxes:
        x1, y1, x2, y2 = [float(v) for v in box.xyxy[0].tolist()]
        center_x = (x1 + x2) / 2
        center_y = (y1 + y2) / 2
        point = {
            "x": int(center_x + offset_x),
            "y": int(center_y + offset_y),
        }
        points.append(point)
        boxes.append({
            "x1": round(x1, 2),
            "y1": round(y1, 2),
            "x2": round(x2, 2),
            "y2": round(y2, 2),
            "center_x": round(center_x, 2),
            "center_y": round(center_y, 2),
            "x": point["x"],
            "y": point["y"],
            "confidence": round(float(box.conf.item()), 4),
            "class_id": int(box.cls.item()),
        })

    # Template Matching Fallback (especially for Logic/Match Icon)
    if (not points or captcha_type == "Logic") and patch_b64:
        try:
            import cv2
            import numpy as np

            patch = decode_image(patch_b64)
            res = cv2.matchTemplate(image, patch, cv2.TM_CCOEFF_NORMED)
            min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(res)

            # Threshold for template matching
            if max_val > 0.5:
                top_left = max_loc
                h, w = patch.shape[:2]
                center_x = top_left[0] + w/2
                center_y = top_left[1] + h/2

                # If we don't have YOLO points, or this is a "Logic" type, use this
                tm_point = {
                    "x": int(center_x + offset_x),
                    "y": int(center_y + offset_y),
                    "source": "template_matching",
                    "confidence": round(max_val, 4)
                }

                # Check if this point is already found by YOLO (avoid duplication)
                is_duplicate = any(abs(p["x"] - tm_point["x"]) < 10 for p in points)
                if not is_duplicate:
                    points.append(tm_point)
                    # Add to boxes for debugging
                    boxes.append({
                        "x1": top_left[0],
                        "y1": top_left[1],
                        "x2": top_left[0] + w,
                        "y2": top_left[1] + h,
                        "center_x": round(center_x, 2),
                        "center_y": round(center_y, 2),
                        "x": tm_point["x"],
                        "y": tm_point["y"],
                        "confidence": round(max_val, 4),
                        "source": "template_matching"
                    })
        except Exception as e:
            sys.stderr.write(f"[YOLO-TM] Template matching failed: {str(e)}\n")

    points.sort(key=lambda point: point["x"])
    boxes.sort(key=lambda item: item["x"])
    return points, boxes, {
        "width": int(width),
        "height": int(height),
        "channels": int(channels),
    }


def main():
    model = load_model()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        request = None
        try:
            request = json.loads(line)
            points, boxes, image = detect(model, request)
            emit({
                "id": request.get("id"),
                "ok": True,
                "points": points,
                "boxes": boxes,
                "image": image,
            })
        except Exception as error:
            emit({
                "id": request.get("id") if request else None,
                "ok": False,
                "error": str(error),
            })


if __name__ == "__main__":
    main()
