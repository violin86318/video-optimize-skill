---
name: video-optimize
description: 爆款视频拆解与优化专家。输入视频链接或本地文件，自动完成下载→压缩→AI分析→8维度爆款拆解+5进阶模块→逐场景细拆→生成可视化HTML报告。
---

# Video Optimize — 爆款视频拆解与优化

## 触发方式

`/video-optimize <视频链接或本地路径>`

支持平台：B站、YouTube、小红书、抖音、本地文件

## 执行协议

### 1. 收到链接后直接开始，不反复确认

判断输入类型：
- **本地文件**：直接传入脚本
- **B站/YouTube 链接**：用 yt-dlp 下载
- **小红书/抖音链接**：读取 FALLBACK.md 走浏览器方案

### 2. 执行命令

```bash
python3 ~/.claude/skills/video-optimize/scripts/video_analyzer.py run \
  "<URL或本地路径>" \
  --title "<用户提供或自动生成的标题>" \
  --archive-dir ./outputs/reports
```

始终使用 `--archive-dir ./outputs/reports` 和 `--title` 参数。

### 3. 输出报告

分析完成后：
- 将 `report.html` 作为 `<file type="static">` 附件直接输出
- 在对话中输出完整 Markdown 拆解报告，包含：
  - 总评分 + 一句话总结
  - 8 维度评分表（表格形式）
  - 时间线章节概览
  - 情绪弧线关键转折点
  - 爆款公式摘要
  - 可复制模板（结构 + 文案）
  - TOP 3 亮点
  - TOP 3 改进建议

### Markdown 输出格式参考

```markdown
## 📊 爆款视频拆解报告: {标题}

**总评分: {overall_score}/10** — {summary}

### 📈 8 维度评分

| 维度        | 评分                    | 说明                       |
| ----------- | ----------------------- | -------------------------- |
| 🎯 Hook 开头 | {hook.score}/10         | {hook.description}         |
| 📖 叙事结构  | {narrative.score}/10    | {narrative.description}    |
| 🥁 节奏感    | {pacing.score}/10       | {pacing.description}       |
| 🎨 视觉构图  | {visual.score}/10       | {visual.description}       |
| 📝 字幕设计  | {text_overlay.score}/10 | {text_overlay.description} |
| 🎵 音乐音效  | {audio.score}/10        | {audio.description}        |
| 📢 互动引导  | {cta.score}/10          | {cta.description}          |
| 🔚 结尾设计  | {ending.score}/10       | {ending.description}       |

### 🗺️ 时间线

| 时间段        | 章节    | 描述          |
| ------------- | ------- | ------------- |
| {start}-{end} | {label} | {description} |

### 🔥 爆款公式

**脚本公式:** {steps}
**情绪公式:** {nodes}

### 📋 可复制模板

**结构:** {structure}
**文案模板:** {script_template}

### 🌟 TOP 3 亮点
1. {strength1}
2. {strength2}
3. {strength3}

### 🔧 TOP 3 改进建议
1. {improvement1}
2. {improvement2}
3. {improvement3}
```

## 异常处理

### yt-dlp 下载失败 (HTTP 412/403)
1. 脚本会 exit code 2
2. **自动读取** `~/.claude/skills/video-optimize/FALLBACK.md`
3. 按 FALLBACK.md 中对应平台的浏览器方案下载视频
4. 下载成功后，用本地路径重新执行分析

### 小红书/抖音链接
1. 脚本检测到平台后 exit code 2
2. **自动读取** `~/.claude/skills/video-optimize/FALLBACK.md`
3. 按浏览器方案下载
4. 用本地路径重新执行分析

### API 调用失败
- 脚本内置自动重试 3 次（递增等待）
- 如果 3 次都失败，报告错误信息

### 视频过大
- 脚本自动压缩到 35MB（base64 后 <50MB）
- 如果一次压缩不够，自动二次压缩（更激进参数）

## 依赖检查

运行前确保以下工具可用：
- `python3`（标准库即可，无需 pip 安装）
- `ffmpeg` / `ffprobe`（`brew install ffmpeg`）
- `yt-dlp`（`brew install yt-dlp`，仅 B站/YouTube 需要）

## 环境变量配置

API 密钥通过环境变量读取，**不要硬编码在代码中**。执行分析前确保已设置：

```bash
export DOUBAO_API_KEY="你的API密钥"
# 以下为可选，有默认值：
# export DOUBAO_MODEL="doubao-seed-2-0-pro-260215"
# export DOUBAO_API_ENDPOINT="https://ark.cn-beijing.volces.com/api/v3/responses"
```

如果用户未设置 `DOUBAO_API_KEY`，脚本会报错并提示设置方法。
