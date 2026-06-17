#!/usr/bin/env python3
"""
video_scene_sfx.py — 视频画面场景识别 + 站长之家匹配音效下载 + ffmpeg 合成

Pipeline:
  1. ffmpeg 从视频提取关键帧（按场景变化或固定间隔）
  2. 调用 MINIMAX 视觉模型逐帧识别画面内容
  3. 根据画面内容联想应有的声音 → 用具体关键词在站长之家搜索 → curl 下载
  4. ffmpeg 将音效轨道合成到视频

核心逻辑：先识别画面内容 → 再联想该场景下应该有什么声音 → 用关键词搜索
  登山过程中(登山者+山景) → 喘息声 + 脚步声 + 登山杖触地声
  登上山顶(开阔山景) → 风声 + 鸟鸣声
  森林画面 → 虫鸣 + 鸟叫 + 风吹树叶
  海边 → 海浪 + 海鸥 + 风声

Usage:
  python3 video_scene_sfx.py <video_file> --output <output.mp4>

Dependencies: ffmpeg, ffprobe, curl, python3
"""

import argparse, json, os, re, subprocess, sys, tempfile
from pathlib import Path

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
SITE = "sc.chinaz.com"
BASE = f"https://{SITE}"

# ─── 画面场景 → 联想声音 → 站长之家搜索关键词 ───────────────────
# 核心逻辑：先识别画面内容，再联想该场景下应有的声音
SCENE_TO_SFX_SEARCH = {
    # ── 登山/户外 ──
    "mountain_climbing": {
        "description": "登山过程中，登山者在山路上行走",
        "sounds": ["喘息声", "脚步声", "登山杖", "爬山"],
        "tags": ["喘息", "脚步", "登山"],
    },
    "mountain_summit": {
        "description": "登上山顶，开阔山景",
        "sounds": ["风声", "鸟鸣", "欢呼", "山顶"],
        "tags": ["风声", "鸟叫声", "欢呼声"],
    },
    "mountain_view": {
        "description": "山景远景，无人物",
        "sounds": ["风声", "鸟鸣", "溪流"],
        "tags": ["风声", "鸟叫声", "溪流"],
    },

    # ── 自然/森林 ──
    "forest": {
        "description": "森林、树林",
        "sounds": ["虫鸣", "鸟叫", "风吹树叶", "森林"],
        "tags": ["虫鸣", "鸟叫声", "风声", "森林"],
    },
    "grass": {
        "description": "草地、草原",
        "sounds": ["风声", "虫鸣", "鸟叫"],
        "tags": ["风声", "虫鸣", "鸟叫声"],
    },
    "river": {
        "description": "河流、溪流",
        "sounds": ["流水声", "溪水", "水声"],
        "tags": ["流水", "溪流", "水声"],
    },
    "waterfall": {
        "description": "瀑布",
        "sounds": ["瀑布声", "水声", "流水"],
        "tags": ["瀑布", "流水", "水声"],
    },
    "ocean_wave": {
        "description": "海浪、海边",
        "sounds": ["海浪声", "海鸥", "风声"],
        "tags": ["海浪", "海鸥", "风声"],
    },
    "rain": {
        "description": "下雨",
        "sounds": ["雨声", "下雨", "雷雨"],
        "tags": ["雨声", "下雨", "雷雨"],
    },

    # ── 动物 ──
    "birds": {
        "description": "鸟类、鸟群",
        "sounds": ["鸟叫声", "鸟鸣", "叽叽喳喳"],
        "tags": ["鸟叫声", "小鸟", "叽叽喳喳"],
    },
    "insects": {
        "description": "昆虫（蝉、蟋蟀、蜜蜂等）",
        "sounds": ["虫鸣", "蝉鸣", "蜜蜂", "蟋蟀"],
        "tags": ["虫鸣", "蝉鸣", "蜜蜂"],
    },

    # ── 城市/人类活动 ──
    "city_street": {
        "description": "城市街道、交通",
        "sounds": ["车流声", "喇叭", "城市环境"],
        "tags": ["交通", "车流", "城市"],
    },
    "city_park": {
        "description": "城市公园/CBD 摩天楼下大草坪",
        "sounds": ["城市环境", "车流", "风声", "鸟叫声", "孩子"],
        "tags": ["城市", "车流", "鸟叫声", "风声"],
    },
    "riverfront": {
        "description": "滨江/海边步道(城市河道边,有人骑车散步)",
        "sounds": ["水声", "海鸥", "风声", "城市远景"],
        "tags": ["水声", "海鸥", "风声", "城市"],
    },
    "crowd": {
        "description": "人群聚集、庆祝",
        "sounds": ["人群声", "掌声", "欢呼声", "嘈杂"],
        "tags": ["人群", "掌声", "欢呼"],
    },
    "people_walking": {
        "description": "人物走路、日常活动",
        "sounds": ["脚步声", "走路"],
        "tags": ["脚步", "走路"],
    },
    "market": {
        "description": "市场、集市",
        "sounds": ["叫卖声", "嘈杂", "市场"],
        "tags": ["叫卖", "嘈杂", "人声"],
    },

    # ── 室内/特效 ──
    "fire": {
        "description": "火焰、篝火",
        "sounds": ["燃烧声", "火焰", "噼啪"],
        "tags": ["燃烧", "火焰", "噼啪"],
    },
    "snow": {
        "description": "雪景",
        "sounds": ["风声", "踩雪", "寂静"],
        "tags": ["风声", "踩雪"],
    },
}

# 中文 → 拼音映射（站长之家标签页只支持拼音 URL）
_KEYWORD_TO_PINYIN = {
    "风声": "fengsheng", "鸟叫声": "niaojiaosheng", "欢呼声": "huanhuansheng",
    "溪流": "xiliu", "喘息": "tansheng", "脚步": "jiaosheng", "登山": "dengshan",
    "虫鸣": "chongming", "森林": "senlin", "流水": "liushui", "水声": "shuisheng",
    "瀑布": "pubu", "海浪": "hailang", "海鸥": "haiou", "雨声": "yusheng",
    "下雨": "xiayu", "雷雨": "leiyu", "蝉鸣": "chaiming", "蜜蜂": "mifeng",
    "交通": "jiaotong", "车流": "cheliliang", "城市": "chengshi",
    "人群": "renqun", "掌声": "zhangsheng", "欢呼": "huansheng",
    "走路": "zoulu", "叫卖": "jiaoshi", "嘈杂": "caoza", "人声": "rensheng",
    "燃烧": "ranshao", "火焰": "huoyan", "噼啪": "pipa", "踩雪": "caixue",
    "小鸟": "xiaoniao", "叽叽喳喳": "jijizhazha",
    "海边": "haibian", "河边": "hebian", "滨江": "binjiang",
    "广场": "guangchang", "公园": "gongyuan", "海风": "haifeng",
    "水鸟": "shuiniao", "河水": "heshui",
}

# 标签页缓存：记录已抓取的标签页 HTML，避免重复请求
_tag_page_cache = {}


def run(cmd, **kwargs):
    """Run a command and return stdout as string."""
    if isinstance(cmd, str):
        cmd = cmd, shell=True
    r = subprocess.run(cmd, capture_output=True, **kwargs)
    if r.returncode != 0:
        print(f"  ⚠ cmd failed: {cmd}", file=sys.stderr)
        try:
            err = r.stderr[:300].decode("utf-8", errors="replace")
        except:
            err = str(r.stderr[:300])
        print(f"    stderr: {err}", file=sys.stderr)
    try:
        return r.stdout.decode("utf-8")
    except UnicodeDecodeError:
        return r.stdout.decode("gbk", errors="replace")


def video_duration(video_path):
    out = run(["ffprobe", "-v", "quiet", "-show_entries", "format=duration", "-of", "csv=p=0", video_path])
    try:
        return float(out.strip())
    except ValueError:
        return None


def video_fps(video_path):
    out = run(["ffprobe", "-v", "quiet", "-select_streams", "v:0",
               "-show_entries", "stream=r_frame_rate", "-of", "csv=p=0", video_path])
    try:
        num, den = out.strip().split("/")
        return int(num) / int(den)
    except (ValueError, ZeroDivisionError):
        return 30.0


def extract_frames(video_path, output_dir, num_frames=10, method="scene"):
    duration = video_duration(video_path)
    if not duration:
        print("  ⚠ 无法获取视频时长，用默认 30 秒", file=sys.stderr)
        duration = 30.0

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    frames = []

    if method == "scene":
        print(f"  [ffmpeg] 场景检测提取关键帧 (阈值 0.3)...")
        run(["ffmpeg", "-i", video_path,
             "-vf", "select='gt(scene,0.3)',scale=960:-1",
             "-vsync", "vfr", "-frames:v", str(num_frames),
             "-q:v", "2",
             str(output_dir / "frame_%04d.jpg"), "-y"])

        extracted = sorted(output_dir.glob("frame_*.jpg"))
        if len(extracted) < 3:
            print(f"  ⚠ 场景检测只提取了 {len(extracted)} 帧，补充间隔提取...")
            interval = duration / (num_frames + 1)
            for i in range(num_frames):
                t = interval * (i + 1)
                out_file = output_dir / f"frame_{i+1:04d}.jpg"
                run(["ffmpeg", "-ss", str(t), "-i", video_path,
                     "-frames:v", "1", "-q:v", "2", "-vf", "scale=960:-1",
                     str(out_file), "-y"])
                if out_file.exists() and out_file.stat().st_size > 100:
                    frames.append(str(out_file))
        else:
            frames = [str(f) for f in extracted]
    else:
        interval = duration / (num_frames + 1)
        for i in range(num_frames):
            t = interval * (i + 1)
            out_file = output_dir / f"frame_{i+1:04d}.jpg"
            run(["ffmpeg", "-ss", str(t), "-i", video_path,
                 "-frames:v", "1", "-q:v", "2", "-vf", "scale=960:-1",
                 str(out_file), "-y"])
            if out_file.exists() and out_file.stat().st_size > 100:
                frames.append(str(out_file))

    print(f"  ✅ 提取了 {len(frames)} 个关键帧")
    return frames[:num_frames]


def analyze_frames_with_minimax(frame_paths, api_key, max_concurrent=3):
    """Use MINIMAX-M3 vision model to analyze frame content. Returns list of scene labels."""
    import base64, urllib.request, ssl

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    scene_options = list(SCENE_TO_SFX_SEARCH.keys())

    results = []
    for i, fpath in enumerate(frame_paths):
        with open(fpath, "rb") as f:
            img_b64 = base64.b64encode(f.read()).decode()

        payload = {
            "model": "MiniMax-M3",
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": f"这张图片的画面内容是什么场景？只返回一个英文标签，不要解释。选项：{', '.join(scene_options)}"},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}}
                ]
            }],
            "max_tokens": 30,
            "thinking": {"type": "disabled"}
        }

        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            "https://api.minimaxi.com/v1/chat/completions",
            data=data,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=30, context=ctx) as resp:
                result = json.loads(resp.read())
                label = result["choices"][0]["message"]["content"].strip().lower()
                # Validate label is in our options
                if label not in SCENE_TO_SFX_SEARCH:
                    # Try fuzzy match
                    label = _fuzzy_match_scene(label, scene_options)
                results.append(label)
                scene_info = SCENE_TO_SFX_SEARCH.get(label, {})
                desc = scene_info.get("description", label)
                print(f"  [{i+1:2d}] {os.path.basename(fpath)}: {label} ({desc})")
        except Exception as e:
            print(f"  [{i+1:2d}] ERROR: {e}")
            results.append("unknown")

    return results


def _fuzzy_match_scene(label, options):
    """Try to fuzzy match a label to known scene types."""
    label = label.lower().replace(" ", "_").replace("-", "_")
    # Exact match first
    if label in options:
        return label
    # Partial match
    for opt in options:
        if opt in label or label in opt:
            return opt
    # Keyword matching
    keywords = {
        "mountain": ["mountain", "hill", "climbing", "hike", "peak", "summit"],
        "forest": ["forest", "tree", "woods", "jungle"],
        "river": ["river", "stream", "creek", "water"],
        "ocean_wave": ["ocean", "sea", "wave", "beach", "coast"],
        "rain": ["rain", "rainy", "drizzle", "storm"],
        "birds": ["bird", "birds", "flying"],
        "city_street": ["city", "street", "urban", "traffic", "road"],
        "crowd": ["crowd", "people", "crowded", "festival", "celebration"],
        "fire": ["fire", "flame", "burning", "bonfire"],
        "snow": ["snow", "winter", "ice"],
        "grass": ["grass", "meadow", "field", "prairie"],
    }
    for scene_key, kws in keywords.items():
        for kw in kws:
            if kw in label:
                return scene_key
    return "unknown"


def _fetch_url(url, timeout=20):
    """Fetch URL via curl."""
    try:
        out = run(["curl", "-sL", "--compressed",
                   "--connect-timeout", str(min(timeout, 10)),
                   "--max-time", str(timeout),
                   "-A", UA, "-H", f"Referer: {BASE}/", url])
        return out if out else None
    except Exception as e:
        print(f"  ⚠ 抓取 {url} 失败: {e}", file=sys.stderr)
        return None


def search_chinaz_by_keyword(keyword, max_results=10):
    """Search chinaz for a keyword and return detail page URLs."""
    # Convert Chinese keyword to pinyin for URL (chinaz only supports pinyin URLs)
    pinyin = _KEYWORD_TO_PINYIN.get(keyword, keyword)
    tag_page = f"/tag_yinxiao/{pinyin}.html"
    url = f"{BASE}{tag_page}"

    html = _fetch_url(url)
    if not html:
        return []

    # Extract detail page URLs
    urls = re.findall(r'href="(https?://sc\.chinaz\.com/yinxiao/\d+\.htm[l]?)"', html)
    return sorted(set(urls))[:max_results]


def fetch_detail_page(url):
    """Fetch a detail page and return (title, audio_url)."""
    html = _fetch_url(url)
    if html is None:
        return None, None

    # Extract title
    m = re.search(r'<h1[^>]*>(.*?)</h1>', html, re.DOTALL)
    if m:
        title = re.sub(r'<[^>]+>', '', m.group(1)).strip()
    else:
        m = re.search(r'<title>(.*?)</title>', html)
        title = m.group(1).strip() if m else "untitled"

    # Extract audio link (.wav or .mp3)
    audio_rel = re.search(r'href="((?:https?:)?//downsc\.chinaz\.net/[^\"]*\.(?:mp3|wav))"', html)
    if audio_rel:
        audio_url = audio_rel.group(1)
        if audio_url.startswith("//"):
            audio_url = f"https:{audio_url}"
        return title, audio_url

    return title, None


def download_audio(audio_url, output_path):
    """Download an audio file via curl."""
    try:
        run(["curl", "-sL", "--connect-timeout", "15", "--max-time", "60",
             "-A", UA, "-H", f"Referer: {BASE}/",
             "-o", output_path, audio_url])
        size = os.path.getsize(output_path) if os.path.exists(output_path) else 0
        if size > 1000:
            return size
        if os.path.exists(output_path):
            os.remove(output_path)
        return 0
    except Exception as e:
        print(f"  ⚠ 下载 {audio_url} 失败: {e}", file=sys.stderr)
        return 0


def find_and_download_sfx(scene_label, output_dir, max_downloads=2):
    """
    Given a scene label, infer appropriate sounds, search chinaz, and download.
    Core logic: scene → imagine sounds → search by keyword → download
    """
    scene_info = SCENE_TO_SFX_SEARCH.get(scene_label)
    if not scene_info:
        print(f"  ⚠ 未知场景: {scene_label}，跳过")
        return []

    sounds = scene_info["sounds"]
    tags = scene_info["tags"]
    desc = scene_info["description"]

    print(f"  🖼 场景: {scene_label}（{desc}）")
    print(f"  🔊 联想声音: {', '.join(sounds)}")
    print(f"  🔍 搜索关键词: {', '.join(tags)}")

    results = []
    downloaded_urls = set()  # deduplicate by URL

    for tag in tags:
        if len(results) >= max_downloads:
            break

        print(f"  [chinaz] 搜索: {tag}")
        detail_urls = search_chinaz_by_keyword(tag, max_results=10)

        if not detail_urls:
            print(f"  ⚠ 关键词 '{tag}' 未找到结果")
            continue

        for url in detail_urls:
            if len(results) >= max_downloads:
                break

            title, audio_url = fetch_detail_page(url)
            if not audio_url or audio_url in downloaded_urls:
                continue

            safe_title = re.sub(r'[/\\:*?"<>|]', '_', title or "untitled")
            out_path = os.path.join(output_dir, f"{safe_title}.mp3")

            size = download_audio(audio_url, out_path)
            if size > 1000:
                downloaded_urls.add(audio_url)
                results.append((title, out_path))
                print(f"  ✅ 下载: {title} ({size//1024}KB)")
            else:
                if os.path.exists(out_path):
                    os.remove(out_path)

    return results


def has_audio(video_path):
    out = run(["ffprobe", "-v", "quiet", "-select_streams", "a:0",
               "-show_entries", "stream=codec_type", "-of", "csv=p=0", video_path])
    return "audio" in out.strip().lower()


def composite_video(video_path, sfx_tracks, output_path, sfx_volume=0.6):
    if not sfx_tracks:
        print("  ⚠ 没有音效可以合成", file=sys.stderr)
        return False

    num_sfx = len(sfx_tracks)
    video_has_audio = has_audio(video_path)
    video_dur = video_duration(video_path) or 60.0

    inputs = ["-i", video_path]
    for i, (title, path) in enumerate(sfx_tracks):
        inputs.extend(["-i", path])

    # 对每条 SFX:apad 补齐到视频时长,atrim 裁到视频时长,确保不裁短原视频
    sfx_inputs = "".join(
        f"[{i+1}:a]aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo,"
        f"apad,atrim=0:{video_dur}[s{i}];"
        for i in range(num_sfx)
    )
    sfx_mix_inputs = "".join(f"[s{i}]" for i in range(num_sfx))
    sfx_mix = f"{sfx_mix_inputs}amix=inputs={num_sfx}:duration=longest:dropout_transition=0," \
              f"volume={sfx_volume}[sfx]"

    if video_has_audio:
        filter_str = (
            f"[0:a]aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo[orig];"
            f"{sfx_inputs}{sfx_mix};"
            f"[orig][sfx]amix=inputs=2:duration=longest:dropout_transition=0[audio_out]"
        )
    else:
        filter_str = (
            f"{sfx_inputs}{sfx_mix};"
            f"[sfx]anull[audio_out]"
        )

    cmd = ["ffmpeg", "-y", *inputs,
           "-filter_complex", filter_str,
           "-map", "0:v", "-map", "[audio_out]",
           "-c:v", "libx264", "-preset", "fast", "-crf", "23",
           "-c:a", "aac", "-b:a", "192k",
           output_path]

    print(f"  [ffmpeg] 合成视频 + {num_sfx} 条音效 (目标时长 {video_dur:.1f}s)...")
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        err = result.stderr[-500:].decode("utf-8", errors="replace")
        print(f"  ❌ ffmpeg 合成失败: {err}", file=sys.stderr)
        return False

    size = os.path.getsize(output_path)
    print(f"  ✅ 输出: {output_path} ({size // 1024}KB)")
    return True


def main():
    parser = argparse.ArgumentParser(description="视频场景识别 + 匹配音效合成")
    parser.add_argument("video", help="输入视频文件路径")
    parser.add_argument("--output", "-o", help="输出视频路径（默认: 视频目录/视频名_sfx.mp4）")
    parser.add_argument("--frames-per-scene", type=int, default=5, help="关键帧数（默认 5）")
    parser.add_argument("--sfx-per-scene", type=int, default=2, help="每个场景下载的音效数（默认 2）")
    parser.add_argument("--sfx-volume", type=float, default=1.0, help="音效音量（默认 1.0）")
    parser.add_argument("--dry-run", action="store_true", help="只提取帧并识别场景，不下载/合成")
    parser.add_argument("--scenes-json", help="手动指定场景 JSON（跳过 MINIMAX 分析）")
    parser.add_argument("--frame-method", choices=["scene", "interval"], default="scene",
                        help="帧提取方式: scene=场景检测(可能漏掉静止段) / interval=固定间隔(均匀覆盖整段视频)")

    args = parser.parse_args()

    video_path = os.path.abspath(args.video)
    if not os.path.exists(video_path):
        print(f"❌ 文件不存在: {video_path}")
        sys.exit(1)

    # Output: always in a sub-directory next to the source video
    video_dir = os.path.dirname(video_path)
    video_stem = Path(video_path).stem
    output_dir = os.path.join(video_dir, f"{video_stem}_video_sfx")
    os.makedirs(output_dir, exist_ok=True)

    if args.output:
        output_path = os.path.abspath(args.output)
    else:
        output_path = os.path.join(output_dir, f"{video_stem}_sfx.mp4")

    # Working temp dir
    work_dir = tempfile.mkdtemp(prefix="video_sfx_")
    frames_dir = os.path.join(work_dir, "frames")
    sfx_dir = os.path.join(work_dir, "sfx")
    os.makedirs(sfx_dir, exist_ok=True)

    print(f"📹 输入视频: {video_path}")
    print(f"📂 输出目录: {output_dir}")
    print(f"📂 工作目录: {work_dir}")

    # Step 1: Extract frames
    print(f"\n═══ Step 1: 提取关键帧 ═══")
    frames = extract_frames(video_path, frames_dir, num_frames=args.frames_per_scene * 3, method=args.frame_method)
    if not frames:
        print("❌ 未能提取任何帧")
        sys.exit(1)

    # Step 2: Identify scenes
    print(f"\n═══ Step 2: 场景识别 ═══")
    if args.scenes_json:
        with open(args.scenes_json) as f:
            scene_data = json.load(f)
        scenes = scene_data.get("scenes", [])
        print(f"  📋 手动指定 {len(scenes)} 个场景")
    else:
        # Use MINIMAX vision model
        api_key = ""
        env_path = os.path.expanduser("~/.hermes/.env")
        with open(env_path) as f:
            for line in f:
                if "MINIMAX_API_KEY" in line and not line.strip().startswith("#"):
                    api_key = line.strip().split("=", 1)[1].strip()
                    break

        if not api_key:
            print("❌ 未找到 MINIMAX_API_KEY（在 ~/.hermes/.env 中）")
            sys.exit(1)

        print(f"  🔍 用 MINIMAX-M3 分析 {len(frames)} 帧...")
        scene_labels = analyze_frames_with_minimax(frames, api_key)

        # Build scenes list with unique labels
        scenes = []
        for label in scene_labels:
            if not scenes or scenes[-1].get("scene") != label:
                scenes.append({"frame": len(scenes), "scene": label, "description": SCENE_TO_SFX_SEARCH.get(label, {}).get("description", label)})

        # Save scene analysis
        scene_path = os.path.join(output_dir, "scene_analysis.json")
        with open(scene_path, "w") as f:
            json.dump({"scenes": scenes, "frames": [os.path.basename(f) for f in frames]},
                      f, ensure_ascii=False, indent=2)
        print(f"  📋 场景分析已保存: {scene_path}")

    print(f"  📋 {len(scenes)} 个场景已识别:")
    for s in scenes:
        desc = SCENE_TO_SFX_SEARCH.get(s.get("scene", ""), {}).get("description", "")
        print(f"    - [{s.get('frame', '?')}] {s.get('scene', '?')}: {desc}")

    if args.dry_run:
        print("\n  (dry-run 模式，停止)")
        return

    # Step 3: Download matching SFX
    print(f"\n═══ Step 3: 下载匹配音效 ═══")
    unique_scenes = list(dict.fromkeys(s.get("scene", "unknown") for s in scenes))
    all_sfx = []
    for scene_type in unique_scenes:
        print(f"\n  --- 场景: {scene_type} ---")
        sfx_list = find_and_download_sfx(scene_type, sfx_dir, max_downloads=args.sfx_per_scene)
        all_sfx.extend(sfx_list)

    if not all_sfx:
        print("\n⚠ 未找到任何匹配音效")
        sys.exit(1)

    print(f"\n  📦 共下载 {len(all_sfx)} 条音效")

    # Copy SFX files to output directory for user access
    import shutil
    sfx_output_dir = os.path.join(output_dir, "sfx")
    os.makedirs(sfx_output_dir, exist_ok=True)
    for title, sfx_path in all_sfx:
        if os.path.exists(sfx_path):
            dest = os.path.join(sfx_output_dir, os.path.basename(sfx_path))
            shutil.copy2(sfx_path, dest)
    print(f"  📁 音效文件已复制到: {sfx_output_dir}")

    # Step 4: Composite
    print(f"\n═══ Step 4: 合成视频 ═══")
    success = composite_video(video_path, all_sfx, output_path, sfx_volume=args.sfx_volume)

    if success:
        print(f"\n🎉 完成！输出: {output_path}")
        print(f"   音效文件在: {sfx_dir}")
    else:
        print(f"\n❌ 合成失败")

    print(f"\n  🧹 工作目录保留在: {work_dir}")
    print(f"     可用 `rm -rf {work_dir}` 清理")


if __name__ == "__main__":
    main()
