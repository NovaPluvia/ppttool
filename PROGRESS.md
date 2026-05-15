# Image to Editable PPT - Skill 开发进度

> 最后更新: 2026-05-15

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
├── .gitignore                          # Git 忽略规则
├── .claude/
│   ├── launch.json                     # SAM 服务启动配置 (preview_start)
│   └── settings.local.json             # 权限记录
├── tools/
│   └── annotator.html                  # 标注工具（双击浏览器打开）
├── scripts/
│   ├── build_editable_pptx.py          # PPTX 构建脚本（支持多页批量）
│   ├── sam_service.py                  # SAM 分割服务（Flask + MobileSAM）
│   ├── analyze_regions.py              # AI 二次识别（支持批量处理）
│   └── ollama_service.py               # ★ 新增: Ollama 桥接服务（Qwen-VL 一键分析）
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
| `scripts/ollama_service.py` | ★ **新增**: Ollama 桥接服务，Qwen-VL 一键分析图片区域 |
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

### ★ 最近更新（2026-05-15）：Qwen-VL AI 一键分析

#### 新增组件
- **`scripts/ollama_service.py`** — Ollama 桥接 HTTP 服务（端口 5680）
  - `GET /health` — 检测 Ollama 服务状态
  - `POST /analyze` — 发送图片 → Qwen-VL 识别 → 返回结构化区域数据
  - 自动缩放图片（最长边 1024px），坐标映射回原图尺寸
  - 支持 `--model` 参数切换模型
  - 多线程处理，分析时不影响健康检查
  - 新增 `allow_reuse_address = False` 防止端口冲突

#### 标注工具增强（`annotator.html`）
- **🤖 AI 分析按钮** — 一键调用 Qwen-VL 自动识别图片所有区域
- **右侧 AI 识别详情面板** — 选中区域后显示：
  - 文字区：识别文字内容（可编辑）、字体、字号、颜色
  - 形状区：填充色、形状类型
  - 图标/图片区：裁剪缩略图预览
- **画布文字预览** — 选中文字区域时，直接在画布上渲染识别的文字（模拟 PPT 效果）
- **区域类型可修改** — 点击"更改类型"弹窗修改，ai_processed 自动适配
- **导出增强** — JSON 直接包含 `ai_processed` 数据，跳过二次识别步骤

#### 新工作流
```
插入图片 → 点击 🤖 AI 分析 → 自动标注所有区域 →
右侧面板查看/编辑识别结果 →
修改区域类型（如需） →
导出 JSON（含 ai_processed） →
直接 build_editable_pptx.py → PPTX
```

---

## 工作流程（用户视角）

### 单图片 AI 一键流程（推荐）

```bash
# 终端 1：启动 Ollama 桥接服务
cd C:\Users\Wang\Desktop\ppttool
python scripts\ollama_service.py --model qwen3.6:27b

# 浏览器：双击 tools\annotator.html
# → 拖入图片 → 点击 🤖 AI 分析
# → 检查/修改识别结果 → 导出 JSON
# → 直接构建 PPTX（跳过 analyze_regions.py）
python scripts/build_editable_pptx.py annotation-manifest-001.json
```

### 单图片手动标注流程

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
- [ ] Qwen-VL 27B 模型首次分析较慢（>5分钟），建议换用 9B 以下模型

### 潜在问题
- numpy 版本需要 <2.0（当前 1.26.4），opencv-python 最新版要求 >=2，但实际兼容
- `python3` 命令指向 Windows Store Python stub，需用 `python` 替代
- 中文路径在 Git Bash 中可能有问题
