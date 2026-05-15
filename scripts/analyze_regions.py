#!/usr/bin/env python3
"""AI secondary recognition for annotated image regions.

Fix stdout encoding for Windows.
Usage:
  python scripts/analyze_regions.py manifest.json --source source.png --output enriched.json
"""
from __future__ import annotations

import sys
if sys.stdout.encoding and sys.stdout.encoding.upper() == 'GBK':
    sys.stdout.reconfigure(encoding='utf-8')

import argparse
import json
import os
import sys
import tempfile
import traceback
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw


def crop_region_from_image(
    source: Path | np.ndarray,
    bbox: list[float],
    mask_polygon: list[list[float]] | None = None,
    use_mask: bool = False,
) -> Image.Image:
    """Crop a region from the source image, optionally applying a mask for transparency."""
    if isinstance(source, (str, Path)):
        img = Image.open(str(source)).convert("RGBA")
    else:
        img = Image.fromarray(source).convert("RGBA")

    x, y, w, h = [int(round(v)) for v in bbox]
    x = max(0, x)
    y = max(0, y)
    w = min(w, img.width - x)
    h = min(h, img.height - y)

    cropped = img.crop((x, y, x + w, y + h))

    if use_mask and mask_polygon and len(mask_polygon) >= 3:
        # Create alpha mask from polygon (in cropped coordinates)
        mask = Image.new("L", cropped.size, 0)
        draw = ImageDraw.Draw(mask)
        # Adjust polygon to cropped coordinates
        adj_poly = [(px - x, py - y) for px, py in mask_polygon]
        draw.polygon(adj_poly, fill=255)
        # Apply antialiasing via Gaussian blur on the mask edge
        from PIL import ImageFilter
        mask = mask.filter(ImageFilter.GaussianBlur(radius=1.5))
        cropped.putalpha(mask)
        # Trim transparent edges to tight shape bounds
        trim_box = cropped.getbbox()
        if trim_box:
            cropped = cropped.crop(trim_box)

    return cropped


def analyze_text_region(source: Path, element: dict) -> dict:
    """Analyze a text_area region: OCR + estimate font properties.

    Uses PaddleOCR for text recognition.
    """
    bbox = element["annotation"]["bbox"]
    mask_poly = element["annotation"].get("mask_polygon")
    use_mask = element["annotation"].get("use_mask", False)
    crop = crop_region_from_image(source, bbox, mask_poly, use_mask)

    result = {
        "type": "text",
        "text": "",
        "font": "Arial",
        "font_size": 18,
        "bold": False,
        "color": "#000000",
        "align": "left",
    }

    # OCR
    try:
        import easyocr
        reader = easyocr.Reader(['ch_sim', 'en'], gpu=False, verbose=False)
        # Convert crop to numpy array for easyocr
        crop_np = np.array(crop.convert("RGB"))

        ocr_result = reader.readtext(crop_np)
        texts = []
        for detection in ocr_result:
            texts.append(detection[1])

        if texts:
            result["text"] = "\n".join(texts)
    except Exception as e:
        print(f"  OCR error: {e}", file=sys.stderr)

    # Estimate colors from the region
    try:
        rgb = crop.convert("RGB")
        pixels = np.array(rgb)
        # Sample center area for text color (assume dark pixels = text)
        if pixels.size > 0:
            # Get the dominant color (background)
            flat = pixels.reshape(-1, 3)
            median = np.median(flat, axis=0).astype(int)
            bg_color = f"#{median[0]:02X}{median[1]:02X}{median[2]:02X}"

            # If we have OCR text, estimate font size from region height
            h = bbox[3]
            result["font_size"] = max(8, min(72, int(h * 0.7)))
            result["color"] = get_text_color(pixels)
    except Exception as e:
        print(f"  Color analysis error: {e}", file=sys.stderr)

    return result


def get_text_color(pixels: np.ndarray) -> str:
    """Simple heuristic: sample edges for background, center for text."""
    h, w = pixels.shape[:2]
    if h < 3 or w < 3:
        return "#000000"

    # Sample border pixels as background candidates
    border = np.concatenate([
        pixels[0, :],
        pixels[-1, :],
        pixels[:, 0],
        pixels[:, -1],
    ])
    bg_median = np.median(border.reshape(-1, 3), axis=0)

    # Sample center region
    cy, cx = h // 2, w // 2
    size = max(3, min(h, w) // 3)
    center = pixels[cy - size // 2:cy + size // 2, cx - size // 2:cx + size // 2]
    center_flat = center.reshape(-1, 3)
    center_median = np.median(center_flat, axis=0)

    # Compare luminance
    def lum(c):
        return 0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]

    bg_lum = lum(bg_median)
    text_lum = lum(center_median)

    # If center is significantly different from border, it's the text color
    if abs(text_lum - bg_lum) > 40:
        c = center_median.astype(int)
        return f"#{c[0]:02X}{c[1]:02X}{c[2]:02X}"

    # Otherwise assume black text
    return "#000000"


def analyze_shape_region(source: Path, element: dict) -> dict:
    """Analyze a shape_bg region: detect fill color, stroke, dimensions."""
    bbox = element["annotation"]["bbox"]
    crop = crop_region_from_image(source, bbox)
    rgb = crop.convert("RGB")
    pixels = np.array(rgb)

    result = {
        "type": "shape",
        "fill": "#FFFFFF",
        "stroke": None,
        "shape": "rect",
    }

    if pixels.size > 0:
        flat = pixels.reshape(-1, 3)

        # Dominant fill color (median of all pixels)
        median = np.median(flat, axis=0).astype(int)
        result["fill"] = f"#{median[0]:02X}{median[1]:02X}{median[2]:02X}"

        # Check if it's likely a rounded rect (check corner pixels)
        h, w = pixels.shape[:2]
        if h > 4 and w > 4:
            corners = [
                pixels[1, 1], pixels[1, w - 2],
                pixels[h - 2, 1], pixels[h - 2, w - 2],
            ]
            corner_lums = [0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2] for c in corners]
            bg_lum = 0.299 * median[0] + 0.587 * median[1] + 0.114 * median[2]
            # If corners are significantly different, might be rounded (background showing)
            if any(abs(pl - bg_lum) > 30 for pl in corner_lums):
                result["shape"] = "roundRect"

    return result


def analyze_icon_region(source: Path, element: dict, crop_dir: Path) -> dict:
    """Crop an icon region from the source."""
    bbox = element["annotation"]["bbox"]
    mask_poly = element["annotation"].get("mask_polygon")
    use_mask = element["annotation"].get("use_mask", False)

    crop = crop_region_from_image(source, bbox, mask_poly, use_mask)
    crop_dir.mkdir(parents=True, exist_ok=True)
    crop_path = crop_dir / f"{element['id']}.png"
    crop.save(crop_path)

    return {
        "type": "image",
        "source": str(crop_path.relative_to(crop_dir.parent.parent) if crop_path.parent.parent else crop_path),
        "width": crop.width,
        "height": crop.height,
    }


def analyze_keep_image_region(source: Path, element: dict, crop_dir: Path) -> dict:
    """Crop a keep_image region as-is from the source."""
    bbox = element["annotation"]["bbox"]
    crop = crop_region_from_image(source, bbox)
    crop_dir.mkdir(parents=True, exist_ok=True)
    crop_path = crop_dir / f"{element['id']}_full.png"
    crop.save(crop_path)

    return {
        "type": "image",
        "source": str(crop_path.relative_to(crop_dir.parent.parent) if crop_path.parent.parent else crop_path),
        "width": crop.width,
        "height": crop.height,
    }


def analyze_convert_svg(element: dict) -> dict:
    """Mark a region for SVG conversion."""
    return {
        "type": "svg",
        "note": "User requested SVG conversion for this region",
    }


def analyze_decorative(element: dict) -> dict:
    """Mark a decorative element."""
    return {
        "type": "decorative",
        "note": "Decorative element - may be simplified or kept as image",
    }


def analyze_formula_region(source: Path, element: dict, crop_dir: Path) -> dict:
    """Analyze a formula region: crop as image (same as icon)."""
    bbox = element["annotation"]["bbox"]
    mask_poly = element["annotation"].get("mask_polygon")
    use_mask = element["annotation"].get("use_mask", False)
    crop = crop_region_from_image(source, bbox, mask_poly, use_mask)
    crop_dir.mkdir(parents=True, exist_ok=True)
    crop_path = crop_dir / f"{element['id']}.png"
    crop.save(crop_path)
    return {
        "type": "image",
        "source": str(crop_path.relative_to(crop_dir.parent) if crop_path.is_relative_to(crop_dir.parent) else crop_path),
        "width": crop.width,
        "height": crop.height,
    }


def auto_detect_text_regions(source_path: Path) -> list[dict]:
    """Run EasyOCR on full image to auto-discover text regions.
    Returns a list of element dicts (user_type=text_area) for each detected text block.
    """
    try:
        import easyocr
        _ocr_reader = easyocr.Reader(['ch_sim', 'en'], gpu=False, verbose=False)
    except Exception as e:
        print(f"  [WARN] EasyOCR not available: {e}")
        print(f"  [WARN] Install with: pip install easyocr")
        return []
    import numpy as np
    from PIL import Image
    img_np = np.array(Image.open(source_path).convert("RGB"))
    results = _ocr_reader.readtext(img_np)
    elements = []
    for idx, (poly, text, conf) in enumerate(results):
        if conf < 0.3 or not text.strip():
            continue
        # Convert polygon [[x1,y1],[x2,y2],[x3,y3],[x4,y4]] to bbox [x, y, w, h]
        xs = [p[0] for p in poly]
        ys = [p[1] for p in poly]
        x, y = min(xs), min(ys)
        w, h = max(xs) - x, max(ys) - y
        if w < 10 or h < 5:
            continue
        elements.append({
            "id": f"auto-text-{idx + 1:03d}",
            "user_type": "text_area",
            "label": text.strip()[:40],
            "annotation": {
                "tool": "auto_ocr",
                "bbox": [round(x), round(y), round(w), round(h)],
                "mask_polygon": None,
                "use_mask": False,
            },
        })
    print(f"  Auto-detected {len(elements)} text regions")
    return elements


def main():
    parser = argparse.ArgumentParser(description="AI secondary recognition for annotated image regions")
    parser.add_argument("manifests", type=Path, nargs="+", help="Path(s) to annotation manifest JSON (supports glob)")
    parser.add_argument("--source", type=Path, nargs="+", required=True, help="Source image file path(s), one per manifest")
    parser.add_argument("--crop-dir", type=Path, help="Directory for cropped assets (default: beside each manifest)")
    parser.add_argument("--auto-detect-text", action="store_true", help="Auto-detect text regions from full image using EasyOCR")
    args = parser.parse_args()

    # Expand glob patterns in manifests
    manifest_paths: list[Path] = []
    for p in args.manifests:
        expanded = list(Path().glob(str(p))) if '*' in str(p) else [p]
        manifest_paths.extend(expanded)
    manifest_paths = sorted(set(p.resolve() for p in manifest_paths))

    if not manifest_paths:
        parser.error("No manifest files found matching the given path(s).")

    source_paths = [p.resolve() for p in args.source]

    # Broadcast single source to all manifests, or require 1:1 match
    if len(source_paths) == 1 and len(manifest_paths) > 1:
        source_paths = source_paths * len(manifest_paths)
    elif len(source_paths) != len(manifest_paths):
        parser.error(
            f"Number of --source entries ({len(source_paths)}) must match "
            f"number of manifests ({len(manifest_paths)}), or be exactly 1."
        )

    for mi, (manifest_path, source_path) in enumerate(zip(manifest_paths, source_paths), start=1):
        print(f"\n=== [{mi}/{len(manifest_paths)}] Processing {manifest_path.name} ===")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        source_base = source_path.parent
        crop_dir = args.crop_dir or (manifest_path.parent / f"{source_path.stem}_crops")

        # Derive output path from manifest name
        output_path = manifest_path.parent / f"{manifest_path.stem}_enriched.json"

        # Add source image info to manifest
        manifest["source_image"] = str(source_path)
        with Image.open(source_path) as img:
            manifest["source_image_w"], manifest["source_image_h"] = img.size

        elements = manifest.get("elements", [])
        print(f"  {len(elements)} user-annotated regions")

        # Auto-detect text regions from full image
        if args.auto_detect_text:
            new_texts = auto_detect_text_regions(source_path)
            elements.extend(new_texts)
            manifest["elements"] = elements
            print(f"  Total regions after auto-detect: {len(elements)}")

        for i, el in enumerate(elements):
            uid = el.get("id", f"region-{i}")
            utype = el.get("user_type", "text_area")
            print(f"  [{i+1}/{len(elements)}] {uid}: {utype} ... ", end="", flush=True)

            try:
                if utype == "text_area":
                    result = analyze_text_region(source_path, el)
                elif utype == "shape_bg":
                    result = analyze_shape_region(source_path, el)
                elif utype == "icon":
                    result = analyze_icon_region(source_path, el, crop_dir)
                elif utype == "keep_image":
                    result = analyze_keep_image_region(source_path, el, crop_dir)
                elif utype == "convert_svg":
                    result = analyze_convert_svg(el)
                elif utype == "decorative":
                    result = analyze_decorative(el)
                elif utype == "formula":
                    result = analyze_formula_region(source_path, el, crop_dir)
                else:
                    result = {"type": "unknown", "note": f"Unhandled type: {utype}"}

                el["ai_processed"] = result
                print(f"[OK] {result.get('type', '?')}")
                if utype == "text_area" and result.get("text"):
                    print(f"       OCR: {result.get('text', '')[:60]}...")
            except Exception as e:
                el["ai_processed"] = {"type": "error", "error": str(e)}
                print(f"[ERR] {e}")
                traceback.print_exc()

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  [OK] Saved: {output_path}")
        print(f"  Cropped assets in: {crop_dir}")


if __name__ == "__main__":
    main()
