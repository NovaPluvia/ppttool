#!/usr/bin/env python3
"""Ollama bridge service for Qwen-VL image analysis.

Serves both API endpoints AND the annotation tool web page.
Auto-opens browser on startup — just run it and go.

Usage:
  python scripts/ollama_service.py                     # default port 5680
  python scripts/ollama_service.py --port 5680         # custom port
  python scripts/ollama_service.py --no-browser        # don't open browser
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import os
import re
import sys
import time
import traceback
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from urllib.error import URLError

from PIL import Image

# ── Config ──────────────────────────────────────────────────────────────
DEFAULT_PORT = 5680
DEFAULT_OLLAMA_URL = "http://localhost:11434"
MODEL = "qwen3.6:27b"
MAX_IMAGE_SIZE = 1024  # longest edge in px — resize to speed up

# Project root = ppttool/
PROJECT_ROOT = Path(__file__).resolve().parent.parent


# ── Prompt ──────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are an image analysis assistant for a PPT conversion tool. Identify all meaningful visual regions in the image.

Classify each region as one of:
- text_area: paragraphs, headings, labels, numbers (becomes editable text box)
- shape_bg: colored backgrounds, cards, banners (becomes PPT shape)
- icon: logos, icons, small graphics (becomes standalone PNG)
- keep_image: photos, complex charts, illustrations (kept as full image)
- formula: mathematical equations (becomes image)
- decorative: purely decorative lines, dividers, ornaments (may be simplified)"""

USER_PROMPT_TEMPLATE = """Analyze this image (width: {width}px, height: {height}px) and identify all meaningful visual regions.

Return ONLY a valid JSON array. No markdown, no code fences, no explanation.

Each item schema:
{{
  "type": "text_area" | "shape_bg" | "icon" | "keep_image" | "formula" | "decorative",
  "bbox": [x, y, width, height],
  "text": "recognized text content (for text_area, empty string for others)",
  "font": "Font name estimate (e.g. Arial, Microsoft YaHei)",
  "font_size": 18,
  "color": "#RRGGBB",
  "fill": "#RRGGBB",
  "label": "brief description in Chinese"
}}

Rules:
- bbox coordinates in pixels, top-left origin, relative to {width}x{height}
- For text_area: OCR the text accurately, preserve original language
- For shape_bg: provide fill color, omit text
- For icon/keep_image/formula: text should be empty string
- Colors must be 6-digit hex with # prefix
- font_size in points (pt)
- Do NOT merge separate text blocks into one region
- Do NOT split a single logical block"""


# ── MIME types ──────────────────────────────────────────────────────────

MIME_MAP = {
    ".html": "text/html; charset=utf-8",
    ".js":   "application/javascript; charset=utf-8",
    ".css":  "text/css; charset=utf-8",
    ".png":  "image/png",
    ".jpg":  "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif":  "image/gif",
    ".svg":  "image/svg+xml",
    ".ico":  "image/x-icon",
    ".json": "application/json",
    ".py":   "text/plain; charset=utf-8",
    ".md":   "text/markdown; charset=utf-8",
}


# ── Helpers ─────────────────────────────────────────────────────────────

def resize_image(img: Image.Image, max_size: int) -> Image.Image:
    w, h = img.size
    if max(w, h) <= max_size:
        return img
    scale = max_size / max(w, h)
    return img.resize((round(w * scale), round(h * scale)), Image.LANCZOS)


def data_url_to_image(data_url: str) -> tuple[Image.Image, str]:
    m = re.match(r"data:image/(\w+);base64,(.+)", data_url)
    if not m:
        raise ValueError("Invalid data URL format")
    mime_type = m.group(1)
    img_bytes = base64.b64decode(m.group(2))
    return Image.open(io.BytesIO(img_bytes)), mime_type


def image_to_base64(img: Image.Image, fmt: str = "PNG") -> str:
    buf = io.BytesIO()
    img.save(buf, format=fmt)
    return base64.b64encode(buf.getvalue()).decode("utf-8")


def call_ollama(
    ollama_url: str, model: str, image_base64: str,
    system: str, user_prompt: str, timeout: int = 120,
) -> dict[str, Any]:
    payload = {
        "model": model, "stream": False,
        "options": {"temperature": 0.1},
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user_prompt, "images": [image_base64]},
        ],
    }
    req = Request(
        f"{ollama_url}/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    resp = urlopen(req, timeout=timeout)
    return json.loads(resp.read().decode("utf-8"))


def parse_regions_from_response(response: dict) -> list[dict]:
    content = response.get("message", {}).get("content", "")
    json_match = re.search(r"```(?:json)?\s*(\[[\s\S]*?\])\s*```", content)
    if json_match:
        content = json_match.group(1)
    array_match = re.search(r"(\[[\s\S]*\])", content)
    if not array_match:
        raise ValueError("No JSON array found in response")
    content = array_match.group(1)
    content = re.sub(r",\s*}", "}", content)
    content = re.sub(r",\s*\]", "]", content)
    regions = json.loads(content)
    if not isinstance(regions, list):
        raise ValueError("Response is not a JSON array")
    return regions


def normalize_regions(
    regions: list[dict], orig_w: int, orig_h: int,
    resized_w: int, resized_h: int,
) -> list[dict]:
    scale_x = orig_w / resized_w
    scale_y = orig_h / resized_h
    valid_types = {"text_area", "shape_bg", "icon", "keep_image", "formula", "decorative"}
    normalized = []

    for i, r in enumerate(regions):
        if not isinstance(r, dict):
            continue
        rtype = r.get("type", "text_area")
        if rtype not in valid_types:
            rtype = "text_area"
        bbox_raw = r.get("bbox", [0, 0, 100, 100])
        if not isinstance(bbox_raw, (list, tuple)) or len(bbox_raw) != 4:
            continue
        x, y, w, h = bbox_raw
        x = max(0, round(x * scale_x))
        y = max(0, round(y * scale_y))
        w = max(10, round(w * scale_x))
        h = max(5, round(h * scale_y))
        x = min(x, orig_w - 10)
        y = min(y, orig_h - 10)
        w = min(w, orig_w - x)
        h = min(h, orig_h - y)

        text = r.get("text", "")
        if not isinstance(text, str):
            text = str(text) if text else ""

        ai_proc = {"type": rtype}
        if rtype == "text_area":
            ai_proc.update({
                "text": text,
                "font": r.get("font", "Arial") or "Arial",
                "font_size": max(8, min(72, int(r.get("font_size", 18) or 18))),
                "color": normalize_color(r.get("color", "")),
                "align": "left",
            })
        elif rtype == "shape_bg":
            ai_proc.update({
                "fill": normalize_color(r.get("fill", ""), "#E0E0E0"),
                "shape": "rect",
            })
        elif rtype in ("icon", "keep_image"):
            ai_proc.update({"type": "image", "note": f"AI-detected {rtype}"})
        elif rtype == "formula":
            ai_proc.update({"type": "image", "text": text, "note": "AI-detected formula"})
        elif rtype == "decorative":
            ai_proc.update({"type": "decorative", "note": "AI-detected decorative element"})

        normalized.append({
            "id": f"ai-r{i + 1:03d}",
            "user_type": rtype,
            "label": r.get("label", "") or rtype,
            "annotation": {
                "tool": "ai_auto",
                "bbox": [x, y, w, h],
                "mask_polygon": None,
                "use_mask": False,
            },
            "ai_processed": ai_proc,
        })
    return normalized


def normalize_color(value: str, default: str = "#000000") -> str:
    if not value or not isinstance(value, str):
        return default
    value = value.strip().lstrip("#")
    if re.fullmatch(r"[0-9a-fA-F]{6}", value):
        return f"#{value.upper()}"
    if re.fullmatch(r"[0-9a-fA-F]{3}", value):
        return f"#{value[0]*2}{value[1]*2}{value[2]*2}".upper()
    return default


# ── MIME ────────────────────────────────────────────────────────────────

def guess_mime(path: str) -> str:
    _, ext = os.path.splitext(path)
    return MIME_MAP.get(ext.lower(), "application/octet-stream")


# ── HTTP Handler ────────────────────────────────────────────────────────

class OllamaHandler(BaseHTTPRequestHandler):
    """Serves API endpoints + static files."""

    def log_message(self, fmt, *args):
        pass  # silence per-request logs

    # ── helpers ──────────────────────────────────────────────────────

    def _send_json(self, status: int, data: dict):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (ConnectionError, BrokenPipeError, OSError):
            pass

    def _serve_static(self, rel_path: str):
        """Serve a static file from the project directory."""
        # Security: resolve path and ensure it's within PROJECT_ROOT
        full = (PROJECT_ROOT / rel_path).resolve()
        try:
            full.relative_to(PROJECT_ROOT.resolve())
        except ValueError:
            self._send_json(403, {"error": "Forbidden"})
            return

        if not full.is_file():
            self._send_json(404, {"error": "Not found"})
            return

        try:
            data = full.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", guess_mime(str(full)))
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(data)
        except Exception as e:
            self._send_json(500, {"error": str(e)})

    # ── HTTP methods ─────────────────────────────────────────────────

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Max-Age", "86400")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"

        # API endpoints
        if path == "/health":
            return self._handle_health()

        # Static files — route `/` → annotator.html
        if path == "/" or path == "/annotator.html":
            return self._serve_static("tools/annotator.html")

        # favicon.ico — silently ignore
        if path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return

        # Strip leading slash → relative path
        rel = path.lstrip("/")
        if rel:
            return self._serve_static(rel)

        self._send_json(404, {"error": "Not found"})

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/analyze":
            return self._handle_analyze()
        self._send_json(404, {"error": "Not found"})

    # ── handlers ─────────────────────────────────────────────────────

    def _handle_health(self):
        try:
            req = Request(f"{self.server.ollama_url}/api/tags", method="GET")
            resp = urlopen(req, timeout=5)
            tags = json.loads(resp.read().decode("utf-8"))
            models = [t["name"] for t in tags.get("models", [])]
            model_available = any(MODEL in m for m in models)
            self._send_json(200, {
                "status": "ok",
                "model": MODEL,
                "model_available": model_available,
                "available_models": models[:10],
                "ollama_url": self.server.ollama_url,
            })
        except Exception as e:
            self._send_json(200, {
                "status": "error",
                "model": MODEL,
                "error": str(e),
            })

    def _handle_analyze(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            if length == 0:
                self._send_json(400, {"error": "Empty request body"})
                return
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            data_url = body.get("image", "")
            timeout = int(body.get("timeout", 120))
            if not data_url:
                self._send_json(400, {"error": "Missing 'image' field"})
                return

            print(f"  [analyze] Received image request...")
            img, _mime = data_url_to_image(data_url)
            orig_w, orig_h = img.size
            img_resized = resize_image(img, MAX_IMAGE_SIZE)
            resized_w, resized_h = img_resized.size
            print(f"  [analyze] Image: {orig_w}x{orig_h} -> {resized_w}x{resized_h}")

            b64 = image_to_base64(img_resized)
            user_prompt = USER_PROMPT_TEMPLATE.format(width=resized_w, height=resized_h)

            print(f"  [analyze] Calling Ollama model={MODEL}...")
            t0 = time.time()
            response = call_ollama(self.server.ollama_url, MODEL, b64, SYSTEM_PROMPT, user_prompt, timeout=timeout)
            elapsed = time.time() - t0
            print(f"  [analyze] Ollama responded in {elapsed:.1f}s")

            raw_regions = parse_regions_from_response(response)
            print(f"  [analyze] Found {len(raw_regions)} raw regions")
            regions = normalize_regions(raw_regions, orig_w, orig_h, resized_w, resized_h)
            print(f"  [analyze] Returning {len(regions)} normalized regions")

            self._send_json(200, {
                "status": "ok",
                "regions": regions,
                "elapsed": round(elapsed, 1),
                "image_size": {"w": orig_w, "h": orig_h},
            })
        except json.JSONDecodeError as e:
            print(f"  [analyze] JSON error: {e}", file=sys.stderr)
            self._send_json(400, {"error": f"Invalid JSON: {e}"})
        except Exception as e:
            print(f"  [analyze] Error: {e}", file=sys.stderr)
            traceback.print_exc()
            self._send_json(500, {"error": str(e)})


class OllamaServer(HTTPServer):
    ollama_url: str = DEFAULT_OLLAMA_URL


# ── Main ────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Ollama bridge + annotator server")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"Port (default: {DEFAULT_PORT})")
    parser.add_argument("--ollama-url", default=DEFAULT_OLLAMA_URL, help=f"Ollama URL (default: {DEFAULT_OLLAMA_URL})")
    parser.add_argument("--no-browser", action="store_true", help="Don't auto-open browser")
    args = parser.parse_args()

    server = OllamaServer(("0.0.0.0", args.port), OllamaHandler)
    server.ollama_url = args.ollama_url.rstrip("/")

    url = f"http://localhost:{args.port}"

    print("  ╔══════════════════════════════════════════════╗")
    print("  ║    AI 分析服务已启动                         ║")
    print("  ╠══════════════════════════════════════════════╣")
    print(f"  ║  网页:  {url:<39}║")
    print(f"  ║  Ollama: {server.ollama_url:<37}║")
    print(f"  ║  模型: {MODEL:<42}║")
    print("  ╚══════════════════════════════════════════════╝")
    print()
    print("  操作说明:")
    print("  ───────────────────────────────────────────────")
    print("  1. 打开浏览器访问上方地址")
    print("  2. 拖入图片 → 点击 🤖 AI 分析")
    print("  3. 按 Ctrl+C 停止服务")
    print()

    if not args.no_browser:
        try:
            webbrowser.open(url)
            print("  → 已自动打开浏览器...")
        except Exception:
            print(f"  → 请手动打开浏览器访问: {url}")
        print()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  服务已停止")
        server.server_close()


if __name__ == "__main__":
    main()
