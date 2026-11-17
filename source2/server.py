import cv2
import numpy as np
import base64
import os
from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)  # Enable CORS for browser extension

DATA_DIR = os.path.dirname(os.path.abspath(__file__))

def solve_match_img(piece_img, data_dir):
    if piece_img is None:
        return None, "Piece image is None", 0

    best_match = None
    max_val = -1.0
    best_coords = (0, 0)

    # Convert piece to grayscale for potentially faster/more robust matching
    # or keep color if color is important.
    if piece_img.shape[2] == 4:
        piece_gray = cv2.cvtColor(piece_img[:, :, :3], cv2.COLOR_BGR2GRAY)
        piece_mask = piece_img[:, :, 3]
    else:
        piece_gray = cv2.cvtColor(piece_img, cv2.COLOR_BGR2GRAY)
        piece_mask = None

    for filename in os.listdir(data_dir):
        if not filename.endswith('.png'):
            continue

        target_path = os.path.join(data_dir, filename)
        target_img = cv2.imread(target_path, cv2.IMREAD_UNCHANGED)

        if target_img is None:
            continue

        # Target must be same size or larger than piece for matchTemplate
        # If target matches piece exactly, result will be a 1x1 map with 1.0 value
        if target_img.shape[:2] != piece_img.shape[:2]:
            target_img = cv2.resize(target_img, (piece_img.shape[1], piece_img.shape[0]))

        if target_img.shape[2] == 4:
            target_gray = cv2.cvtColor(target_img[:, :, :3], cv2.COLOR_BGR2GRAY)
        else:
            target_gray = cv2.cvtColor(target_img, cv2.COLOR_BGR2GRAY)

        # Use normalized cross-correlation
        res = cv2.matchTemplate(target_gray, piece_gray, cv2.TM_CCOEFF_NORMED, mask=piece_mask)
        _, val, _, _ = cv2.minMaxLoc(res)

        if val > max_val:
            max_val = val
            best_match = filename
            try:
                coords_part = filename.replace('.png', '').split('_')
                best_coords = (int(coords_part[0]), int(coords_part[1]))
            except:
                pass

    return best_coords, best_match, max_val

@app.route('/solve', methods=['POST'])
def solve():
    data = request.json
    if not data or 'image' not in data:
        return jsonify({"error": "No image data provided"}), 400

    piece_b64 = data['image']
    encoded_data = piece_b64.split(',')[-1]

    try:
        nparr = np.frombuffer(base64.b64decode(encoded_data), np.uint8)
        piece_img = cv2.imdecode(nparr, cv2.IMREAD_UNCHANGED)
    except Exception as e:
        return jsonify({"error": f"Failed to decode image: {e}"}), 400

    if piece_img is None:
        return jsonify({"error": "Failed to decode image (None)"}), 400

    coords, match, confidence = solve_match_img(piece_img, DATA_DIR)

    return jsonify({
        "left": coords[0],
        "top": coords[1],
        "match": match,
        "confidence": float(confidence)
    })

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000, debug=True)
