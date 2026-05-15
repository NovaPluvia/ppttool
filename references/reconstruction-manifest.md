# Reconstruction Manifest Schema

This document describes the JSON manifest format consumed by `build_editable_pptx.py`. The script accepts both v1 (legacy) and v2 (annotator) formats.

- **v1**: Original format with direct `bbox` and `type` fields on each element
- **v2**: Annotator format with `annotation.bbox`, `user_type`, and `ai_processed` sections

---

## v1 Legacy Format (Full Reference)

### Top-Level Fields

```json
{
  "source_image": "input.png",
  "output_pptx": "input_rebuilt.pptx",
  "slide_size_px": [1440, 810],
  "slide_size_in": [13.333333, 7.5],
  "crop_dir": "input_crops",
  "background": "#FFFFFF",
  "elements": []
}
```

- `source_image`: Required. Path to the original image.
- `output_pptx`: Optional. Defaults to `<source stem>_rebuilt.pptx`.
- `slide_size_px`: Optional. Coordinate space. Defaults to source image size.
- `slide_size_in`: Optional. Slide inches. Defaults to 13.333×7.5 for 16:9.
- `crop_dir`: Optional. Where cropped assets go. Defaults to `<source stem>_crops`.
- `background`: Optional slide background hex color.
- `elements`: Required. List of element objects.

### Coordinates

```json
{ "bbox": [x, y, width, height] }
{ "bbox_xyxy": [left, top, right, bottom] }
```

### Editable Shapes

```json
{
  "type": "shape",
  "name": "bottom bar",
  "shape": "rect",
  "bbox": [0, 728, 1440, 82],
  "fill": "#111827",
  "stroke": "none",
  "opacity": 1
}
```

Supported shapes: `rect`, `roundRect`, `ellipse`, `line`. Type aliases: `box`, `rounded_box`, `bar`, `footer`, `sidebar`, `divider`.

### Editable Text

```json
{
  "type": "text",
  "name": "title",
  "bbox": [80, 72, 760, 72],
  "text": "Rebuilt headline",
  "font": "Arial",
  "font_size": 34,
  "bold": true,
  "color": "#111111",
  "align": "left",
  "valign": "top",
  "margin": 0
}
```

Type aliases: `text`, `number`, `label`.

### Cropped Picture Objects

```json
{
  "type": "image",
  "name": "hero photo",
  "bbox": [860, 88, 420, 280],
  "crop": true
}
```

Type aliases: `image`, `photo`, `icon`, `logo`, `picture`.

---

## v2 Annotator Format

### Top-Level Fields

```json
{
  "manifest_version": "2.0",
  "source_image": "source_image.png",
  "source_image_w": 1440,
  "source_image_h": 810,
  "annotator": {
    "tool": "image-to-editable-ppt",
    "version": "2.0",
    "output_at": "2026-05-14T12:00:00Z"
  },
  "background": "#FFFFFF",
  "elements": []
}
```

### Element Fields

Each element:

```json
{
  "id": "r001",
  "user_type": "text_area",
  "label": "主标题",
  "annotation": {
    "tool": "rect",
    "bbox": [100, 50, 600, 80],
    "mask_polygon": null,
    "use_mask": false
  },
  "ai_processed": {
    "type": "text",
    "text": "欢迎使用本系统",
    "font": "Microsoft YaHei",
    "font_size": 28,
    "bold": true,
    "color": "#1F2937",
    "align": "center"
  }
}
```

| user_type | ai_processed.type | PPT Output |
|-----------|------------------|------------|
| `text_area` | `"text"` | Editable text box (with optional shape bg) |
| `shape_bg` | `"shape"` | Native shape (rect/roundRect) |
| `icon` | `"image"` | Cropped PNG with optional mask transparency |
| `keep_image` | `"image"` | Rectangular PNG crop |
| `convert_svg` | `"image"` | PNG (SVG marker for future use) |
| `decorative` | `"image"` | Cropped PNG |

### ai_processed Details

**text_area:**
```json
{ "type": "text", "text": "...", "font": "...", "font_size": 28, "bold": true, "color": "#000000", "align": "left", "fill": null, "sub_elements": [] }
```

**shape_bg:**
```json
{ "type": "shape", "fill": "#F3F4F6", "stroke": null, "shape": "rect" }
```

**icon / keep_image:**
```json
{ "type": "image", "source": "crops/r002.png", "width": 32, "height": 32 }
```

---

## Complete Example (v2)

```json
{
  "manifest_version": "2.0",
  "source_image": "screenshot.png",
  "source_image_w": 1440,
  "source_image_h": 810,
  "background": "#FFFFFF",
  "elements": [
    {
      "id": "r001",
      "user_type": "text_area",
      "label": "页面标题",
      "annotation": {
        "tool": "rect",
        "bbox": [80, 40, 580, 56],
        "mask_polygon": null,
        "use_mask": false
      },
      "ai_processed": {
        "type": "text",
        "text": "数据分析报告",
        "font": "Microsoft YaHei",
        "font_size": 28,
        "bold": true,
        "color": "#1A1A2E",
        "align": "left"
      }
    },
    {
      "id": "r002",
      "user_type": "icon",
      "label": "用户头像",
      "annotation": {
        "tool": "lasso",
        "bbox": [720, 35, 48, 48],
        "mask_polygon": [[720,35], [768,35], [768,83], [720,83]],
        "use_mask": true
      },
      "ai_processed": {
        "type": "image",
        "source": "crops/r002.png",
        "width": 48,
        "height": 48
      }
    },
    {
      "id": "r003",
      "user_type": "shape_bg",
      "label": "左侧导航栏",
      "annotation": {
        "tool": "rect",
        "bbox": [0, 0, 200, 810],
        "mask_polygon": null,
        "use_mask": false
      },
      "ai_processed": {
        "type": "shape",
        "fill": "#1F2937",
        "stroke": null,
        "shape": "rect"
      }
    },
    {
      "id": "r004",
      "user_type": "keep_image",
      "label": "数据图表",
      "annotation": {
        "tool": "rect",
        "bbox": [240, 120, 800, 400],
        "mask_polygon": null,
        "use_mask": false
      },
      "ai_processed": {
        "type": "image",
        "source": "crops/r004_full.png",
        "width": 800,
        "height": 400
      }
    }
  ]
}
```
