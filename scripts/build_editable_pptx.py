#!/usr/bin/env python3
"""Build a one-slide or multi-slide editable PPTX from image reconstruction manifest(s).

The script intentionally uses only stdlib plus Pillow. It writes a small OpenXML
PPTX with native text boxes, shapes, and cropped picture objects.

Usage (single, backward-compatible):
    build_editable_pptx.py enriched.json

Usage (multi-slide):
    build_editable_pptx.py enriched-001.json enriched-002.json --output deck.pptx
    build_editable_pptx.py "enriched-*.json" --output deck.pptx
"""

from __future__ import annotations

import argparse
import glob
import json
import math
import posixpath
import re
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from PIL import Image

EMU_PER_INCH = 914400
PT_TO_EMU = 12700

NS_CT = "http://schemas.openxmlformats.org/package/2006/content-types"
NS_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
NS_OFFICE_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS_P = "http://schemas.openxmlformats.org/presentationml/2006/main"
NS_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
NS_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS_DC = "http://purl.org/dc/elements/1.1/"
NS_CP = "http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
NS_DCTERMS = "http://purl.org/dc/terms/"
NS_DCMITYPE = "http://purl.org/dc/dcmitype/"
NS_XSI = "http://www.w3.org/2001/XMLSchema-instance"


@dataclass
class MediaItem:
    rid: str
    source_path: Path
    target_name: str


@dataclass
class SlideData:
    """Processed slide data ready for XML serialization."""
    slide_w: int
    slide_h: int
    elements_xml: list[str]
    media_items: list[MediaItem]
    bg: str | None
    bg_image_rid: str | None  # source image as slide background


def xml(text: Any) -> str:
    return escape(str(text), {'"': "&quot;"})


def slug(value: str, fallback: str = "asset") -> str:
    value = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip()).strip("-._")
    return value or fallback


def resolve_path(base: Path, value: str | None) -> Path | None:
    if not value:
        return None
    p = Path(value)
    return p if p.is_absolute() else (base / p).resolve()


def normalize_color(value: Any, default: str | None = None) -> str | None:
    if value is None:
        return default
    if isinstance(value, str):
        cleaned = value.strip()
        if cleaned.lower() in {"none", "transparent", "no", "null"}:
            return None
        if cleaned.startswith("#"):
            cleaned = cleaned[1:]
        if re.fullmatch(r"[0-9a-fA-F]{3}", cleaned):
            cleaned = "".join(ch * 2 for ch in cleaned)
        if re.fullmatch(r"[0-9a-fA-F]{6}", cleaned):
            return cleaned.upper()
    if isinstance(value, (list, tuple)) and len(value) >= 3:
        return "".join(f"{max(0, min(255, int(c))):02X}" for c in value[:3])
    raise ValueError(f"Invalid color value: {value!r}")


def solid_fill(color: str | None, opacity: float | None = None) -> str:
    if color is None:
        return "<a:noFill/>"
    alpha = ""
    if opacity is not None and opacity < 1:
        alpha_val = max(0, min(100000, int(round(opacity * 100000))))
        alpha = f"<a:alpha val=\"{alpha_val}\"/>"
    return f"<a:solidFill><a:srgbClr val=\"{color}\">{alpha}</a:srgbClr></a:solidFill>"


def line_xml(color: str | None, width_pt: float | None = None, opacity: float | None = None) -> str:
    if color is None:
        return "<a:ln><a:noFill/></a:ln>"
    width = int(round((width_pt if width_pt is not None else 1) * PT_TO_EMU))
    return f"<a:ln w=\"{width}\">{solid_fill(color, opacity)}</a:ln>"


# Font fallback table: map common font names to nearest available alternatives
FONT_FALLBACK = {
    "宋体": "SimSun", "SimSun": "SimSun",
    "黑体": "SimHei", "SimHei": "SimHei",
    "微软雅黑": "Microsoft YaHei", "Microsoft YaHei": "Microsoft YaHei",
    "楷体": "KaiTi", "KaiTi": "KaiTi",
    "Arial": "Arial",
    "Helvetica": "Arial",
    "sans-serif": "Arial",
    "serif": "Times New Roman",
    "Times New Roman": "Times New Roman",
    "Courier New": "Courier New",
    "Consolas": "Consolas",
    "Calibri": "Calibri",
    "Segoe UI": "Segoe UI",
    "Tahoma": "Tahoma",
    "Verdana": "Verdana",
    "Georgia": "Georgia",
}


def resolve_font(font_name: str | None) -> str:
    """Resolve a font name to a known fallback; returns a PPT-safe font name."""
    if not font_name or font_name.strip() == "":
        return "Arial"
    cleaned = font_name.strip()
    if cleaned in FONT_FALLBACK:
        return FONT_FALLBACK[cleaned]
    lower = cleaned.lower()
    for key, val in FONT_FALLBACK.items():
        if key.lower() == lower:
            return val
    return cleaned


def get_bbox(el: dict[str, Any]) -> tuple[float, float, float, float]:
    """Get bounding box from an element, supporting both old and new manifest formats."""
    annotation = el.get("annotation")
    if isinstance(annotation, dict) and "bbox" in annotation:
        x, y, w, h = annotation["bbox"]
        return float(x), float(y), float(w), float(h)
    if "bbox" in el:
        x, y, w, h = el["bbox"]
        return float(x), float(y), float(w), float(h)
    if "bbox_px" in el:
        x, y, w, h = el["bbox_px"]
        return float(x), float(y), float(w), float(h)
    if "bbox_xyxy" in el:
        x1, y1, x2, y2 = el["bbox_xyxy"]
        return float(x1), float(y1), float(x2) - float(x1), float(y2) - float(y1)
    raise ValueError(f"Element is missing bbox: {el}")


def get_mask_polygon(el: dict[str, Any]) -> list[list[float]] | None:
    annotation = el.get("annotation")
    if isinstance(annotation, dict):
        return annotation.get("mask_polygon")
    return None


def get_use_mask(el: dict[str, Any]) -> bool:
    annotation = el.get("annotation")
    if isinstance(annotation, dict):
        return bool(annotation.get("use_mask", False))
    return False


def get_ai_attr(el: dict[str, Any], key: str, default: Any = None) -> Any:
    ai = el.get("ai_processed") or {}
    if key in ai:
        return ai[key]
    if key in el:
        return el[key]
    return default


def emu_bbox(
    bbox: tuple[float, float, float, float],
    scale_x: float,
    scale_y: float,
) -> tuple[int, int, int, int]:
    x, y, w, h = bbox
    return (
        int(round(x * scale_x)),
        int(round(y * scale_y)),
        int(round(w * scale_x)),
        int(round(h * scale_y)),
    )


def shape_preset(el: dict[str, Any]) -> str:
    raw = str(el.get("shape") or el.get("type") or "rect")
    mapping = {
        "rectangle": "rect", "rect": "rect", "box": "rect",
        "bar": "rect", "footer": "rect", "sidebar": "rect", "divider": "rect",
        "rounded_box": "roundRect", "rounded-rect": "roundRect",
        "roundrect": "roundRect", "roundRect": "roundRect",
        "rounded_rectangle": "roundRect",
        "ellipse": "ellipse", "oval": "ellipse", "circle": "ellipse",
        "line": "line",
    }
    return mapping.get(raw, raw)


def xfrm_xml(x: int, y: int, w: int, h: int) -> str:
    return f"<a:xfrm><a:off x=\"{x}\" y=\"{y}\"/><a:ext cx=\"{w}\" cy=\"{h}\"/></a:xfrm>"


def nonvisual_shape(shape_id: int, name: str, tx_box: bool = False) -> str:
    tx = " txBox=\"1\"" if tx_box else ""
    return (
        "<p:nvSpPr>"
        f"<p:cNvPr id=\"{shape_id}\" name=\"{xml(name)}\"/>"
        f"<p:cNvSpPr{tx}/>"
        "<p:nvPr/>"
        "</p:nvSpPr>"
    )


def shape_xml(shape_id: int, el: dict[str, Any], scale_x: float, scale_y: float) -> str:
    x, y, w, h = emu_bbox(get_bbox(el), scale_x, scale_y)
    name = str(el.get("name") or f"Shape {shape_id}")
    preset = shape_preset(el)
    fill = normalize_color(el.get("fill"), "#FFFFFF")
    stroke = normalize_color(el.get("stroke"), None)
    opacity = el.get("opacity")
    stroke_opacity = el.get("stroke_opacity")
    stroke_width = el.get("stroke_width")
    return (
        "<p:sp>"
        f"{nonvisual_shape(shape_id, name)}"
        "<p:spPr>"
        f"{xfrm_xml(x, y, max(w, 1), max(h, 1))}"
        f"<a:prstGeom prst=\"{xml(preset)}\"><a:avLst/></a:prstGeom>"
        f"{solid_fill(fill, float(opacity) if opacity is not None else None)}"
        f"{line_xml(stroke, float(stroke_width) if stroke_width is not None else None, float(stroke_opacity) if stroke_opacity is not None else None)}"
        "</p:spPr>"
        "</p:sp>"
    )


def text_body_xml(el: dict[str, Any]) -> str:
    text = str(get_ai_attr(el, "text", el.get("text", "")))
    font = resolve_font(str(get_ai_attr(el, "font", el.get("font") or el.get("font_family") or "Arial")))
    font_size = float(get_ai_attr(el, "font_size", el.get("font_size") or el.get("size") or 18))
    color = normalize_color(get_ai_attr(el, "color", el.get("color")), "#000000")
    bold = bool(get_ai_attr(el, "bold", el.get("bold", False)))
    italic = bool(get_ai_attr(el, "italic", el.get("italic", False)))
    underline = bool(get_ai_attr(el, "underline", el.get("underline", False)))
    align = str(get_ai_attr(el, "align", el.get("align") or "left")).lower()
    valign = str(get_ai_attr(el, "valign", el.get("valign") or el.get("vertical_align") or "top")).lower()
    margin = el.get("margin", 2)
    if isinstance(margin, (int, float)):
        left = right = top = bottom = float(margin)
    else:
        left, top, right, bottom = [float(v) for v in margin]

    align_map = {"left": "l", "center": "ctr", "centre": "ctr", "right": "r", "justify": "just"}
    valign_map = {"top": "t", "middle": "mid", "center": "mid", "mid": "mid", "bottom": "b"}
    p_align = align_map.get(align, "l")
    anchor = valign_map.get(valign, "t")
    r_attrs = [f"lang=\"en-US\"", f"sz=\"{int(round(font_size * 100))}\""]
    if bold:
        r_attrs.append('b="1"')
    if italic:
        r_attrs.append('i="1"')
    if underline:
        r_attrs.append('u="sng"')
    rpr = (
        f"<a:rPr {' '.join(r_attrs)}>"
        f"{solid_fill(color)}"
        f"<a:latin typeface=\"{xml(font)}\"/>"
        f"<a:ea typeface=\"{xml(font)}\"/>"
        f"<a:cs typeface=\"{xml(font)}\"/>"
        "</a:rPr>"
    )

    paras = []
    for para in text.splitlines() or [""]:
        paras.append(
            f"<a:p><a:pPr algn=\"{p_align}\"/>"
            f"<a:r>{rpr}<a:t>{xml(para)}</a:t></a:r>"
            "</a:p>"
        )
    body_pr = (
        f"<a:bodyPr wrap=\"square\" anchor=\"{anchor}\" "
        f"lIns=\"{int(round(left * PT_TO_EMU))}\" "
        f"tIns=\"{int(round(top * PT_TO_EMU))}\" "
        f"rIns=\"{int(round(right * PT_TO_EMU))}\" "
        f"bIns=\"{int(round(bottom * PT_TO_EMU))}\"/>"
    )
    return f"<p:txBody>{body_pr}<a:lstStyle/>{''.join(paras)}</p:txBody>"


def text_xml(shape_id: int, el: dict[str, Any], scale_x: float, scale_y: float) -> str:
    x, y, w, h = emu_bbox(get_bbox(el), scale_x, scale_y)
    name = str(el.get("name") or f"Text {shape_id}")
    preset = shape_preset(el) if ("shape" in el or "fill" in el or "stroke" in el) else "rect"
    fill = normalize_color(el.get("fill"), None)
    stroke = normalize_color(el.get("stroke"), None)
    opacity = el.get("opacity")
    stroke_width = el.get("stroke_width")
    return (
        "<p:sp>"
        f"{nonvisual_shape(shape_id, name, tx_box=True)}"
        "<p:spPr>"
        f"{xfrm_xml(x, y, max(w, 1), max(h, 1))}"
        f"<a:prstGeom prst=\"{xml(preset)}\"><a:avLst/></a:prstGeom>"
        f"{solid_fill(fill, float(opacity) if opacity is not None else None)}"
        f"{line_xml(stroke, float(stroke_width) if stroke_width is not None else None)}"
        "</p:spPr>"
        f"{text_body_xml(el)}"
        "</p:sp>"
    )


def crop_or_copy_image(
    manifest_dir: Path,
    source_image: Path,
    crop_dir: Path,
    el: dict[str, Any],
    index: int,
) -> Path:
    ai_source = get_ai_attr(el, "source")
    if ai_source:
        src = resolve_path(manifest_dir, ai_source)
        if src and src.exists():
            out_name = slug(str(el.get("id") or el.get("name") or f"image-{index}")) + ".png"
            out = crop_dir / out_name
            with Image.open(src) as img:
                img.save(out)
            return out

    source = resolve_path(manifest_dir, el.get("source"))
    if source:
        if not source.exists():
            raise FileNotFoundError(source)
        out_name = slug(Path(str(el.get("crop_name") or el.get("name") or f"image-{index}")).stem) + ".png"
        out = crop_dir / out_name
        with Image.open(source) as img:
            img.save(out)
        return out

    crop_dir.mkdir(parents=True, exist_ok=True)
    x, y, w, h = get_bbox(el)
    left = math.floor(x)
    top = math.floor(y)
    right = math.ceil(x + w)
    bottom = math.ceil(y + h)

    mask_polygon = get_mask_polygon(el)
    use_mask = get_use_mask(el)

    out_name = slug(str(el.get("crop_name") or el.get("name") or el.get("id") or f"crop-{index}"))
    if not out_name.lower().endswith(".png"):
        out_name += ".png"
    out = crop_dir / out_name

    with Image.open(source_image) as img:
        img_rgba = img.convert("RGBA")
        box = (
            max(0, left),
            max(0, top),
            min(img_rgba.width, right),
            min(img_rgba.height, bottom),
        )
        cropped = img_rgba.crop(box)

        if use_mask and mask_polygon and len(mask_polygon) >= 3:
            from PIL import ImageDraw
            mask = Image.new("L", cropped.size, 0)
            draw = ImageDraw.Draw(mask)
            adj_poly = [(px - left, py - top) for px, py in mask_polygon]
            draw.polygon(adj_poly, fill=255)
            from PIL import ImageFilter
            mask = mask.filter(ImageFilter.GaussianBlur(radius=1.0))
            cropped.putalpha(mask)
            # Trim transparent edges to tight shape bounds
            trim_box = cropped.getbbox()
            if trim_box:
                cropped = cropped.crop(trim_box)

        cropped.save(out)
    return out


def picture_xml(shape_id: int, el: dict[str, Any], media: MediaItem, scale_x: float, scale_y: float) -> str:
    x, y, w, h = emu_bbox(get_bbox(el), scale_x, scale_y)
    name = str(el.get("name") or f"Picture {shape_id}")
    return (
        "<p:pic>"
        "<p:nvPicPr>"
        f"<p:cNvPr id=\"{shape_id}\" name=\"{xml(name)}\"/>"
        "<p:cNvPicPr><a:picLocks noChangeAspect=\"0\"/></p:cNvPicPr>"
        "<p:nvPr/>"
        "</p:nvPicPr>"
        "<p:blipFill>"
        f"<a:blip r:embed=\"{media.rid}\"/>"
        "<a:stretch><a:fillRect/></a:stretch>"
        "</p:blipFill>"
        "<p:spPr>"
        f"{xfrm_xml(x, y, max(w, 1), max(h, 1))}"
        "<a:prstGeom prst=\"rect\"><a:avLst/></a:prstGeom>"
        "</p:spPr>"
        "</p:pic>"
    )


def group_shape_xml() -> str:
    return (
        "<p:nvGrpSpPr>"
        "<p:cNvPr id=\"1\" name=\"\"/>"
        "<p:cNvGrpSpPr/>"
        "<p:nvPr/>"
        "</p:nvGrpSpPr>"
        "<p:grpSpPr>"
        "<a:xfrm>"
        "<a:off x=\"0\" y=\"0\"/><a:ext cx=\"0\" cy=\"0\"/>"
        "<a:chOff x=\"0\" y=\"0\"/><a:chExt cx=\"0\" cy=\"0\"/>"
        "</a:xfrm>"
        "</p:grpSpPr>"
    )


def blip_fill_bg(rid: str) -> str:
    """Background fill using an image (source image as slide background)."""
    return (
        "<p:bg>"
        "<p:bgPr>"
        f"<a:blipFill><a:blip r:embed=\"{xml(rid)}\"/><a:stretch><a:fillRect/></a:stretch></a:blipFill>"
        "<a:effectLst/>"
        "</p:bgPr>"
        "</p:bg>"
    )


def slide_xml(slide_w: int, slide_h: int, bg: str | None, elements_xml: list[str], bg_image_rid: str | None = None) -> str:
    bg_xml = ""
    if bg_image_rid:
        bg_xml = blip_fill_bg(bg_image_rid)
    elif bg:
        bg_xml = f"<p:bg><p:bgPr>{solid_fill(bg)}</p:bgPr></p:bg>"
    return (
        f"<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>"
        f"<p:sld xmlns:a=\"{NS_A}\" xmlns:r=\"{NS_R}\" xmlns:p=\"{NS_P}\">"
        "<p:cSld>"
        f"{bg_xml}"
        f"<p:spTree>{group_shape_xml()}{''.join(elements_xml)}</p:spTree>"
        "</p:cSld>"
        "<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr>"
        "</p:sld>"
    )


def rels_xml(rels: list[tuple[str, str, str]]) -> str:
    items = [
        f"<Relationship Id=\"{xml(rid)}\" Type=\"{xml(rel_type)}\" Target=\"{xml(target)}\"/>"
        for rid, rel_type, target in rels
    ]
    return (
        f"<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>"
        f"<Relationships xmlns=\"{NS_REL}\">{''.join(items)}</Relationships>"
    )


# ===== XML generation functions with multi-slide support =====

def presentation_xml(slide_w: int, slide_h: int, slide_count: int = 1) -> str:
    sld_ids = "".join(
        f'<p:sldId id="{256 + i}" r:id="rId{2 + i}"/>'
        for i in range(slide_count)
    )
    return (
        f"<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>"
        f"<p:presentation xmlns:a=\"{NS_A}\" xmlns:r=\"{NS_R}\" xmlns:p=\"{NS_P}\">"
        "<p:sldMasterIdLst><p:sldMasterId id=\"2147483648\" r:id=\"rId1\"/></p:sldMasterIdLst>"
        f"<p:sldIdLst>{sld_ids}</p:sldIdLst>"
        f"<p:sldSz cx=\"{slide_w}\" cy=\"{slide_h}\" type=\"custom\"/>"
        "<p:notesSz cx=\"6858000\" cy=\"9144000\"/>"
        "</p:presentation>"
    )


def presentation_rels_xml(slide_count: int) -> str:
    rels = [
        ("rId1", f"{NS_OFFICE_REL}/slideMaster", "slideMasters/slideMaster1.xml"),
    ]
    for i in range(1, slide_count + 1):
        rels.append((f"rId{i + 1}", f"{NS_OFFICE_REL}/slide", f"slides/slide{i}.xml"))
    return rels_xml(rels)


def content_types_xml(slide_count: int = 1) -> str:
    slide_overrides = "".join(
        f'<Override PartName="/ppt/slides/slide{i}.xml" '
        f'ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>'
        for i in range(1, slide_count + 1)
    )
    return (
        f"<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>"
        f"<Types xmlns=\"{NS_CT}\">"
        "<Default Extension=\"rels\" ContentType=\"application/vnd.openxmlformats-package.relationships+xml\"/>"
        "<Default Extension=\"xml\" ContentType=\"application/xml\"/>"
        "<Default Extension=\"png\" ContentType=\"image/png\"/>"
        "<Override PartName=\"/docProps/app.xml\" ContentType=\"application/vnd.openxmlformats-officedocument.extended-properties+xml\"/>"
        "<Override PartName=\"/docProps/core.xml\" ContentType=\"application/vnd.openxmlformats-package.core-properties+xml\"/>"
        "<Override PartName=\"/ppt/presentation.xml\" ContentType=\"application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml\"/>"
        f"{slide_overrides}"
        "<Override PartName=\"/ppt/slideMasters/slideMaster1.xml\" ContentType=\"application/vnd.openxmlformats-officedocument.presentationml.slideMaster+xml\"/>"
        "<Override PartName=\"/ppt/slideLayouts/slideLayout1.xml\" ContentType=\"application/vnd.openxmlformats-officedocument.presentationml.slideLayout+xml\"/>"
        "<Override PartName=\"/ppt/theme/theme1.xml\" ContentType=\"application/vnd.openxmlformats-officedocument.theme+xml\"/>"
        "</Types>"
    )


def core_props_xml() -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return (
        f"<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>"
        f"<cp:coreProperties xmlns:cp=\"{NS_CP}\" xmlns:dc=\"{NS_DC}\" xmlns:dcterms=\"{NS_DCTERMS}\" xmlns:dcmitype=\"{NS_DCMITYPE}\" xmlns:xsi=\"{NS_XSI}\">"
        "<dc:title>Editable image reconstruction</dc:title>"
        "<dc:creator>image-to-editable-ppt</dc:creator>"
        "<cp:lastModifiedBy>image-to-editable-ppt</cp:lastModifiedBy>"
        f"<dcterms:created xsi:type=\"dcterms:W3CDTF\">{now}</dcterms:created>"
        f"<dcterms:modified xsi:type=\"dcterms:W3CDTF\">{now}</dcterms:modified>"
        "</cp:coreProperties>"
    )


def app_props_xml(slide_count: int = 1) -> str:
    return (
        "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>"
        "<Properties xmlns=\"http://schemas.openxmlformats.org/officeDocument/2006/extended-properties\" xmlns:vt=\"http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes\">"
        "<Application>image-to-editable-ppt</Application>"
        "<PresentationFormat>Custom</PresentationFormat>"
        f"<Slides>{slide_count}</Slides>"
        "</Properties>"
    )


def slide_master_xml() -> str:
    return (
        f"<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>"
        f"<p:sldMaster xmlns:a=\"{NS_A}\" xmlns:r=\"{NS_R}\" xmlns:p=\"{NS_P}\">"
        f"<p:cSld><p:spTree>{group_shape_xml()}</p:spTree></p:cSld>"
        "<p:clrMap bg1=\"lt1\" tx1=\"dk1\" bg2=\"lt2\" tx2=\"dk2\" accent1=\"accent1\" accent2=\"accent2\" accent3=\"accent3\" accent4=\"accent4\" accent5=\"accent5\" accent6=\"accent6\" hlink=\"hlink\" folHlink=\"folHlink\"/>"
        "<p:sldLayoutIdLst><p:sldLayoutId id=\"2147483649\" r:id=\"rId1\"/></p:sldLayoutIdLst>"
        "<p:txStyles><p:titleStyle/><p:bodyStyle/><p:otherStyle/></p:txStyles>"
        "</p:sldMaster>"
    )


def slide_layout_xml() -> str:
    return (
        f"<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>"
        f"<p:sldLayout xmlns:a=\"{NS_A}\" xmlns:r=\"{NS_R}\" xmlns:p=\"{NS_P}\" type=\"blank\" preserve=\"1\">"
        f"<p:cSld name=\"Blank\"><p:spTree>{group_shape_xml()}</p:spTree></p:cSld>"
        "<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr>"
        "</p:sldLayout>"
    )


def theme_xml() -> str:
    return (
        f"<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>"
        f"<a:theme xmlns:a=\"{NS_A}\" name=\"Office Theme\">"
        "<a:themeElements>"
        "<a:clrScheme name=\"Office\">"
        "<a:dk1><a:sysClr val=\"windowText\" lastClr=\"000000\"/></a:dk1>"
        "<a:lt1><a:sysClr val=\"window\" lastClr=\"FFFFFF\"/></a:lt1>"
        "<a:dk2><a:srgbClr val=\"1F2937\"/></a:dk2>"
        "<a:lt2><a:srgbClr val=\"F9FAFB\"/></a:lt2>"
        "<a:accent1><a:srgbClr val=\"2563EB\"/></a:accent1>"
        "<a:accent2><a:srgbClr val=\"10B981\"/></a:accent2>"
        "<a:accent3><a:srgbClr val=\"F59E0B\"/></a:accent3>"
        "<a:accent4><a:srgbClr val=\"EF4444\"/></a:accent4>"
        "<a:accent5><a:srgbClr val=\"8B5CF6\"/></a:accent5>"
        "<a:accent6><a:srgbClr val=\"06B6D4\"/></a:accent6>"
        "<a:hlink><a:srgbClr val=\"0000FF\"/></a:hlink>"
        "<a:folHlink><a:srgbClr val=\"800080\"/></a:folHlink>"
        "</a:clrScheme>"
        "<a:fontScheme name=\"Office\"><a:majorFont><a:latin typeface=\"Arial\"/></a:majorFont><a:minorFont><a:latin typeface=\"Arial\"/></a:minorFont></a:fontScheme>"
        "<a:fmtScheme name=\"Office\"><a:fillStyleLst><a:solidFill><a:schemeClr val=\"phClr\"/></a:solidFill></a:fillStyleLst><a:lnStyleLst><a:ln w=\"9525\"><a:solidFill><a:schemeClr val=\"phClr\"/></a:solidFill></a:ln></a:lnStyleLst><a:effectStyleLst><a:effectStyle><a:effectLst/></a:effectStyle></a:effectStyleLst><a:bgFillStyleLst><a:solidFill><a:schemeClr val=\"phClr\"/></a:solidFill></a:bgFillStyleLst></a:fmtScheme>"
        "</a:themeElements>"
        "</a:theme>"
    )


def slide_master_rels_xml() -> str:
    return rels_xml([
        ("rId1", f"{NS_OFFICE_REL}/slideLayout", "../slideLayouts/slideLayout1.xml"),
        ("rId2", f"{NS_OFFICE_REL}/theme", "../theme/theme1.xml"),
    ])


def slide_layout_rels_xml() -> str:
    return rels_xml([
        ("rId1", f"{NS_OFFICE_REL}/slideMaster", "../slideMasters/slideMaster1.xml"),
    ])


# ===== Manifest processing =====

def process_one_manifest(
    manifest_path: Path,
    *,
    start_media_index: int = 1,
    start_shape_id: int = 2,
) -> tuple[SlideData, int, int]:
    """Process a single manifest and return SlideData plus (next_media_index, next_shape_id)."""
    manifest_path = manifest_path.resolve()
    manifest_dir = manifest_path.parent
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    source_image = resolve_path(manifest_dir, manifest.get("source_image"))
    if source_image is None or not source_image.exists():
        raise FileNotFoundError(f"source_image not found: {manifest.get('source_image')!r}")

    with Image.open(source_image) as img:
        image_w, image_h = img.size

    slide_px = manifest.get("slide_size_px") or manifest.get("canvas_px") or [image_w, image_h]
    coord_w, coord_h = float(slide_px[0]), float(slide_px[1])
    slide_in = manifest.get("slide_size_in")
    if slide_in:
        slide_w_in, slide_h_in = float(slide_in[0]), float(slide_in[1])
    elif abs((coord_w / coord_h) - (16 / 9)) < 0.02:
        slide_w_in, slide_h_in = 13.333333, 7.5
    else:
        slide_w_in = 10.0
        slide_h_in = 10.0 * coord_h / coord_w
    slide_w = int(round(slide_w_in * EMU_PER_INCH))
    slide_h = int(round(slide_h_in * EMU_PER_INCH))
    scale_x = slide_w / coord_w
    scale_y = slide_h / coord_h

    crop_dir = resolve_path(manifest_dir, manifest.get("crop_dir"))
    if crop_dir is None:
        crop_dir = manifest_dir / f"{source_image.stem}_crops"
    crop_dir.mkdir(parents=True, exist_ok=True)

    elements_xml: list[str] = []
    media_items: list[MediaItem] = []
    shape_id = start_shape_id
    media_index = start_media_index

    # Determine manifest format version
    is_v2 = manifest.get("manifest_version", "").startswith("2") or any(
        "user_type" in el and "annotation" in el for el in manifest.get("elements", [])
    )

    def resolve_type(el: dict[str, Any]) -> str:
        if is_v2:
            user_type = el.get("user_type", "")
            ai = el.get("ai_processed") or {}
            ai_type = ai.get("type", "")
            type_map = {
                "text_area": "text", "shape_bg": "shape",
                "icon": "image", "keep_image": "image",
                "convert_svg": "image", "decorative": "image", "formula": "image",
            }
            if ai_type in ("text", "shape", "image", "svg"):
                return ai_type
            return type_map.get(user_type, "shape")
        return str(el.get("type", "shape"))

    image_types = {"image", "photo", "icon", "logo", "picture"}
    text_types = {"text", "number", "label"}
    shape_types = {"shape", "box", "rounded_box", "bar", "footer", "sidebar", "divider", "line"}

    def process_element(el: dict[str, Any], index: int, depth: int = 0) -> None:
        nonlocal shape_id, media_index
        if el.get("visible") is False:
            return

        el_type = resolve_type(el)
        ai = el.get("ai_processed") or {}
        sub_elements = ai.get("sub_elements") or el.get("sub_elements") or []
        has_sub = isinstance(sub_elements, list) and len(sub_elements) > 0

        if el_type in image_types:
            cropped = crop_or_copy_image(manifest_dir, source_image, crop_dir, el, index)
            target_name = f"image{media_index}.png"
            media = MediaItem(rid=f"rId{media_index + 1}", source_path=cropped, target_name=target_name)
            media_items.append(media)
            elements_xml.append(picture_xml(shape_id, el, media, scale_x, scale_y))
            media_index += 1
        elif el_type in text_types:
            elements_xml.append(text_xml(shape_id, el, scale_x, scale_y))
        elif el_type in shape_types:
            elements_xml.append(shape_xml(shape_id, el, scale_x, scale_y))
            text_content = get_ai_attr(el, "text", el.get("text"))
            if text_content and str(text_content).strip():
                text_el = dict(el)
                text_el["annotation"] = dict(el.get("annotation", {}))
                ai_copy = dict(ai)
                text_el["ai_processed"] = ai_copy
                text_el["text"] = text_content
                shape_id += 1
                elements_xml.append(text_xml(shape_id, text_el, scale_x, scale_y))
        else:
            raise ValueError(f"Unsupported element type: {el_type} (user_type={el.get('user_type')})")

        shape_id += 1

        if has_sub:
            for j, sub in enumerate(sub_elements, start=1):
                sub_with_parent = dict(sub)
                if "annotation" not in sub_with_parent:
                    sub_with_parent["annotation"] = el.get("annotation", {})
                process_element(sub_with_parent, index * 100 + j, depth + 1)

    for i, el in enumerate(manifest.get("elements", []), start=1):
        process_element(el, i)

    bg = normalize_color(manifest.get("background"), None)

    # Build background: source image with annotated regions erased (filled white)
    bg_image_rid = None
    if source_image and source_image.exists():
        bg_source_path = source_image
        elements_list = manifest.get("elements", [])
        if elements_list:
            from PIL import ImageDraw as PILImageDraw
            bg_img = Image.open(source_image).convert("RGBA")
            for el in elements_list:
                try:
                    ex, ey, ew, eh = get_bbox(el)
                    left, top = math.floor(ex), math.floor(ey)
                    right = math.ceil(ex + ew)
                    bottom = math.ceil(ey + eh)
                    m_poly = get_mask_polygon(el)
                    u_mask = get_use_mask(el)
                    if u_mask and m_poly and len(m_poly) >= 3:
                        m = Image.new("L", bg_img.size, 0)
                        PILImageDraw.Draw(m).polygon([(p[0], p[1]) for p in m_poly], fill=255)
                        white = Image.new("RGBA", bg_img.size, (255, 255, 255, 255))
                        bg_img = Image.composite(white, bg_img, m)
                    else:
                        white = Image.new("RGBA", (right - left, bottom - top), (255, 255, 255, 255))
                        bg_img.paste(white, (left, top))
                except Exception:
                    pass
            bg_erased_path = crop_dir / f"bg_erased_{start_media_index}.png"
            bg_img.save(bg_erased_path)
            bg_source_path = bg_erased_path

        bg_target = f"slide_bg{start_media_index}.png"
        bg_media = MediaItem(rid=f"rIdBg{start_media_index}", source_path=bg_source_path, target_name=bg_target)
        media_items.append(bg_media)
        bg_image_rid = bg_media.rid
        media_index += 1

    slide_data = SlideData(
        slide_w=slide_w,
        slide_h=slide_h,
        elements_xml=elements_xml,
        media_items=media_items,
        bg=bg,
        bg_image_rid=bg_image_rid,
    )
    return slide_data, media_index, shape_id


def build_multi(manifest_paths: list[Path], output: Path | None = None) -> Path:
    """Build a multi-slide PPTX from multiple manifests."""
    if not manifest_paths:
        raise ValueError("No manifest files provided")

    slides: list[SlideData] = []
    total_media = 1
    total_shapes = 2
    errors: list[str] = []

    for i, mpath in enumerate(manifest_paths):
        try:
            slide_data, total_media, total_shapes = process_one_manifest(
                mpath, start_media_index=total_media, start_shape_id=total_shapes,
            )
            slides.append(slide_data)
        except Exception as e:
            errors.append(f"  Slide {i + 1} ({mpath.name}): {e}")

    if errors:
        raise RuntimeError(
            f"Failed to process {len(errors)} manifest(s):\n" + "\n".join(errors)
        )

    n = len(slides)
    slide_w = slides[0].slide_w
    slide_h = slides[0].slide_h

    # Determine output path
    if output is None:
        first_dir = manifest_paths[0].parent
        output = first_dir / "presentation.pptx"
    output.parent.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(str(output), "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types_xml(slide_count=n))
        z.writestr("_rels/.rels", rels_xml([
            ("rId1", f"{NS_OFFICE_REL}/officeDocument", "ppt/presentation.xml"),
            ("rId2", f"{NS_OFFICE_REL}/extended-properties", "docProps/app.xml"),
            ("rId3", f"{NS_OFFICE_REL}/metadata/core-properties", "docProps/core.xml"),
        ]))
        z.writestr("docProps/core.xml", core_props_xml())
        z.writestr("docProps/app.xml", app_props_xml(slide_count=n))
        z.writestr("ppt/presentation.xml", presentation_xml(slide_w, slide_h, slide_count=n))
        z.writestr("ppt/_rels/presentation.xml.rels", presentation_rels_xml(slide_count=n))

        for idx, slide in enumerate(slides, start=1):
            z.writestr(
                f"ppt/slides/slide{idx}.xml",
                slide_xml(slide.slide_w, slide.slide_h, slide.bg, slide.elements_xml, slide.bg_image_rid),
            )
            slide_rels = [
                ("rId1", f"{NS_OFFICE_REL}/slideLayout", "../slideLayouts/slideLayout1.xml"),
            ]
            for media in slide.media_items:
                slide_rels.append((media.rid, f"{NS_OFFICE_REL}/image", f"../media/{media.target_name}"))
            z.writestr(f"ppt/slides/_rels/slide{idx}.xml.rels", rels_xml(slide_rels))

        z.writestr("ppt/slideMasters/slideMaster1.xml", slide_master_xml())
        z.writestr("ppt/slideMasters/_rels/slideMaster1.xml.rels", slide_master_rels_xml())
        z.writestr("ppt/slideLayouts/slideLayout1.xml", slide_layout_xml())
        z.writestr("ppt/slideLayouts/_rels/slideLayout1.xml.rels", slide_layout_rels_xml())
        z.writestr("ppt/theme/theme1.xml", theme_xml())

        for slide in slides:
            for media in slide.media_items:
                z.write(media.source_path, posixpath.join("ppt/media", media.target_name))

    return output


def build(manifest_path: Path) -> Path:
    """Backward-compatible single-slide build."""
    return build_multi([manifest_path])


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build editable PPTX from image reconstruction manifest(s). "
                    "Pass multiple manifests to create a multi-slide presentation."
    )
    parser.add_argument("manifests", type=Path, nargs="+",
                        help="Path(s) to enriched manifest JSON (supports glob patterns like 'enriched-*.json').")
    parser.add_argument("--output", "-o", type=Path,
                        help="Output PPTX path (required for multi-slide, optional for single).")
    args = parser.parse_args()

    # Expand glob patterns
    manifest_paths: list[Path] = []
    for p in args.manifests:
        expanded = list(Path().glob(str(p))) if '*' in str(p) else [p]
        manifest_paths.extend(expanded)

    manifest_paths = sorted(set(p.resolve() for p in manifest_paths))

    if not manifest_paths:
        parser.error("No manifest files found matching the given path(s).")

    if len(manifest_paths) == 1 and args.output is None:
        # Single manifest, backward-compatible: derive output from manifest
        output = build(manifest_paths[0])
    else:
        if args.output is None:
            parser.error("--output is required when processing multiple manifests.")
        output = build_multi(manifest_paths, output=args.output)

    print(output)


if __name__ == "__main__":
    main()
