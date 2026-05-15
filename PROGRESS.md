# Image to Editable PPT - Skill 开发进度

> 最后更新: 2026-05-14

---

## 项目位置

```
C:\Users\Wang\Desktop\ppttool\
```

---

## 项目结构

```
C:\Users\Wang\Desktop\ppttool\
├── SKILL.md                           # 完整工作流定义（6步流程）
├── PROGRESS.md                         # 本文件 - 进度记录
├── .claude/
│   ├── launch.json                     # SAM 服务启动配置 (preview_start)
│   └── settings.local.json             # 权限记录
├── tools/
│   └── annotator.html                  # 标注工具（双击浏览器打开）
├── scripts/
│   ├── build_editable_pptx.py          # PPTX 构建脚本（支持多页批量）
│   ├── sam_service.py                  # SAM 分割服务（Flask + MobileSAM）
│   └── analyze_regions.py              # AI 二次识别（支持批量处理）
├── references/
│   ├── reconstruction-manifest.md      # Manifest schema 文档
│   └── annotation-guide.md             # 标注工具使用说明
├── agents/
│   └── openai.yaml                     # Agent 配置
└── models/                             # SAM 模型文件（首次自动下载）
```

---

## 当前状态

### 已完成

| 组件 | 说明 |
|------|------|
| `tools/annotator.html` | 标注工具完整实现（含多边形渲染、自动编号导出） |
| `scripts/sam_service.py` | SAM 分割服务（HTTP + CLI） |
| `scripts/analyze_regions.py` | AI 二次识别 + 批量处理多 manifests |
| `scripts/build_editable_pptx.py` | PPTX 构建（单页/多页，mask裁剪，嵌套元素） |
| `SKILL.md` | 完整工作流定义 |
| `references/reconstruction-manifest.md` | v2 schema 文档 |
| `references/annotation-guide.md` | 标注工具使用指南 |
| `.claude/launch.json` | SAM 服务启动配置 |

### 标注工具功能

**三种圈定方式：**
- `矩形` — 拖拽画框，松开自动完成（R 快捷键）
- `套索` — 点击开始绘制，再次点击或按 Enter 闭合，画布上显示实际多边形形状（L 快捷键）
- `AI 分割` — 单击目标，SAM 自动识别边界（A 快捷键，需先启动 SAM 服务）

**6 种标记类型：**
- 文字区、背景框、图标、整体保留、转SVG、装饰

**其他功能：**
- 工具栏预选类型（画之前先选好）
- 类型弹窗（画完后可修改）
- 聚焦按钮（比例缩放，展示区域上下文和分割线）
- 撤销、清空
- 导出自动编号 JSON manifest + 源图片（annotation-manifest-001.json）
- 套索支持 Escape 取消绘制

### 最近更新（2026-05-14）

#### AI 识别开关
- 工具栏新增 **"AI 识别"复选框**（默认开启），控制是否需要 OCR + 颜色检测
- 关闭时 manifest 包含 `"ai_recognition": false`，直接走 PPTX 构建
- `build_editable_pptx.py` 已天然兼容无 AI 数据的 manifest

#### 原图作为幻灯片背景 + 标注区域擦除
- `build_editable_pptx.py`：幻灯片背景 = 原图去掉标注区域（填白）
- 矩形区域→bbox填白，套索/AI区域→多边形填白
- 标注区域作为可编辑元素覆盖在上方，不会与背景重叠

#### 公式类型
- 标注工具新增"公式"类型（工具栏+弹窗）
- 分析脚本新增 `analyze_formula_region()` → 裁剪为PNG
- 构建脚本中公式类型映射为 image，输出为独立图片

#### 全图文字自动检测
- `analyze_regions.py`：新增 `--auto-detect-text` 参数
- 启动后用 EasyOCR 扫描全图，自动发现文字区域并创建 text_area 元素
- 无需手动标注每一个文字块，AI 自动识别位置和内容

#### 透明边距裁剪
- `analyze_regions.py` 和 `build_editable_pptx.py`：mask 透明处理后去掉透明边距
- 套索区域 PNG 只保留形状最小范围，不包含外部元素

#### 套索交互优化
- 改为点击模式：点击开始绘制，自由移动画轨迹，再次点击或按 Enter 闭合
- 不再需要按住鼠标拖画
- 渲染独立于绘制状态，路径始终可见
- 新增 Escape 取消绘制快捷键
- 完成后 `isDrawing` 正确复位，可立即开始新绘制

#### 多边形渲染
- 套索/AI 区域在画布上显示实际多边形形状（不再是矩形）
- 矩形区域仍保持矩形渲染
- 选中状态红色实线，未选中类型颜色虚线
- 标签和工具指示器位置不变

#### 聚焦按钮优化
- 从固定 40px 内边距改为 55% 视口比例缩放
- 聚焦后周围区域边界线自然可见

#### Bug 修复
- `drawRegion` 读取 `region.bbox` → 修正为 `region.annotation.bbox`
- 修复 `const bw/bh` 重复声明问题

#### 导出自动编号
- 导出文件名自动编号：`annotation-manifest-001.json` / `source-image-001.png`
- 每次导出计数器递增，不覆盖之前文件

#### 多图片批量处理
- `analyze_regions.py` 支持批量：`analyze_regions.py *.json --source *.png`
- `build_editable_pptx.py` 支持多页：`build_editable_pptx.py enriched-*.json --output deck.pptx`
- 每张图片独立标注 → 批量分析 → 合成一个多页 PPTX

### 依赖安装状态

| 包名 | 用途 | 状态 |
|------|------|------|
| Pillow | 图片处理 | ✅ 已安装 |
| numpy 1.26.4 | 矩阵运算 | ✅ 已安装 (需降级到<2) |
| Flask + flask-cors | SAM 服务 | ✅ 已安装 |
| PyTorch 2.5.1 (CPU) | 深度学习 | ✅ 已安装 |
| mobile_sam (GitHub) | MobileSAM 模型 | ✅ 已安装 (需 timm 辅助包) |
| opencv-python | 图像处理 | ✅ 已安装 |
| PaddleOCR | OCR 识别 | ✅ 已安装（首次运行会下载模型）|
| timm | PyTorch 图像模型库 | ✅ 已安装 |

### 已验证的端到端流程

1. **单图片流程**: 标注 → analyze_regions → build_editable_pptx ✅
2. **多图片批量**: 多 manifests 分析 → 多页 PPTX（2 slides） ✅
3. **mask 透明 PNG**: 套索区域正确裁剪为透明 PNG ✅

---

## 工作流程（用户视角）

### 单图片流程

1. **准备图片**：复制到工作目录
2. **（可选）启动 SAM 服务**：`python scripts/sam_service.py --port 5678`
3. **打开标注工具**：双击 `tools/annotator.html`，拖入图片
4. **圈定区域**：矩形/套索/AI 分割，标记类型
5. **导出**：点击"导出 JSON"，生成 `annotation-manifest-001.json` + `source-image-001.png`
6. **AI 二次识别**：`python scripts/analyze_regions.py annotation-manifest-001.json --source source-image-001.png`
7. **生成 PPTX**：`python scripts/build_editable_pptx.py annotation-manifest-001_enriched.json`

### 多图片批量流程

1. **逐张标注导出**：每张图标注后导出，编号自动累加
2. **批量分析**：`python scripts/analyze_regions.py annotation-manifest-*.json --source source-image-*.png`
3. **合成多页 PPTX**：`python scripts/build_editable_pptx.py annotation-manifest-*_enriched.json --output deck.pptx`

---

## 已知问题和 TODOs

### 待改进
- [ ] `analyze_regions.py` 的 OCR 在测试图片上未检测到 PIL 生成的文字（真实截图没问题）
- [ ] `analyze_regions.py` 的颜色检测算法较简单，复杂背景可能不准
- [ ] SAM 服务首次使用需下载模型（~40MB），启动需要几秒钟
- [ ] PaddleOCR 首次使用需下载模型（~100MB），后续自动缓存
- [ ] AI 分割交互体验需改进（反馈不够直观）

### 潜在问题
- numpy 版本需要 <2.0（当前 1.26.4），opencv-python 最新版要求 >=2，但实际兼容
- `python3` 命令指向 Windows Store Python stub，需用 `python` 替代
- 中文路径在 Git Bash 中可能有问题

---

## 关键命令速查

```bash
cd C:\Users\Wang\Desktop\ppttool

# 启动 SAM 服务
python scripts/sam_service.py --port 5678

# 单图片：AI 二次识别 + 构建
python scripts/analyze_regions.py annotation-manifest-001.json --source source-image-001.png
python scripts/build_editable_pptx.py annotation-manifest-001_enriched.json

# 多图片批量：分析 + 合成多页 PPTX
python scripts/analyze_regions.py annotation-manifest-*.json --source source-image-*.png
python scripts/build_editable_pptx.py annotation-manifest-*_enriched.json --output deck.pptx

# SAM CLI 模式（不需 Flask）
python scripts/sam_service.py --image input.png --click 500,300 --output mask.png
```

---

## 档案

原始项目来源：`D:\05_Downloads\QQDownload\image-to-editable-ppt\`  
项目迁移自该路径，保留了原有的 `build_editable_pptx.py`（v1 兼容）和配置文件。
