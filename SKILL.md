---
name: 视频音效匹配
description: 视频画面场景识别 + 站长之家匹配音效自动下载 + ffmpeg 合成。核心逻辑：先识别画面内容(MINIMAX-M3) → 联想该场景应有的声音 → 用中文关键词在站长之家搜索 → curl下载 → ffmpeg合成。输入一段视频，自动提取关键帧、识别场景、下载匹配音效、合成到视频里。
version: 1.2.0
tags: [media, video, sfx, audio, vlog, ffmpeg, scene-detection]
author: Hermes Agent
license: MIT
platforms: [linux, macos]
prerequisites:
  commands: [ffmpeg, ffprobe, curl, python3]
metadata:
  hermes:
    tags: [media, video, sfx, ffmpeg, scene-detection, vlog]
---

# 视频场景匹配音效

输入一段视频 → 自动识别画面场景 → 从站长之家下载匹配音效 → 合成到视频里。

> 适用场景：Vlog 环境音增强、AI 生成视频补音效（AI 视频通常无音频）、无声视频修复、短片自动配音效。

## 触发条件

- 用户要给视频加环境音效（"给这个视频加点音效/环境音/背景音"）
- 用户说"视频里的场景需要匹配的声音"
- 用户想给 AI 生成的无声视频配音效
- 用户提到"瀑布要有水声"、"森林要有虫鸣"等画面-音效对应需求
- **用户说"有些画面没配音"** —— 说明场景检测模式漏掉了静止段，应改用 `--frame-method interval`
- **用户说"按秒还是按帧截"** —— 用户在质疑帧提取策略，应该解释两种模式并推荐 interval（vlog）/ scene（动作视频）
- **家庭 vlog / 节奏慢 / 风景 / 城市漫步类视频** —— 默认用 `--frame-method interval` 完整覆盖整段，避免漏段

## 快速选择指南

**不知道用什么模式?** 按视频类型选:

| 视频类型 | 推荐参数 | 原因 |
|---------|---------|------|
| 家庭 vlog（深圳 CBD、滨江步道、城市漫步） | `--frame-method interval --frames-per-scene 4`（55s=12 帧） | 节奏慢、画面变化小，scene 模式只识别 2-3 帧会漏掉大半段 |
| 旅行 vlog（场景频繁切换） | `--frame-method scene` | 一镜到底或动作场景多，scene 模式精准定位变化点 |
| AI 生成视频（每几秒换场景） | `--frame-method scene --frames-per-scene 8` | 场景变化大，多取帧提升识别准确度 |
| 短片/微电影 | `--frame-method scene` | 导演精心设计场景切换，scene 模式能跟随剪辑节奏 |

**不知道用什么音量?** 按场景选:
- 家庭 vlog 有原声/对话/环境音 → `--sfx-volume 0.4`
- 旅行 vlog 有原声/音乐 → `--sfx-volume 0.5-0.6`
- AI 无声视频 → `--sfx-volume 0.8-1.0`

**不确定场景识别对不对?** 永远先跑 `--dry-run` 看识别结果再决定下载/合成。城市/CBD/室内场景识别率低的话用 `vision_analyze` 工具配合详细中文 prompt 逐帧分析（见 Pitfall 18）

## 核心工作流（4 阶段）

### 阶段 1：提取关键帧

用 ffmpeg 场景检测或固定间隔模式从视频中提取关键帧：

**方式 A:场景检测（`--frame-method scene`，默认）**
- ffmpeg `select='gt(scene,0.3)'` —— 只截前后帧差异>0.3 的画面
- 适合**场景切换明显**的视频（vlog 一镜到底、连续动作镜头）
- ⚠️ 缺点：画面静止/变化小的段（连续 10 秒都在同一画面）会被跳过，**那段视频就没配音效**

**方式 B:固定间隔（`--frame-method interval`，vlog 推荐）**
- 按视频时长均匀截 N 帧（如 55s 视频 + `--frames-per-scene 4` = 12 帧 = 每 4.6s 1 帧）
- **完整覆盖整段视频**，每段时间都有机会被识别
- 适合**节奏慢、场景变化不明显**的家庭 vlog、风景视频

```bash
# 场景检测模式（默认）
python3 {baseDir}/scripts/video_sfx_match.py <video_file> \
  --frame-method scene --frames-per-scene 6 --dry-run

# 固定间隔模式（vlog 推荐）
python3 {baseDir}/scripts/video_sfx_match.py <video_file> \
  --frame-method interval --frames-per-scene 4 --dry-run
```

`--dry-run` 只跑阶段 1+2（提帧 + 识别场景），不下载/合成。先 review 识别结果再决定要不要继续。

**参数说明：**
- `--frames-per-scene N`：提取关键帧数（默认 5）。复杂/多场景视频用 8-10；短视频（<60s）用 6-8 防漏场景
- 脚本内部固定用 ffmpeg `select='gt(scene,0.3)'` 场景检测；阈值 0.3 偏保守，画面变化不大的视频可能只识别 2-3 帧
- ⚠️ 脚本**没有** `--frame-method` 和 `--output-dir` 参数——SKILL.md 早期版本里写过但脚本 CLI 不支持。输出路径用 `--output` 指定

### 阶段 2：场景识别（需要 vision 模型）

对每个关键帧调用 vision 模型识别画面内容。脚本内置 `analyze_frames_with_minimax()` 函数用 MINIMAX M3 自动逐帧分析，但**实际推荐用 Hermes 的 `vision_analyze` 工具配合详细中文 prompt**，识别准确率高很多（见 Pitfall 18）。

**MINIMAX M3 调用要点（脚本内置函数使用）：**
- API 默认开启 thinking 模式，会只输出思考过程不返回结果
- **必须加 `"thinking": {"type": "disabled"}` 参数**，否则只返回空内容
- 图片需用 base64 编码后通过 `data:image/jpeg;base64,...` 传递
- 需要 SSL 绕过（`ssl.CERT_NONE`），MINIMAX 证书链可能不完整
- Prompt 要求只返回一个英文标签，不要解释
- 已知问题：内置 prompt 太简短，复杂城市/混合场景识别率低（CBD 被识别为 grass，公园被识别为 unknown）

**推荐替代：vision_analyze 工具 + 详细中文 prompt**

```python
# 对每个关键帧用 vision_analyze 看，prompt 要包含"什么地方/活动/天气/季节"等
vision_analyze(
    image_url=frame_path,
    question="用中文详细描述这张画面: 这是什么地方? 看到了什么具体内容(建筑/植物/人物/活动)? 光线如何? 时间(白天/夜晚)? 天气? 场景应分类为以下哪一类: 城市街道/城市公园/草地/森林/河流/海浪/雨/雪/人群/市场/其他。"
)
```

**核心逻辑：先识别画面内容 → 联想该场景下应有的声音 → 用具体关键词去站长之家搜索**

> ⚠️ **搜索关键词必须用中文**（如"风声"、"喘息"、"脚步"），不要用拼音。
> 脚本内置 `_KEYWORD_TO_PINYIN` 映射表自动转换 URL（站长之家只支持拼音 URL）。
> 向用户解释搜索意图时用中文，如"搜索关键词：风声、鸟叫声、欢呼声"。

| 标签 | 画面内容 | 联想声音 | 搜索关键词（中文） |
|------|---------|---------|-----------------|
| `mountain_summit` | 登上山顶，开阔山景 | 风声、鸟鸣、欢呼 | 风声、鸟叫声、欢呼声 |
| `mountain_climbing` | 登山过程中，登山者在山路上行走 | 喘息声、脚步声、登山杖 | 喘息、脚步、登山 |
| `mountain_view` | 山景远景，无人物 | 风声、鸟鸣、溪流 | 风声、鸟叫声、溪流 |
| `forest` | 森林、树林、树木茂密 | 虫鸣、鸟叫、风吹树叶 | 虫鸣、鸟叫声、风声、森林 |
| `grass` | 草地、草原、田野 | 风声、虫鸣、鸟叫 | 风声、虫鸣、鸟叫声 |
| `river` | 河流、溪流、溪水潺潺 | 流水声、溪水 | 流水、溪流、水声 |
| `waterfall` | 瀑布、飞流直下 | 瀑布声、水声 | 瀑布、流水、水声 |
| `ocean_wave` | 海浪、海边、大海 | 海浪声、海鸥、风声 | 海浪、海鸥、风声 |
| `rain` | 下雨、雨天、雨滴 | 雨声、下雨、雷雨 | 雨声、下雨、雷雨 |
| `birds` | 鸟类、鸟群飞过 | 鸟叫声、鸟鸣、叽叽喳喳 | 鸟叫声、小鸟、叽叽喳喳 |
| `insects` | 昆虫（蝉、蟋蟀、蜜蜂等） | 虫鸣、蝉鸣、蜜蜂 | 虫鸣、蝉鸣、蜜蜂 |
| `city_street` | 城市街道、马路、建筑 | 车流声、喇叭、城市环境 | 交通、车流、城市 |
| `city_park` | 城市公园/CBD 摩天楼下大草坪 | 城市环境、车流、风声、鸟叫声 | 城市、车流、鸟叫声、风声 |
| `riverfront` | 滨江/海边步道（城市河道边，有人骑车散步） | 水声、海鸥、风声、城市远景 | 水声、海鸥、风声、城市 |
| `crowd` | 人群聚集、庆祝 | 人群声、掌声、欢呼声 | 人群、掌声、欢呼 |
| `people_walking` | 人物走路、日常活动 | 脚步声、走路 | 脚步、走路 |
| `market` | 市场、集市、商场 | 叫卖声、嘈杂、市场 | 叫卖、嘈杂、人声 |
| `fire` | 火焰、篝火、燃烧 | 燃烧声、火焰、噼啪 | 燃烧、火焰、噼啪 |
| `snow` | 雪景、下雪、雪地 | 风声、踩雪、寂静 | 风声、踩雪 |
| `unknown` | 不确定/其他 | 自然音效 | 自然 |

> ⚠️ **场景标签会随 MINIMAX 返回变化**——如果 MINIMAX 返回了上表之外的标签，脚本内置 `_fuzzy_match_scene()` 会尝试模糊匹配到已知场景。如果匹配失败会标记为 `unknown`。

**vision 分析 prompt 模板：**

```
请仔细看这张图片，告诉我画面内容属于以下哪种场景类型（只返回最匹配的一个英文标签）：

场景选项：forest, rain, wind, waterfall, ocean_wave, river, thunder, fire, snow, birds, insects, dog, cat, city_street, market, office, crowd, traffic, explosion, car_chase, footsteps, unknown

只返回场景标签英文单词，不要多余解释。
```

**识别后输出 JSON 文件：**

将结果保存为 `scene_assignments.json`：
```json
{
  "scenes": [
    {"frame": 0, "scene": "forest", "description": "茂密绿色森林"},
    {"frame": 1, "scene": "waterfall", "description": "山间瀑布"},
    {"frame": 2, "scene": "city_street", "description": "繁忙城市街道"}
  ],
  "unique_scenes": ["forest", "waterfall", "city_street"]
}
```

### 阶段 3：下载匹配音效

**核心逻辑：画面场景 → 联想应有的声音 → 用具体关键词在站长之家搜索 → curl 下载**

脚本内置 `SCENE_TO_SFX_SEARCH` 映射表，每个场景标签对应：
- `description`：画面描述（供 agent 理解场景内容）
- `sounds`：该场景下应有的声音列表（中文，供 agent 理解搜索意图）
- `tags`：站长之家搜索关键词（英文/拼音，直接用于 URL 搜索）

**示例：登山视频**
- `mountain_climbing` → 联想"喘息声、脚步声、登山杖" → 搜索关键词：`喘息`, `脚步`, `登山`（URL 自动转拼音：tansheng, jiaosheng, dengshan）
- `mountain_summit` → 联想"风声、鸟鸣、欢呼" → 搜索关键词：`风声`, `鸟叫声`, `欢呼声`（URL 自动转拼音：fengsheng, niaojiaosheng, huanhuansheng）

下载音效（脚本自动完成）：
```bash
python3 {baseDir}/scripts/video_sfx_match.py <video_file> \
  --scenes-json <work_dir>/scene_assignments.json \
  --output <output_path>
```

**下载策略：**
- 每个场景最多下载 2 条音效（`--sfx-per-scene 2`）
- 脚本遍历搜索关键词，从每个关键词的搜索结果中取详情页
- 详情页中的 `.wav` 或 `.mp3` 链接都会被匹配
- 自动验证下载文件（ffprobe 检查时长 > 0.5 秒）
- 文件保存在输出目录的 `sfx/` 子目录

### 阶段 4：ffmpeg 合成

脚本自动将下载的音效与原始视频音频混合：

- 原始音频保持 100% 音量
- 音效以 `--sfx-volume` 控制音量（**脚本默认 1.0**，vlog 有原声建议 0.5-0.6，AI 无声视频可用 0.8-1.0）
- 使用 `amix` filter 混合所有音效轨道
- 视频流**重新编码为 H.264**（`libx264 -preset fast -crf 23`，**不能** `-c:v copy`，因为 audio 走 filter_complex）
- 输出 AAC（192kbps）
- ✅ **时长保持**（1.2.0+ 修复）：每条 SFX 先 `apad` 补齐到视频时长，再 `atrim=0:{video_dur}` 裁切；`amix=duration=longest` 保证混合后时长 = 最长输入 = 视频原长。实测 55.4s 输入 → 55.4s 输出
- ⚠️ 之前版本（≤1.1.0）用 `amix=duration=first` + `-shortest` 会**裁掉视频开头 1-2 秒**——已在 1.2.0 修复

```bash
# 合成完成后输出：
🎉 完成！输出: <output_path>
```

## 完整使用示例

### 示例 1：给家庭 vlog / 旅行 vlog 配音效

```
用户：帮我给这个视频加点环境音效（家庭 vlog，城市 CBD + 滨江步道场景）
```

**默认推荐用 interval 模式**（家庭/旅行 vlog 节奏慢，scene 模式会漏段）：

```bash
# Step 1: 提帧 + 识别场景（不下载不合成，先 review）
python3 {baseDir}/scripts/video_sfx_match.py <video_file> \
  --frame-method interval --frames-per-scene 4 --dry-run

# Step 2: review 场景识别结果（看 city_park / riverfront / grass 等标签是否合理）
# 如果识别不准，用 vision_analyze 工具 + 详细中文 prompt 重写 scene_assignments.json

# Step 3: 下载 + 合成
python3 {baseDir}/scripts/video_sfx_match.py <video_file> \
  --frame-method interval --frames-per-scene 4 \
  --scenes-json scene_assignments.json \
  --sfx-volume 0.4 --sfx-per-scene 2 \
  --output 输出路径.mp4
```

执行步骤：
1. **Step 1**: interval 模式均匀提帧，MINIMAX 自动识别场景 → `scene_analysis.json`
2. **Step 2**: review 识别结果，城市/CBD/室内场景识别率低时可手动用 `vision_analyze` 重写 → `scene_assignments.json`
3. **Step 3**: 跑完整流程（下载 SFX + 合成）

### 示例 2：给 AI 生成视频配音效

```
用户：这个 AI 生成的视频没声音，帮我加点音效
```

执行步骤：
1. 提取帧（AI 视频场景变化大，建议 `--frames-per-scene 8`）
2. vision 识别场景
3. 下载匹配音效（`--sfx-volume 0.8`，AI 视频没有原始音频，音效可以更响）
4. 合成

### 示例 3：手动指定场景（跳过 vision）

```bash
# 如果用户已经知道视频里有什么场景
echo '{"scenes": [{"scene": "forest"}, {"scene": "waterfall"}]}' > scenes.json

python3 {baseDir}/scripts/video_sfx_match.py input.mp4 \
  --scenes-json scenes.json \
  --sfx-volume 0.7 \
  --output output.mp4
```

## 参数速查

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--frames-per-scene` | 5 | 提取关键帧数 |
| `--frame-method` | scene | `scene`=场景检测 / `interval`=固定间隔（vlog 推荐） |
| `--sfx-per-scene` | 2 | 每个场景下载音效数 |
| `--sfx-volume` | **1.0** | 音效音量 (0.0-1.0)。有原声 vlog 用 0.4-0.6；AI 无声视频用 0.8-1.0 |
| `--dry-run` | false | 只提取帧不下载 |
| `--scenes-json` | — | 手动指定场景 JSON（跳过 MINIMAX 自动识别） |
| `--output` / `-o` | `视频目录/视频名_sfx.mp4` | 输出视频路径 |

> 脚本**没有** `--output-dir`、`--max-scenes`、`--max-frames` 参数。早期 SKILL.md 里写过但脚本 CLI 不支持，不要按错。

## Pitfalls

1. **ffmpeg 场景检测可能帧太少** — 如果 < 3 帧，脚本自动回退到间隔提取模式
2. **vision 识别不准** — 可以让用户手动指定场景（`--scenes-json`），或在识别后让用户确认/修正
3. **站长之家反爬** — 脚本已内置 UA + Referer，但如果 chinaz 更新反爬策略，需要更新 UA
4. **音效太吵/太轻** — 默认 1.0 音量；有原始音频的视频建议 0.6-0.8；AI 无声视频建议 1.0-1.5
5. **下载的音效时长不匹配** — 脚本不裁剪音效长度，用 `amix=duration=first` 对齐到视频时长
6. **分类映射可能遗漏** — 如果 chinaz 有新的标签页分类，更新 `SCENE_TO_SFX_SEARCH` 映射表
7. **源文件保护** — 输出总是写到 `视频名_video_sfx/` 新子目录，不覆盖原始视频（遵循林大哥的源文件保护规则）
8. **场景→声音→关键词映射精度** — MINIMAX 识别的场景标签决定搜索关键词，如果场景识别偏差（如把"登山中"识别为"山顶"），搜索到的音效就不匹配。**关键是先正确识别画面内容**，关键词只是搜索手段
9. **手动指定场景可能不符合实际画面** — 用户或 agent 手动指定的场景标签可能与视频实际画面不一致（如指定 sky 但实际是 mountain）。**应先分析关键帧再决定场景标签**，不要凭文件名/标题推断。如果用户要求手动指定场景，agent 仍应先提取关键帧并分析，再与用户确认是否匹配
10. **搜索结果取决于 chinaz 内容质量** — 站长之家某些关键词搜索结果可能不精准（如 `tansheng` 返回"忽忽音效"而非真正的喘息声）。如果搜索质量差，需要调整关键词或让用户手动指定
11. **输出文件在独立子目录** — 脚本自动创建 `视频名_video_sfx/` 目录存放所有输出（场景分析 JSON、合成视频、音效文件），音效文件在 `sfx/` 子目录
12. **搜索关键词必须用中文** — 向用户解释搜索意图时用中文（风声、鸟鸣、喘息、脚步），不要用拼音。脚本内部自动转拼音拼 URL（`_KEYWORD_TO_PINYIN` 映射表）。SKILL.md 中的场景表 `tags` 列也写中文
13. **场景识别是核心，关键词只是手段** — 整个 pipeline 的精度取决于 MINIMAX 对画面的识别是否正确。如果识别结果不对，后续搜索关键词都不对。**先确认画面内容，再联想声音**
14. **标签页搜索结果取决于 chinaz 内容质量** — 某些关键词的搜索结果可能不精准（如 `tansheng` 返回"忽忽音效"而非真正的喘息声）。如果搜索质量差，需要调整关键词或让用户手动指定
15. **音效文件复制到输出目录** — 脚本下载的音效文件（temp 目录）会自动复制到 `输出目录/sfx/` 子目录，方便用户查看和使用。工作目录（`/var/folders/...`）保留供调试，可手动清理
16. **源文件保护** — 原始视频文件绝不能重命名、移动、覆盖或删除。所有输出写到独立子目录（`视频名_video_sfx/`），与源文件物理隔离。这是林大哥的硬性规则
17. **`amix=duration=first` 裁掉视频开头 1-2 秒** — 实测 55.4s 输入 → 53.5s 输出。`duration=first` 会以首条输入流（视频）的时长为准，但首条前的 SFX padding 会被吞掉。**已在 1.2.0 修复**：用 `apad`+`atrim` 把每条 SFX 补齐到视频时长，`amix=duration=longest` 保留原长
18. **vision_analyze 配合详细中文 prompt 比脚本内置 MINIMAX 一次性 one-liner 准很多** — 脚本内置的 prompt 只要求"返回一个英文标签"，对城市/室内/混合场景识别率低（实测把 CBD 摩天楼+大草坪识别成 `grass`、把儿童玩耍的城市公园识别成 `unknown`）。推荐流程：先用 `vision_analyze` 配合详细中文 prompt（"什么地方/建筑/人物/活动/天气/分类"）逐帧分析，再手动写 `scene_assignments.json` 喂给 `--scenes-json`
19. **`--scenes-json` 的 label 必须在 `SCENE_TO_SFX_SEARCH` 里，否则静默失败** — 手动指定新场景类型（如 `city_park`）时，必须**同时**改脚本里的 `SCENE_TO_SFX_SEARCH`（加新场景的 description/sounds/tags）和 `_KEYWORD_TO_PINYIN`（加新中文关键词的拼音 URL）。改完后 `--help` 不会显示新场景，得跑一次 dry-run 验证映射生效
20. **chinaz 部分关键词搜索质量差** — 实测以下关键词返回结果不符合预期：
    - `海鸥` / `海边` → 经常返回海浪/海风，没有真正海鸥叫声
    - `河边` / `滨江` → 经常返回山泉/瀑布/山野水声，没有城市河道
    - `风声` → 前几条里可能出现"阴冷怪异的风""恐怖风声"等诡异风格，**不适合春节家庭温馨 vlog**
    - `车流` → 经常搜不到结果（chinaz 该分类素材少），可改用 `城市`/`交通`
21. **场景检测模式可能漏掉静止段** — 实测 55s 家庭 vlog 用 scene 模式只识别 3 帧（开/中/末），中间 30s 静止画面就**没配音效**。vlog 改用 `--frame-method interval` + `--frames-per-scene 4`（=12 帧）可完整覆盖

## 核心原则：先识别画面，再联想声音

这不是一个"按场景名搜固定分类"的工具。它的核心逻辑是：

1. **看画面** — 用 MINIMAX 视觉模型识别画面里有什么（登山者？森林？海浪？）
2. **想声音** — 这个画面在现实中应该有什么声音（登山→喘息+脚步、山顶→风声+鸟鸣）
3. **搜关键词** — 用具体声音名去站长之家搜索（中文关键词，自动转拼音 URL）
4. **下载合成** — curl 下载 + ffmpeg 混合

**这意味着场景标签不是固定的分类，而是视觉识别的结果。** MINIMAX 返回什么标签，就决定搜什么关键词。如果 MINIMAX 返回了意外的标签，`\_fuzzy\_match\_scene()` 会尝试匹配到已知场景，匹配失败则标记 `unknown`。

## 已知技术坑（开发时踩过的）

> 详细 CDN 踩坑记录见 `references/chinaz_cdn_notes.md`

### Python urllib 访问 chinaz CDN 极慢（已修复）
`downsc.chinaz.net` 从 Python `urllib` 访问经常超时（SSL 连接建立慢），而 `curl` 秒开（0.6s vs 60s+）。
**脚本已改为 subprocess 调 curl 做所有 HTTP 请求**（`_fetch_url` 和 `download_mp3`）。
如果未来改回 Python 方式，务必加 `ssl.CERT_NONE` 且仍然可能超时——**坚持用 curl**。

### ffmpeg `-c:v copy` 不能和 `-filter_complex` 共用
当使用 `-filter_complex` 处理音频时，视频流不能直接用 `-c:v copy`（会报 "Streamcopy requested for output stream fed from a complex filtergraph"）。
脚本已改为 `-c:v libx264 -preset fast -crf 23` 重新编码视频。代价是合成稍慢，但兼容性最好。

### 无音频轨道的视频
如果输入视频没有音频（常见于 AI 生成视频、纯画面视频），`[0:a]` 在 filter 中不存在会报错。
脚本已内置 `has_audio()` 检测：无音频时走单独的 filter 路径（只混合 SFX，不做原始音频+音效的混合）。

### chinaz CDN 超时
`downsc.chinaz.net` 的某些服务器（尤其 sound1）从部分网络访问极慢甚至超时。
缓解措施：下载超时设为 60 秒；`find_and_download_sfx` 遍历多个详情页，某个超时会跳过继续下一个。
如果所有候选都超时，该场景会被跳过，不会阻塞整个 pipeline。

### 搜索逻辑已从标签页改为关键词搜索（重要）
旧版脚本用"场景→标签页"映射（如 mountain → `ziran.html`），但 `ziran.html` 是综合分类，搜索结果不精准。
新版改为"画面→联想声音→搜索关键词"（如 mountain_climbing → 喘息、脚步、登山），直接用 `search_chinaz_by_keyword()` 搜索站长之家。

**关键词必须用中文**（风声、鸟鸣、喘息、脚步），不要用拼音。脚本内置 `_KEYWORD_TO_PINYIN` 映射表自动转换 URL（站长之家只支持拼音 URL）。
向用户解释搜索意图时用中文，如"搜索关键词：风声、鸟叫声、欢呼声"。

如果某个关键词搜索结果差，可以换同义词或相关词尝试。

### 输出目录结构（新版）
脚本自动在视频同目录下创建 `视频名_video_sfx/` 子目录：
```
视频名_video_sfx/
├── 视频名_sfx.mp4     ← 合成后的视频
├── scene_analysis.json ← 场景分析结果
└── sfx/                ← 下载的音效文件
    ├── 树林的风MP3音效下载.mp3
    ├── 阴冷怪异的风音效素材.mp3
    └── ...
```

### MINIMAX M3 必须关闭 thinking 模式
MINIMAX M3 API 默认开启 thinking 模式，调用时模型只输出思考过程（`<think>...</think>`），不返回最终答案。
**必须加 `"thinking": {"type": "disabled"}` 参数**，否则场景识别会得到空结果。

### chinaz 详情页编码：GBK 不是 UTF-8
chinaz 详情页内容是 GBK 编码，不是 UTF-8。`subprocess.run(cmd, text=True)` 用 locale 编码（UTF-8）解码会报错。
**正确做法**：`capture_output=True`（不加 `text=True`），手动 `decode("utf-8")` 并 fallback 到 `decode("gbk", errors="replace")`。

### curl 必须加 `--compressed`
chinaz 服务器返回 gzip 压缩内容。curl 不加 `--compressed` 会返回压缩二进制数据，Python 无法解析。
脚本已内置 `--compressed` 参数，**不要移除**。

## 多模态模型选择（重要）

需要 vision 或语音时，**优先从已配置 API 找支持该模态的模型**，不要用外部 vision API（如 `vision_analyze` 依赖的独立 API key 经常失效）：

- **MINIMAX M3** — 原生多模态（视觉+文本），可用于关键帧场景识别
- **MINIMAX Speech 2.8** — 语音合成
- **MINIMAX Music 2.6** — 音乐生成

只有自有模型都不支持时，才考虑外部 API。

## 依赖

- `ffmpeg` + `ffprobe`（brew install ffmpeg / apt install ffmpeg）
- `curl`（**必须**，脚本用 subprocess 调 curl 下载，比 Python urllib 快 10-100x）
- `python3`
- vision 模型（优先 MINIMAX M3，备选 Hermes vision_analyze）

## 关联技能

- `chinese-stock-media-download` — 站长之家素材下载（本 skill 依赖它的工作流）
- `vlog-auto-edit` — Vlog 全流程剪辑（本 skill 可作为其音效阶段）
- `scripted-short-film-editing` — 短片剪辑（同理）

---
**License:** MIT
