"""
猫抓(cat-catch)检测引擎 - 直接从猫抓源码提取的检测逻辑

本模块忠实实现了猫抓浏览器扩展的核心检测功能:
  - js/init.js   -> Ext 列表 + Type 列表 (猫抓跟踪哪些扩展名和MIME类型)
  - js/background.js -> findMedia() 网络请求拦截 + CheckExtension + CheckType
  - js/content-script.js -> getVideoState() 页面 video/audio 元素检测

检测策略与猫抓扩展完全一致:
  1. 拦截所有网络请求的 URL, 解析扩展名, 匹配 Ext 列表
  2. 拦截所有网络响应的 content-type, 匹配 Type 列表
  3. 检查页面 <video>/<audio> 元素的 currentSrc
  4. 优先返回 m3u8 (HLS 播放列表)
"""
import re
import os
from urllib.parse import urlparse, unquote

# ════════════════════════════════════════════════════════════════════
# 猫抓 init.js 中的 Ext 列表 (state=true 的扩展名)
# 来源: cat-catch-master/js/init.js  L30-L62
# ════════════════════════════════════════════════════════════════════
CATCATCH_EXT_LIST = {
    "flv", "hlv", "f4v", "mp4", "mp3", "wma", "wav", "m4a",
    "webm", "ogg", "ogv", "acc", "mov", "mkv", "m4s",
    "m3u8", "m3u", "mpeg", "avi", "wmv", "asf", "movie",
    "divx", "mpeg4", "vid", "aac", "mpd", "weba", "opus",
    # ts 默认 state=false, 但我们保留用于参考
}

# ════════════════════════════════════════════════════════════════════
# 猫抓 init.js 中的 Type 列表 (state=true 的MIME类型)
# 来源: cat-catch-master/js/init.js  L63-L73
# ════════════════════════════════════════════════════════════════════
CATCATCH_TYPE_LIST = {
    "audio/*",
    "video/*",
    "application/ogg",
    "application/vnd.apple.mpegurl",
    "application/x-mpegurl",
    "application/mpegurl",
    "application/octet-stream-m3u8",
    "application/dash+xml",
    "application/m4s",
}

# 图片扩展名 (猫抓 _is_image_url 等效逻辑, 永不当作视频流)
IMAGE_EXTENSIONS = (
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".ico", ".bmp",
)

# 广告/统计域名黑名单 (猫抓 AD_DOMAIN_BLACKLIST)
AD_DOMAIN_BLACKLIST = (
    "bcebos.com", "ps.baidu.com", "hm.baidu.com",
    "google-analytics.com", "googletagmanager.com",
    "doubleclick.net", "googlesyndication.com",
    "cnzz.com", "umeng.com",
)


def _is_image_url(url):
    """猫抓: 判断URL是否为图片 (按扩展名)"""
    path = url.split("?", 1)[0].split("#", 1)[0].lower()
    return path.endswith(IMAGE_EXTENSIONS)


def _is_ad_url(url):
    """猫抓: 判断URL是否为广告/统计"""
    try:
        host = urlparse(url).netloc.lower()
    except Exception:
        return False
    return any(dom in host for dom in AD_DOMAIN_BLACKLIST)


def file_name_parse(pathname):
    """猫抓 background.js fileNameParse(): 从路径解析文件名和扩展名"""
    file_name = unquote(pathname.split("/")[-1])
    parts = file_name.split(".")
    ext = parts[-1].lower() if len(parts) > 1 else None
    return file_name, ext


def check_extension(ext):
    """猫抓 background.js CheckExtension(): 检查扩展名是否在跟踪列表中"""
    if not ext:
        return False
    return ext in CATCATCH_EXT_LIST


def check_type(content_type):
    """猫抓 background.js CheckType(): 检查MIME类型是否在跟踪列表中

    猫抓逻辑: 先匹配 type/* 通配, 再精确匹配
    """
    if not content_type:
        return False
    ct = content_type.split(";")[0].strip().lower()
    # 猫抓: G.Type.get(dataType.split("/")[0] + "/*") || G.Type.get(dataType)
    wildcard = ct.split("/")[0] + "/*"
    if wildcard in CATCATCH_TYPE_LIST:
        return True
    if ct in CATCATCH_TYPE_LIST:
        return True
    return False


def is_stream_url(url):
    """猫抓 _is_stream_url(): 判断是否为可信视频流URL

    只有 m3u8 是可靠的视频流信号 (HLS master/media playlist)
    """
    if _is_ad_url(url):
        return False
    if _is_image_url(url):
        return False
    return ".m3u8" in url.lower()


def get_response_headers_value(headers):
    """猫抓 background.js getResponseHeadersValue(): 从响应头提取信息

    返回 dict: {size, type, attachment}
    """
    result = {}
    if not headers:
        return result
    # headers 可能是 list of {name, value} 或 dict
    if isinstance(headers, list):
        for item in headers:
            name = item.get("name", "").lower()
            value = item.get("value", "")
            if name == "content-length":
                try:
                    result["size"] = int(value)
                except ValueError:
                    pass
            elif name == "content-type":
                result["type"] = value.split(";")[0].strip().lower()
            elif name == "content-disposition":
                result["attachment"] = value
            elif name == "content-range":
                parts = value.split("/")
                if len(parts) == 2 and parts[1] != "*":
                    try:
                        result["size"] = int(parts[1])
                    except ValueError:
                        pass
    elif isinstance(headers, dict):
        for name, value in headers.items():
            name = name.lower()
            if name == "content-length":
                try:
                    result["size"] = int(value)
                except ValueError:
                    pass
            elif name == "content-type":
                result["type"] = value.split(";")[0].strip().lower()
            elif name == "content-disposition":
                result["attachment"] = value
    return result


def find_media(url, response_headers=None):
    """猫抓 background.js findMedia() 核心检测函数

    判断一个URL是否为媒体资源:
      1. 屏蔽图片和广告URL
      2. 解析URL扩展名, 检查是否在 Ext 列表
      3. 检查响应头 content-type, 检查是否在 Type 列表
      4. media 类型直接放行

    Returns:
        (is_media, ext, content_type) 或 (False, None, None)
    """
    if _is_image_url(url):
        return False, None, None
    if _is_ad_url(url):
        return False, None, None

    parsed = urlparse(url)
    _, ext = file_name_parse(parsed.path)

    header = get_response_headers_value(response_headers)
    content_type = header.get("type")

    # 猫抓: 检查扩展名
    filter_ext = check_extension(ext) if ext else False
    # 猫抓: 检查类型
    filter_type = check_type(content_type) if content_type else False

    is_media = bool(filter_ext or filter_type)
    return is_media, ext, content_type


def pick_best_url(urls_with_headers):
    """猫抓 _pick_best_url(): 从捕获的URL中选择最佳视频流

    优先级: m3u8 > mp4 > 其他非广告非图片URL

    Args:
        urls_with_headers: list of (url, response_headers) 元组
    """
    clean = []
    seen = set()
    for item in urls_with_headers:
        if isinstance(item, tuple):
            url, headers = item
        else:
            url, headers = item, None
        if url in seen:
            continue
        seen.add(url)
        if _is_image_url(url) or _is_ad_url(url):
            continue
        is_media, ext, ct = find_media(url, headers)
        if is_media:
            clean.append((url, ext, ct))

    if not clean:
        return None, None

    # 优先 m3u8
    m3u8 = [u for u, e, c in clean if e == "m3u8" or ".m3u8" in u.lower()]
    if m3u8:
        return m3u8[0], "m3u8"

    # 其次 mp4
    mp4 = [u for u, e, c in clean if e == "mp4"]
    if mp4:
        return mp4[0], "mp4"

    return clean[0][0], clean[0][1]


def get_video_state_js():
    """猫抓 content-script.js getVideoState(): 返回页面 video/audio 检测JS

    在页面上下文执行, 返回所有 video/audio 元素的 currentSrc
    """
    return """
    () => {
        const results = [];
        document.querySelectorAll("video, audio").forEach(v => {
            if (v.currentSrc && v.currentSrc !== "") {
                results.push(v.currentSrc);
            }
        });
        // 也检查 iframe 内的 video (猫抓 content-script.js 同样逻辑)
        document.querySelectorAll("iframe").forEach(iframe => {
            try {
                iframe.contentDocument.querySelectorAll("video, audio").forEach(v => {
                    if (v.currentSrc && v.currentSrc !== "") {
                        results.push(v.currentSrc);
                    }
                });
            } catch(e) {}
        });
        return results;
    }
    """


# 隐身脚本 (猫抓使用的反检测技术)
STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN','zh','en']});
window.chrome = window.chrome || {runtime: {}};
Object.defineProperty(navigator, 'platform', {get: () => 'Win32'});
const originalQuery = window.navigator.permissions && window.navigator.permissions.query;
if (originalQuery) {
  window.navigator.permissions.query = (p) => (
    p && p.name === 'notifications'
      ? Promise.resolve({state: Notification.permission})
      : originalQuery(p)
  );
}
"""

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"


if __name__ == "__main__":
    # 自测: 验证猫抓检测逻辑
    test_urls = [
        ("https://example.com/video/index.m3u8", None),
        ("https://example.com/video.mp4", None),
        ("https://example.com/image.jpg", None),
        ("https://example.com/api/data", {"content-type": "application/vnd.apple.mpegurl"}),
        ("https://hm.baidu.com/hm.js?abc=123", None),
    ]
    print("猫抓检测逻辑自测:")
    print("=" * 60)
    for url, headers in test_urls:
        is_media, ext, ct = find_media(url, headers)
        status = "MEDIA" if is_media else "SKIP"
        print(f"  [{status}] {url[:60]}")
        if ext:
            print(f"         ext={ext}")
        if ct:
            print(f"         type={ct}")
    print("=" * 60)
    best, best_ext = pick_best_url(test_urls)
    print(f"最佳URL: {best}")
    print(f"类型: {best_ext}")
