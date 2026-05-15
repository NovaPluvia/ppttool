---
name: image-to-editable-ppt
description: |
  Convert screenshots, UI mockups, diagrams, posters, and other bitmap layouts into editable PowerPoint decks using a human-guided annotation + optional AI secondary recognition workflow.

  USE THIS SKILL WHEN the user provides an image (screenshot, UI mockup, diagram, poster, whiteboard photo, chart capture) and wants it turned into an editable PowerPoint where text, shapes, numbers, icons, and photos are separate editable objects — NOT a flat background image.

  This skill has a 6-step workflow: (1) copy image, (2) optionally start SAM service, (3) open the HTML annotation tool for the user to manually circle regions, (4) export manifest, (5) optionally run AI secondary recognition (OCR, color/font/dimension analysis) — user can toggle this in the annotator, (6) build the final PPTX.

  Also supports BATCH mode: multiple images → multiple annotation manifests → batch AI analysis or direct build → single multi-slide PPTX.

  Do NOT use this skill for: simple image-to-PNG-in-PPT (use "insert picture"), creating presentations from scratch (use a presentation skill), or OCR-only tasks (use a document skill).
allowed-tools:
  - Read
  - Write
  - Edit
  - Bash
  - Glob
  - Grep
  - Agent
  - WebSearch
---

# 图片转可编辑 PPT — 半自动化工作流

## 核心原则

**人工圈定意图 + AI 填充细节。** 用户通过标注工具告诉 AI 哪些区域是文字、哪些是背景框、哪些是图标、哪些整体保留，AI 负责 OCR 识别、颜色检测、字体匹配等精细化填充。

**绝不将原图作为扁平背景。** 输出 PPT 中的所有文本、形状、图标都必须是原生可编辑对象。

**分割深度 = 用户可能的调整意图深度。** 如果用户不会单独调整某个子元素，就不要分割它。

---

## 完整工作流（6 步）

### Step 1：准备图片

将用户提供的图片复制到工作目录：

```bash
cp "<用户图片路径>" "source_image.png"
```

如果是多张图片，命名为 `source-image-001.png`、`source-image-002.png` 等。

### Step 2（可选）：启动 SAM 分割服务

如果用户需要使用"AI 点击分割"功能（用 AI 自动识别元素边界），启动 SAM 服务：

```bash
cd /d C:\Users\Wang\Desktop\ppttool
python scripts/sam_service.py --port 5678
```

> 首次启动会自动下载 MobileSAM 模型（约 40MB）。服务启动后标注工具的 AI 分割按钮会自动激活。SAM 服务仅在需要 AI 点击分割时启动。

### Step 3：打开标注工具

告诉用户打开标注工具，对图片进行圈定：

```
📂 C:\Users\Wang\Desktop\ppttool\tools\annotator.html
双击此文件在浏览器中打开
```

**告诉用户使用方式：**

> **矩形工具（□）** — 拖拽画框，适合元素周围有留白的场景
> **套索工具（✏）** — 点击开始绘制，鼠标自由移动画轨迹，再次点击或按 Enter 闭合。画布上会显示实际的多边形形状
> **AI 分割（🤖）** — 需要先启动 SAM 服务，单击目标自动识别边界
> **平移（✋）** — 拖拽平移视图（或按 P 键切换）
> **聚焦** — 选中区域后点击右侧面板的"聚焦"按钮，缩放至区域并展示周围上下文
> **AI 识别复选框** — 工具栏右侧，默认开启。关闭后跳过 OCR/颜色检测，直接生成 PPTX
> **提示**：不想手动圈文字？AI 识别步骤加 `--auto-detect-text` 会自动发现文字区域

> **PPT 背景**：背景为原图去掉标注区域（填白），标注元素覆盖在上方，不重叠。
> **滚轮缩放** — 放大/缩小细节
> **Escape** — 取消正在进行的套索绘制

每次圈定后选择该区域的类型：

| 类型 | 含义 | 后续处理 |
|------|------|----------|
| 文字区 (Aa) | 包含文字的矩形区域 | OCR + 字体/颜色检测（AI 开启时） |
| 背景框 (▢) | 纯色/渐变背景框、卡片 | 转为 PPT 形状，检测填充色 |
| 图标/装饰 (🖼) | Logo、图标、小装饰图 | 从原图裁剪为带透明背景的 PNG |
| 整体保留 (📷) | 复杂图表、照片等不需分割的区域 | 整体裁剪为图片 |
| 转 SVG (✦) | 用户指定的矢量化区域 | 转换为 SVG（仅用户指定才转） |
| 装饰元素 (~) | 纯装饰线条/图形 | 视情况简化或保留 |
| 公式 (∫) | 数学公式 | 裁剪为 PNG 图片 |

圈定完成后点击 **"导出 JSON"** 按钮，浏览器会下载两个文件：
- `annotation-manifest-001.json` — 标注数据（编号自动递增，含 `ai_recognition` 标志）
- `source-image-001.png` — 源图片

多张图片时，每标注完一张导出一次，编号自动累加。

### Step 4：检查导出的 manifest

确认 `annotation-manifest-xxx.json` 和 `source-image-xxx.png` 已存在于工作目录。

### Step 5：生成 PPTX（根据 AI 识别开关二选一）

导出的 manifest 中包含 `ai_recognition` 字段，根据其值选择对应流程：

#### 路径 A：AI 识别开启（`ai_recognition: true`）

先运行 AI 二次识别，再进行 PPTX 构建：

**单图片模式：**
```bash
# --auto-detect-text 可选，自动发现文字区域（无需手动圈文字）
python scripts/analyze_regions.py annotation-manifest-001.json \
  --source source-image-001.png \
  --auto-detect-text
python scripts/build_editable_pptx.py annotation-manifest-001_enriched.json
```

**多图片批量模式：**
```bash
python scripts/analyze_regions.py annotation-manifest-*.json \
  --source source-image-001.png source-image-002.png \
  --auto-detect-text
python scripts/build_editable_pptx.py annotation-manifest-*_enriched.json --output deck.pptx
```

`analyze_regions.py` 会处理以下内容：

- **文字区 (text_area)** → 用 EasyOCR 识别文本 → 检测字体颜色、大小
- **背景框 (shape_bg)** → 检测填充色、是否圆角
- **图标 (icon)** → 按 mask 精确裁剪为带透明背景的 PNG（已裁剪到最小范围）
- **整体保留 (keep_image)** → 按矩形裁剪为 PNG
- **转 SVG (convert_svg)** → 标记为 SVG 区域
- **装饰 (decorative)** → 标记为装饰元素

OCR 结果需要**人工校对**。如果识别有误，可以编辑 `ai_processed.text` 字段修正。

#### 路径 B：AI 识别关闭（`ai_recognition: false`）

直接构建 PPTX，跳过 OCR 和颜色检测：

```bash
# 单图片
python scripts/build_editable_pptx.py annotation-manifest-001.json

# 多图片合成多页 PPTX
python scripts/build_editable_pptx.py annotation-manifest-*.json --output deck.pptx
```

AI 关闭时：
- 文字区域生成**空文本框**（无 OCR 识别内容，需在 PPT 中手动填写）
- 图标/形状按标注的 bbox 从原图裁剪（套索区域仍为透明 PNG）
- 背景框按标注的颜色/位置生成形状

> **两种路径的共同点**：PPT 背景为原图去掉标注区域（填白），标注区域作为可编辑元素覆盖在上方。未标注内容通过背景保留，标注区域不会与背景重叠。

---

## 批量处理速查

```bash
# 1. 逐张标注导出
#    打开 tools/annotator.html → 可关闭"AI 识别"复选框 → 标注 → 导出

# 2. 根据导出 manifest 中的 ai_recognition 标志选择流程
#    AI 开启 → 分析 + 构建
python scripts/analyze_regions.py annotation-manifest-*.json \
  --source source-image-001.png source-image-002.png source-image-003.png
python scripts/build_editable_pptx.py annotation-manifest-*_enriched.json \
  --output presentation.pptx

#    AI 关闭 → 直接构建
python scripts/build_editable_pptx.py annotation-manifest-*.json \
  --output presentation.pptx
```

如果所有图片共用同一个源目录，`--source` 也可以只给一个值，脚本会自动广播给所有 manifests。

---

## 验证清单

- [ ] 幻灯片背景为原图，未标注部分通过背景保留
- [ ] 文字、数字、背景框、装饰条等均为可编辑的 PPT 原生对象
- [ ] 图标和照片是独立的裁剪图片对象（套索区域为带透明 PNG，已裁剪到最小范围）
- [ ] 公式区域作为独立图片裁剪（如需要可后续转为 MathML）
- [ ] 相对位置和 Z 序与原始图片一致
- [ ] AI 识别开启：OCR 结果经过人工校对
- [ ] AI 识别关闭 / 自动检测：文字区域已正确定位

---

## 快捷键速查

| 快捷键 | 功能 |
|--------|------|
| `R` | 切换矩形工具 |
| `L` | 切换套索工具 |
| `A` | 切换 AI 分割工具（需 SAM 服务） |
| `P` | 切换平移工具 |
| `Z` | 适应窗口大小 |
| `Enter` | 完成套索（闭合多边形） |
| `Escape` | 关闭弹窗 / 取消套索绘制 |
| `Delete` / `Backspace` | 删除选中的区域 |

---

## 文件结构

```
C:\Users\Wang\Desktop\ppttool\
├── SKILL.md                          # 本技能定义
├── PROGRESS.md                        # 开发进度记录
├── tools/
│   └── annotator.html                # ★ 标注工具（双击打开）
├── scripts/
│   ├── build_editable_pptx.py        # PPTX 构建脚本（支持多页）
│   ├── sam_service.py                # SAM 分割服务
│   └── analyze_regions.py            # AI 二次识别（支持批量）
├── references/
│   ├── reconstruction-manifest.md    # Manifest schema 参考
│   └── annotation-guide.md           # 标注工具使用说明
├── models/                           # SAM 模型文件（自动下载）
└── agents/
    └── openai.yaml
```

---

## 常见问题

**Q: 不需要 SAM 服务该怎么办？**
矩形和套索工具不需要 SAM 服务，可以直接使用。只有当需要"AI 点击分割"时才启动 SAM。

**Q: OCR 识别结果不准确怎么办？**
编辑 `enriched_manifest.json` 中对应区域的 `ai_processed.text` 字段，然后重新运行 `build_editable_pptx.py`。

**Q: 标注工具导出的 JSON 里没有 `source_image` 字段？**
标注工具会同时导出 `annotation-manifest-xxx.json` 和 `source-image-xxx.png`，在运行 `analyze_regions.py` 时通过 `--source` 参数指定源图片路径。

**Q: 我可以修改圈定区域的类型吗？**
可以。导出的 JSON 是纯文本 JSON，可以直接编辑 `user_type` 字段来更改类型。

**Q: 套索画出来的区域在 PPT 里是透明背景吗？**
是的。套索区域的多边形 mask 会写入下游脚本，`analyze_regions.py` 对图标类型使用 mask 裁剪，生成带透明背景的 PNG，PPT 原生支持透明 PNG。

**Q: 多张图片怎么处理？**
每张图在标注工具中标注后导出，编号自动递增（001, 002, ...）。然后用批量命令一次分析和构建，最后生成一个多页 PPTX。
