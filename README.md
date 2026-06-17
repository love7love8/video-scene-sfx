# 视频音效匹配 (video-scene-sfx)

自动识别视频画面场景，联想匹配音效，从站长之家下载并合成。

## 核心逻辑

先识别画面内容 → 联想该场景应有的声音 → 用中文关键词在站长之家搜索 → curl 下载 → ffmpeg 合成。

## 场景→声音映射

| 场景 | 联想声音 |
|------|---------|
| 登山过程中 | 喘息声 + 脚步声 |
| 登上山顶 | 风声 + 鸟鸣声 |
| 森林 | 虫鸣 + 鸟叫 + 风吹树叶 |
| 海边 | 海浪 + 海鸥 + 风声 |

## 使用

```bash
python3 scripts/video_sfx_match.py <video_file> --output <output.mp4>
```

## 依赖

- ffmpeg, ffprobe, curl, python3
- MINIMAX API (视觉识别)