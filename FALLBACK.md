# FALLBACK.md — 浏览器 Fallback 下载方案

当 yt-dlp 被反爬（412/403）或平台不支持 yt-dlp 时，使用以下浏览器方案下载视频。

---

## B站 (Bilibili)

### 步骤

1. **打开页面**（可能需要两次以过验证）：
```
browser navigate to <视频URL>
```
如果出现验证码，等待几秒后再次 navigate。

2. **提取视频流地址**：
在浏览器控制台执行：
```javascript
const playinfo = window.__playinfo__;
const dash = playinfo.data.dash;
// 选择 720p 或最低可用清晰度的视频流
const videos = dash.video.sort((a, b) => a.bandwidth - b.bandwidth);
const video_url = videos.find(v => v.height <= 720)?.baseUrl || videos[0].baseUrl;
// 音频流
const audio_url = dash.audio.sort((a, b) => a.bandwidth - b.bandwidth)[0].baseUrl;
JSON.stringify({video_url, audio_url});
```

3. **下载视频和音频**（必须带 Referer 头）：
```bash
curl -L -o /tmp/bilibili_video.m4s \
  -H "Referer: https://www.bilibili.com" \
  -H "User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36" \
  "<video_url>"

curl -L -o /tmp/bilibili_audio.m4s \
  -H "Referer: https://www.bilibili.com" \
  -H "User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36" \
  "<audio_url>"
```

4. **合并音视频**：
```bash
ffmpeg -y -i /tmp/bilibili_video.m4s -i /tmp/bilibili_audio.m4s \
  -c:v copy -c:a copy -movflags +faststart \
  /tmp/bilibili_merged.mp4
```

5. 用 `/tmp/bilibili_merged.mp4` 作为本地路径继续分析。

---

## YouTube

### 步骤

1. **打开页面**：
```
browser navigate to <视频URL>
```

2. **提取视频流地址**：
```javascript
const playerResp = ytInitialPlayerResponse || window.ytInitialPlayerResponse;
const streaming = playerResp.streamingData;

// 优先选择 formats（音视频合一）
let url = null;
if (streaming.formats && streaming.formats.length > 0) {
    const fmt = streaming.formats
        .filter(f => f.height <= 720)
        .sort((a, b) => (b.height || 0) - (a.height || 0))[0]
        || streaming.formats[0];
    url = fmt.url;
}

// 否则用 adaptiveFormats（需要分别下载）
if (!url && streaming.adaptiveFormats) {
    const video = streaming.adaptiveFormats
        .filter(f => f.mimeType.startsWith('video/') && (f.height || 0) <= 720)
        .sort((a, b) => (b.height || 0) - (a.height || 0))[0];
    const audio = streaming.adaptiveFormats
        .filter(f => f.mimeType.startsWith('audio/'))
        .sort((a, b) => (b.bitrate || 0) - (a.bitrate || 0))[0];
    url = JSON.stringify({video_url: video?.url, audio_url: audio?.url, separate: true});
}
url;
```

3. **下载**：
- 如果是合一流（单个 URL），直接 curl 下载：
```bash
curl -L -o /tmp/youtube_video.mp4 "<url>"
```
- 如果是分离流（separate: true），分别下载后合并：
```bash
curl -L -o /tmp/yt_video.mp4 "<video_url>"
curl -L -o /tmp/yt_audio.m4a "<audio_url>"
ffmpeg -y -i /tmp/yt_video.mp4 -i /tmp/yt_audio.m4a \
  -c:v copy -c:a copy -movflags +faststart \
  /tmp/youtube_merged.mp4
```

4. 用下载的 mp4 文件作为本地路径继续分析。

---

## 小红书 (Xiaohongshu)

### 说明
小红书不使用 yt-dlp，直接使用浏览器方案。CDN 完全公开，无需认证头。音视频合一 MP4，不需要 ffmpeg 合并。

### 步骤

1. **打开页面**：
```
browser navigate to <小红书视频URL>
```

2. **提取视频地址**：
```javascript
const state = window.__INITIAL_STATE__;
const noteMap = state.note.noteDetailMap;
const key = Object.keys(noteMap)[0];
const note = noteMap[key].note;
const videoUrl = note.video.media.stream.h264[0].masterUrl;
videoUrl;
```

3. **下载**（CDN 公开，无需特殊 header）：
```bash
curl -L -o /tmp/xiaohongshu_video.mp4 "<videoUrl>"
```

4. 用 `/tmp/xiaohongshu_video.mp4` 作为本地路径继续分析。

---

## 抖音 (Douyin)

### 说明
抖音不使用 yt-dlp，使用浏览器方案。需要在浏览器上下文中调用抖音内部 API。

### 步骤

1. **打开页面**并提取视频 ID：
```
browser navigate to <抖音视频URL>
```
从 URL 中提取 `aweme_id`：
- `https://www.douyin.com/video/7xxxxxxxxxx` → aweme_id = `7xxxxxxxxxx`
- 短链接需要先打开获取重定向后的 URL

2. **调用抖音内部 API**（在浏览器控制台中执行）：
```javascript
const awemeId = '<从URL提取的ID>';
const resp = await fetch(`/aweme/v1/web/aweme/detail/?aweme_id=${awemeId}`, {
    headers: {'Accept': 'application/json'}
});
const data = await resp.json();
const videoUrl = data.aweme_detail.video.play_addr.url_list[0];
videoUrl;
```

3. **下载**（需带 Referer）：
```bash
curl -L -o /tmp/douyin_video.mp4 \
  -H "Referer: https://www.douyin.com/" \
  -H "User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36" \
  "<videoUrl>"
```

4. 用 `/tmp/douyin_video.mp4` 作为本地路径继续分析。

---

## 通用注意事项

1. 浏览器方案下载后，视频保存在 `/tmp/` 目录
2. 下载成功后，用本地路径重新调用分析脚本：
```bash
python3 ~/.claude/skills/video-optimize/scripts/video_analyzer.py run \
  "/tmp/downloaded_video.mp4" \
  --title "视频标题" \
  --archive-dir ./outputs/reports
```
3. 如果浏览器方案也失败，提示用户手动下载视频并提供本地文件路径
