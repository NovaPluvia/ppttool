#!/usr/bin/env python3
"""SAM segmentation service for the image-to-editable-ppt annotator.

Provides both an HTTP API (for the HTML annotator tool) and CLI usage.

HTTP API:
  python sam_service.py --port 5678
  POST /segment  { "image_path": "str or base64", "click": [x, y] }
  GET  /health   → { "status": "ok", "model": "mobilesam" }

CLI usage:
  python sam_service.py --image input.png --click 500,300 --output mask.png
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

try:
    from flask import Flask, jsonify, request
except ImportError:
    Flask = None

# ---------- SAM utilities ----------

MODEL_DIR = Path(__file__).resolve().parent.parent / "models"
MODEL_URL = "https://github.com/ChaoningZhang/MobileSAM/raw/master/weights/mobile_sam.pt"
MODEL_PATH = MODEL_DIR / "mobile_sam.pt"


def download_model() -> Path:
    """Download MobileSAM checkpoint if not present."""
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    if MODEL_PATH.exists():
        return MODEL_PATH
    print(f"Downloading MobileSAM model ({MODEL_URL})...")
    import urllib.request
    urllib.request.urlretrieve(MODEL_URL, MODEL_PATH)
    print(f"Model saved to {MODEL_PATH}")
    return MODEL_PATH


def load_sam_model():
    """Load MobileSAM model, returns a predictor object."""
    try:
        from mobile_sam import sam_model_registry, SamPredictor
    except ImportError:
        print("ERROR: mobile_sam package not installed. Run: pip install mobile-sam")
        sys.exit(1)

    ckpt = download_model()
    device = "cuda" if __import__("torch").cuda.is_available() else "cpu"
    print(f"Loading MobileSAM from {ckpt} (device={device})...")
    t0 = time.time()
    sam = sam_model_registry["vit_t"](checkpoint=str(ckpt))
    sam.to(device)
    predictor = SamPredictor(sam)
    print(f"Model loaded in {time.time() - t0:.1f}s")
    return predictor, device


def segment_with_click(
    predictor,
    image: np.ndarray,
    click_x: int,
    click_y: int,
) -> list[list[float]]:
    """Run SAM inference with a single click point.

    Returns a polygon as a list of [x, y] coordinates (image-space).
    """
    predictor.set_image(image)

    input_point = np.array([[click_x, click_y]])
    input_label = np.array([1])  # foreground

    masks, scores, _ = predictor.predict(
        point_coords=input_point,
        point_labels=input_label,
        multimask_output=True,
    )

    # Pick the highest-scoring mask
    best_idx = int(np.argmax(scores))
    mask = masks[best_idx]  # [H, W] bool

    # Convert mask to polygon using marching squares via mask-to-polygon
    polygon = mask_to_polygon(mask)

    return polygon


def mask_to_polygon(mask: np.ndarray, simplify_tol: float = 1.0) -> list[list[float]]:
    """Convert a boolean mask to a simplified polygon.

    Uses contour finding, then simplification.
    """
    import cv2

    mask_uint8 = (mask * 255).astype(np.uint8)
    contours, _ = cv2.findContours(mask_uint8, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    if not contours:
        return []

    # Largest contour by area
    largest = max(contours, key=cv2.contourArea)
    polygon = largest.squeeze(axis=1).tolist()  # [[x, y], ...]

    # Simplify
    if simplify_tol > 0 and len(polygon) > 5:
        polygon = simplify_polygon(polygon, simplify_tol)

    return polygon


def simplify_polygon(poly: list[list[float]], epsilon: float = 1.0) -> list[list[float]]:
    """Douglas-Peucker simplification."""
    if len(poly) <= 2:
        return poly

    import cv2
    arr = np.array(poly, dtype=np.float32).reshape((-1, 1, 2))
    simplified = cv2.approxPolyDP(arr, epsilon, closed=True)
    return simplified.squeeze(axis=1).tolist()


# ---------- HTTP Server ----------

def create_app(predictor):
    app = Flask(__name__)

    @app.route("/health", methods=["GET"])
    def health():
        return jsonify({"status": "ok", "model": "mobilesam"})

    @app.route("/segment", methods=["POST"])
    def segment():
        data = request.get_json()
        if not data:
            return jsonify({"error": "No JSON body"}), 400

        click = data.get("click")
        if not click or len(click) != 2:
            return jsonify({"error": "click must be [x, y]"}), 400

        image_path = data.get("image_path", "")
        if not image_path:
            return jsonify({"error": "image_path required"}), 400

        # Load image: either file path or base64 data URL
        try:
            if image_path.startswith("data:image/"):
                # Base64 data URL from the HTML annotator
                header, encoded = image_path.split(",", 1)
                img_bytes = base64.b64decode(encoded)
                img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
            else:
                img = Image.open(image_path).convert("RGB")
        except Exception as e:
            return jsonify({"error": f"Cannot load image: {e}"}), 400

        image_np = np.array(img)

        try:
            polygon = segment_with_click(predictor, image_np, int(click[0]), int(click[1]))
            return jsonify({"polygon": polygon})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    return app


# ---------- CLI mode ----------

def cli_segment(image_path: str, click_x: int, click_y: int, output: str | None):
    """Run SAM segmentation from command line."""
    predictor, device = load_sam_model()
    img = Image.open(image_path).convert("RGB")
    image_np = np.array(img)
    polygon = segment_with_click(predictor, image_np, click_x, click_y)

    if output:
        # Create mask PNG
        mask = np.zeros((img.height, img.width), dtype=np.uint8)
        if polygon:
            import cv2
            pts = np.array([[int(x), int(y)] for x, y in polygon], dtype=np.int32).reshape((-1, 1, 2))
            cv2.fillPoly(mask, [pts], 255)
        Image.fromarray(mask).save(output)
        print(f"Mask saved to {output}")

    # Output polygon as JSON
    result = {"polygon": polygon, "click": [click_x, click_y]}
    print(json.dumps(result, ensure_ascii=False))
    return polygon


# ---------- Main ----------

def main():
    parser = argparse.ArgumentParser(description="MobileSAM segmentation service for PPT annotator")
    parser.add_argument("--port", type=int, default=5678, help="HTTP server port")
    parser.add_argument("--image", help="Image path (CLI mode)")
    parser.add_argument("--click", help="Click coordinates as x,y (CLI mode)")
    parser.add_argument("--output", help="Output mask PNG path (CLI mode)")
    args = parser.parse_args()

    if args.image and args.click:
        # CLI mode
        cx, cy = map(int, args.click.split(","))
        cli_segment(args.image, cx, cy, args.output)
    else:
        # HTTP server mode
        if Flask is None:
            print("ERROR: Flask not installed. Run: pip install flask flask-cors")
            sys.exit(1)

        predictor, device = load_sam_model()
        app = create_app(predictor)

        # Enable CORS for the HTML annotator
        from flask_cors import CORS
        CORS(app)

        print(f"SAM service ready on http://localhost:{args.port}")
        print(f"Device: {device}")
        print("Press Ctrl+C to stop.")
        app.run(host="0.0.0.0", port=args.port, debug=False)


if __name__ == "__main__":
    main()
