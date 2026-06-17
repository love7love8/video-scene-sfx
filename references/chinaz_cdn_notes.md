# 站长之家 CDN 踩坑记录

## Python urllib vs curl 性能对比

测试环境：Mac/Home 网络，2026-06-17

| 操作 | Python urllib | curl |
|------|--------------|------|
| 标签页 HTML | 正常 | 0.5s |
| 详情页 HTML | 正常 | 0.3s |
| MP3 下载 (923KB) | 60s+ (超时) | 0.6s |

**结论**：`downsc.chinaz.net` 对 Python urllib 的 SSL 连接极慢，curl 秒开。
原因推测：CDN 节点对 Python 默认 SSL 握手策略不友好（可能涉及 TLS fingerprinting）。

**脚本已修复**：所有 HTTP 请求改用 subprocess 调 curl。

## chinaz 反爬要点

- 必须带 `Referer: https://sc.chinaz.com/`
- UA 用浏览器 UA（Chrome 120+）
- cookie 不需要额外处理（curl 会自动跟随 redirect 并保存 cookie）
- SSL 验证可跳过（`-k`），但 curl 默认验证也正常

## 详情页音频链接提取

详情页 HTML 中音频直链格式（**注意是 .wav 不是 .mp3**）：

```
# 相对路径（常见）
href="//downsc.chinaz.net/Files/upload/yinxiao/2023/09/04/13818213.wav"

# 绝对路径（也出现）
href="https://downsc.chinaz.net/Files/upload/yinxiao/2023/09/04/13818213.wav"
```

注意：
- 文件后缀主要是 `.wav`，但内容是音频文件，ffmpeg 可直接处理
- 详情页没有直接的 `<a href="...mp3">` 链接，下载链接通过 JS 动态渲染在 `<source>` 和 `<a>` 标签里
- 标签页的详情 URL 有 `.htm` 和 `.html` 两种后缀

## subprocess.run 编码陷阱

Python 3.14 的 `subprocess.run(cmd, capture_output=True, text=True)` 用 locale 编码解码 stdout。
chinaz 详情页内容是 GBK 编码，UTF-8 解码会报 `UnicodeDecodeError: 'utf-8' codec can't decode byte 0xb4`。

**正确做法**：不用 `text=True`，用 `capture_output=True` 获取 bytes，然后手动解码：
```python
try:
    return r.stdout.decode("utf-8")
except UnicodeDecodeError:
    return r.stdout.decode("gbk", errors="replace")
```

**curl 必须加 `--compressed`**：chinaz 服务器返回 gzip 压缩内容，不加此参数 curl 返回压缩二进制数据。

## MINIMAX M3 调用要点

MINIMAX M3 API 默认开启 thinking 模式，调用时模型只输出思考过程（`<think>...</think>`），不返回最终答案。
**必须加 `"thinking": {"type": "disabled"}` 参数**，否则场景识别会得到空结果。

图片需用 base64 编码后通过 `data:image/jpeg;base64,...` 传递。
需要 SSL 绕过（`ssl.CERT_NONE`），MINIMAX 证书链可能不完整。

## 站长之家标签页 URL 只支持拼音不支持中文

直接访问中文编码的 URL（如 `/tag_yinxiao/风声.html`）会返回 404。
**必须用拼音 URL**（如 `/tag_yinxiao/fengsheng.html`）。

脚本内置 `_KEYWORD_TO_PINYIN` 映射表做转换：
```python
_KEYWORD_TO_PINYIN = {
    "风声": "fengsheng", "鸟叫声": "niaojiaosheng", "欢呼声": "huanhuansheng",
    "喘息": "tansheng", "脚步": "jiaosheng", "登山": "dengshan",
    # ... 完整映射见脚本
}
```

**向用户解释搜索意图时用中文**（风声、鸟鸣），URL 自动转拼音。

## 搜索关键词用中文，URL 自动转拼音

向用户解释搜索意图时用中文（风声、鸟鸣、喘息、脚步），不要用拼音。
脚本内置 `_KEYWORD_TO_PINYIN` 映射表自动转换 URL（站长之家只支持拼音 URL）。

```python
_KEYWORD_TO_PINYIN = {
    "风声": "fengsheng", "鸟叫声": "niaojiaosheng", "欢呼声": "huanhuansheng",
    "喘息": "tansheng", "脚步": "jiaosheng", "登山": "dengshan",
    # ... 完整映射见脚本
}
```

## API Key 记录

- Mem0 API key 会过期（2026-06-17 发现 401 token_not_valid）
- 本地 memory 是可靠 fallback，mem0 不可用时不要阻塞工作
