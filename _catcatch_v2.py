"""
猫抓引擎驱动 V2 - 修复集数解析 + 反重定向

关键修复:
  1. 集数从 data-index 属性获取 (非URL中的数字)
  2. 阻止反爬虫脚本 (cdndefend/disable-devtool) 防止重定向到百度
  3. 重试机制: 页面被重定向时自动重试
  4. 猫抓 findMedia 检测逻辑不变
"""
import os
import re
import sys
import json
import glob
import time
import subprocess
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

# ════════════════════════════════════════════════════════════════════
# 配置
# ════════════════════════════════════════════════════════════════════
ROOT = r"C:\Users\xx\Desktop\reclip-main"
CATCATCH_DIR = os.path.join(ROOT, "cat-catch-master")
DOWNLOAD_DIR = os.path.join(ROOT, "downloads")
PROBE_DIR = os.path.join(CATCATCH_DIR, "_catcatch_probe")
FFMPEG = r"D:\software\ffmpeg\bin\ffmpeg.exe"
FFPROBE = r"D:\software\ffmpeg\bin\ffprobe.exe"
CONCURRENT_FRAGMENTS = 8

os.makedirs(DOWNLOAD_DIR, exist_ok=True)
os.makedirs(PROBE_DIR, exist_ok=True)

SERIES_URLS = {
    "26390":  "https://www.dushe07.com/play/26390-41-936828.html",
    "240994": "https://www.dushe07.com/play/240994-41-987230.html",
    "216800": "https://www.dushe07.com/play/216800-41-988596.html",
    "239430": "https://www.dushe07.com/play/239430-41-936705.html",
}

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"

# 隐身脚本
STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN','zh','en']});
window.chrome = window.chrome || {runtime: {}};
Object.defineProperty(navigator, 'platform', {get: () => 'Win32'});
// 阻止 cdndefend 重定向
window.location_orig = window.location;
Object.defineProperty(window, 'location', {
    writable: false,
    configurable: false
});
"""

# 猫抓 init.js 扩展名列表
CATCATCH_EXTS = {
    "flv", "hlv", "f4v", "mp4", "mp3", "wma", "wav", "m4a",
    "webm", "ogg", "ogv", "acc", "mov", "mkv", "m4s",
    "m3u8", "m3u", "mpeg", "avi", "wmv", "asf", "movie",
    "divx", "mpeg4", "vid", "aac", "mpd", "weba", "opus",
}

IMAGE_EXT = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".ico", ".bmp")
AD_DOMAINS = (
    "bcebos.com", "ps.baidu.com", "hm.baidu.com",
    "google-analytics.com", "googletagmanager.com",
    "doubleclick.net", "googlesyndication.com", "cnzz.com", "umeng.com",
)

# 需要阻止的域名 (反爬虫/统计脚本)
BLOCK_DOMAINS = [
    "cdndefend",
    "disable-devtool",
    "vf.cyscyy.com",  # 网站静态资源中包含反调试脚本
]


def is_media_url(url):
    """猫抓 findMedia: 检查扩展名"""
    path = url.split("?", 1)[0].split("#", 1)[0].lower()
    if path.endswith(IMAGE_EXT):
        return False
    try:
        host = urlparse(url).netloc.lower()
        if any(d in host for d in AD_DOMAINS):
            return False
    except Exception:
        pass
    parsed = urlparse(url)
    parts = parsed.path.split(".")
    if len(parts) > 1:
        ext = parts[-1].lower()
        if ext in CATCATCH_EXTS:
            return True
    return ".m3u8" in url.lower()


def is_stream_url(url):
    """猫抓: m3u8 是可信视频流信号"""
    path = url.split("?", 1)[0].split("#", 1)[0].lower()
    if path.endswith(IMAGE_EXT):
        return False
    try:
        host = urlparse(url).netloc.lower()
        if any(d in host for d in AD_DOMAINS):
            return False
    except Exception:
        pass
    return ".m3u8" in url.lower()


def find_episode_links_from_html(html, series_id):
    """从HTML解析集数链接

    修复: 使用 data-index 属性获取集数 (非URL中的数字)
    URL格式: /play/SID-SOURCEID-RESOURCEID.html
    集数: <a data-index="N" href="/play/SID-41-XXX.html">
    """
    # 匹配带 data-index 的集数链接
    pat = re.compile(
        r'href=["\'](/play/(\d+)-\d+-(\d+)\.html)["\'][^>]*data-index=["\'](\d+)["\']',
        re.IGNORECASE,
    )
    matches = pat.findall(html)
    links = []
    seen = set()
    for full_path, sid, resource_id, data_index in matches:
        if sid != series_id:
            continue
        ep = int(data_index)
        if ep in seen:
            continue
        seen.add(ep)
        url = "https://www.dushe07.com" + full_path
        links.append((ep, url, resource_id))

    # 如果没有 data-index, 回退到旧解析方式
    if not links:
        pat2 = re.compile(
            r'href=["\'](/play/(\d+)-(\d+)-(\d+)\.html)["\']',
            re.IGNORECASE,
        )
        matches2 = pat2.findall(html)
        for full_path, sid, ep_str, src in matches2:
            if sid != series_id:
                continue
            ep = int(ep_str)
            if ep in seen:
                continue
            seen.add(ep)
            url = "https://www.dushe07.com" + full_path
            links.append((ep, url, src))

    links.sort(key=lambda x: x[0])
    return links


def find_title_in_html(html):
    """从HTML提取节目标题"""
    m = re.search(r'<title>([^<]+)</title>', html, re.IGNORECASE)
    if m:
        title = m.group(1).strip()
        title = re.sub(r'\s*[-—]\s*毒舌电影.*$', '', title)
        if title and "百度" not in title:
            return title
    m = re.search(r'<meta\s+property="og:title"\s+content="([^"]+)"', html, re.IGNORECASE)
    if m:
        title = m.group(1).strip()
        if title and "百度" not in title:
            return title
    return None


def extract_episodes_js():
    """JavaScript: 从页面DOM提取集数链接 (在重定向前执行)"""
    return """
    () => {
        const links = document.querySelectorAll('a[href*="/play/"][data-index]');
        const results = [];
        const seen = new Set();
        links.forEach(a => {
            const href = a.getAttribute('href');
            const dataIndex = a.getAttribute('data-index');
            if (href && dataIndex) {
                const ep = parseInt(dataIndex);
                if (!seen.has(ep)) {
                    seen.add(ep);
                    results.push({ ep: ep, url: href });
                }
            }
        });
        // 也尝试没有 data-index 的
        if (results.length === 0) {
            const allLinks = document.querySelectorAll('a[href*="/play/"]');
            allLinks.forEach(a => {
                const href = a.getAttribute('href');
                if (href) {
                    const m = href.match(/\\/play\\/(\\d+)-(\\d+)-(\\d+)\\.html/);
                    if (m) {
                        const ep = parseInt(m[2]);
                        if (!seen.has(ep)) {
                            seen.add(ep);
                            results.push({ ep: ep, url: href });
                        }
                    }
                }
            });
        }
        return { episodes: results, title: document.title };
    }
    """


def probe_episode(page, url, ep_num):
    """用猫抓检测逻辑访问单集页面, 捕获 m3u8"""
    captured_streams = []

    def on_request(request):
        u = request.url
        if is_media_url(u) and is_stream_url(u):
            if u not in captured_streams:
                captured_streams.append(u)

    def on_response(response):
        u = response.url
        try:
            ct = response.headers.get("content-type", "").split(";")[0].strip().lower()
            if ct in ("application/vnd.apple.mpegurl", "application/x-mpegurl",
                       "application/mpegurl", "application/octet-stream-m3u8"):
                if u not in captured_streams:
                    captured_streams.append(u)
        except Exception:
            pass

    page.on("request", on_request)
    page.on("response", on_response)

    try:
        page.goto(url, wait_until="domcontentloaded", timeout=25000)
    except PlaywrightTimeout:
        pass
    except Exception:
        pass

    # 等待猫抓捕获 m3u8
    for i in range(20):
        page.wait_for_timeout(1000)
        if captured_streams:
            break

    page.remove_listener("request", on_request)
    page.remove_listener("response", on_response)

    if not captured_streams:
        # 猫抓 content-script: 检查 video/audio 元素
        try:
            video_srcs = page.evaluate("""
                () => {
                    const r = [];
                    document.querySelectorAll("video, audio").forEach(v => {
                        if (v.currentSrc) r.push(v.currentSrc);
                    });
                    return r;
                }
            """)
            for vs in video_srcs:
                if ".m3u8" in vs.lower():
                    captured_streams.append(vs)
                    break
        except Exception:
            pass

    if not captured_streams:
        # 从HTML内嵌查找
        try:
            html = page.content()
            m = re.search(r'(https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*)', html)
            if m:
                captured_streams.append(m.group(1))
        except Exception:
            pass

    return captured_streams[0] if captured_streams else None


def get_episode_list(page, series_id, entry_url, max_retries=5):
    """获取集数列表 (带重试, 防止反爬虫重定向)"""
    for attempt in range(max_retries):
        print(f"  尝试获取集数列表 (第{attempt+1}次)...")

        # 用JS立即提取DOM中的集数链接 (在重定向前)
        js_result = None
        try:
            page.goto(entry_url, wait_until="domcontentloaded", timeout=20000)
        except PlaywrightTimeout:
            pass
        except Exception:
            pass

        # 立即用JS提取 (不等页面完全加载)
        try:
            js_result = page.evaluate(extract_episodes_js())
        except Exception:
            pass

        # 如果JS提取到了集数, 返回
        if js_result and js_result.get("episodes"):
            eps = js_result["episodes"]
            title = js_result.get("title", "")
            if "百度" in title:
                title = ""
            print(f"  ✓ JS提取到 {len(eps)} 集")
            return eps, title

        # 等待更多时间后从HTML提取
        page.wait_for_timeout(3000)
        try:
            html = page.content()
            title = find_title_in_html(html) or ""
            episodes = find_episode_links_from_html(html, series_id)
            if episodes:
                print(f"  ✓ HTML提取到 {len(episodes)} 集")
                return [{"ep": ep, "url": u} for ep, u, _ in episodes], title
        except Exception:
            pass

        # 检查是否被重定向
        try:
            current_url = page.url
            if "baidu" in current_url.lower():
                print(f"  ✗ 被重定向到百度, 重试...")
                continue
        except Exception:
            pass

        # 等待更长时间再试
        page.wait_for_timeout(2000)

    print(f"  ✗ {max_retries}次尝试均失败")
    return [], ""


def probe_series(playwright, series_id, entry_url):
    """探测一个节目"""
    print(f"\n{'='*70}")
    print(f"探测节目 {series_id}: {entry_url}")
    print(f"{'='*70}")

    browser = playwright.chromium.launch(
        headless=False,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--disable-features=IsolateOrigins,site-per-process",
        ]
    )
    context = browser.new_context(
        user_agent=USER_AGENT,
        viewport={"width": 1920, "height": 1080},
        locale="zh-CN",
        ignore_https_errors=True,
    )
    context.add_init_script(STEALTH_JS)

    # 阻止反爬虫脚本
    def block_anti_bot(route):
        url = route.request.url.lower()
        for domain in BLOCK_DOMAINS:
            if domain in url:
                route.abort()
                return
        route.continue_()

    context.route("**/*", block_anti_bot)
    page = context.new_page()

    # Step 1: 获取集数列表
    episodes_data, title = get_episode_list(page, series_id, entry_url)
    if title:
        title = re.sub(r'\s*[-—]\s*毒舌电影.*$', '', title).strip()
    else:
        title = f"节目{series_id}"

    print(f"  节目名: {title}")
    print(f"  集数: {len(episodes_data)}")

    # 保存页面HTML
    try:
        html = page.content()
        safe = re.sub(r'[\\/:*?"<>|]', '_', title)
        with open(os.path.join(PROBE_DIR, f"page_{series_id}_v2.html"), "w", encoding="utf-8") as f:
            f.write(html)
    except Exception:
        pass

    if not episodes_data:
        # 如果入口页无法获取, 尝试详情页
        print(f"  入口页无集数, 尝试详情页...")
        for durl in [f"https://www.dushe07.com/movie/{series_id}.html",
                     f"https://www.dushe07.com/vod/{series_id}.html"]:
            try:
                page.goto(durl, wait_until="domcontentloaded", timeout=15000)
                page.wait_for_timeout(2000)
                js_result = page.evaluate(extract_episodes_js())
                if js_result and js_result.get("episodes"):
                    episodes_data = js_result["episodes"]
                    if not title or title == f"节目{series_id}":
                        t = js_result.get("title", "")
                        if t and "百度" not in t:
                            title = re.sub(r'\s*[-—]\s*毒舌电影.*$', '', t).strip()
                    print(f"  ✓ 详情页提取到 {len(episodes_data)} 集")
                    break
                # 也从HTML提取
                html2 = page.content()
                eps = find_episode_links_from_html(html2, series_id)
                if eps:
                    episodes_data = [{"ep": ep, "url": u} for ep, u, _ in eps]
                    t = find_title_in_html(html2)
                    if t:
                        title = t
                    print(f"  ✓ 详情页HTML提取到 {len(eps)} 集")
                    break
            except Exception:
                continue

    # Step 2: 逐集捕获 m3u8
    episode_m3u8s = {}
    for ep_info in episodes_data:
        ep = ep_info["ep"]
        ep_url = ep_info["url"]
        if not ep_url.startswith("http"):
            ep_url = "https://www.dushe07.com" + ep_url

        # 重试捕获 m3u8
        m3u8 = None
        for attempt in range(3):
            m3u8 = probe_episode(page, ep_url, ep)
            if m3u8:
                break
            print(f"    ep{ep} 第{attempt+1}次未捕获, 重试...")

        if m3u8:
            episode_m3u8s[ep] = m3u8
            print(f"  ✓ ep{ep:02d}: {m3u8[:70]}...")
        else:
            print(f"  ✗ ep{ep:02d}: 未捕获到m3u8")

    browser.close()

    result = {
        "series_id": series_id,
        "title": title,
        "entry_url": entry_url,
        "total_episodes": len(episodes_data),
        "episodes": episodes_data,
        "episode_m3u8s": episode_m3u8s,
        "m3u8_count": len(episode_m3u8s),
    }

    with open(os.path.join(PROBE_DIR, f"result_{series_id}_v2.json"), "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"\n  结果: {len(episode_m3u8s)}/{len(episodes_data)} 集有m3u8")
    return result


def download_episode(ep, m3u8_url, series_name, series_id):
    """下载单集: yt-dlp + ffmpeg"""
    tmp_out = os.path.join(DOWNLOAD_DIR, f"{series_id}_ep{ep:02d}.%(ext)s")
    for f in glob.glob(os.path.join(DOWNLOAD_DIR, f"{series_id}_ep{ep:02d}.*")):
        try:
            os.remove(f)
        except OSError:
            pass

    cmd = [
        sys.executable, "-m", "yt_dlp",
        "--no-playlist", "-f", "best",
        "--concurrent-fragments", str(CONCURRENT_FRAGMENTS),
        "--hls-use-mpegts", "--no-overwrites", "--no-check-certificates",
        "--add-headers", "Referer:https://www.dushe07.com/",
        "--add-headers", f"User-Agent:{USER_AGENT}",
        "--ffmpeg-location", os.path.dirname(FFMPEG),
        "-o", tmp_out,
        m3u8_url,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        if result.returncode != 0:
            return ep, False, f"yt-dlp: {(result.stderr or '')[-200:]}"
        files = glob.glob(os.path.join(DOWNLOAD_DIR, f"{series_id}_ep{ep:02d}.*"))
        if not files:
            return ep, False, "no output"
        src = max(files, key=os.path.getmtime)

        out_dir = os.path.join(ROOT, series_name)
        os.makedirs(out_dir, exist_ok=True)
        dst = os.path.join(out_dir, f"{series_name}_第{ep:02d}集.mp4")
        if os.path.exists(dst):
            os.remove(dst)
        ff_result = subprocess.run(
            [FFMPEG, "-y", "-i", src, "-c", "copy", "-movflags", "+faststart", dst],
            capture_output=True, text=True, timeout=600)
        try:
            os.remove(src)
        except OSError:
            pass
        if ff_result.returncode != 0:
            return ep, False, f"ffmpeg: {(ff_result.stderr or '')[-200:]}"
        size_mb = os.path.getsize(dst) / (1024 * 1024)
        probe = subprocess.run(
            [FFPROBE, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", dst],
            capture_output=True, text=True, timeout=30)
        dur = float(probe.stdout.strip()) if probe.stdout.strip() else 0
        if dur < 60:
            return ep, False, f"too short: {dur}s"
        return ep, True, f"{os.path.basename(dst)} ({size_mb:.1f}MB, {dur:.0f}s)"
    except subprocess.TimeoutExpired:
        return ep, False, "timeout"
    except Exception as e:
        return ep, False, str(e)


def main():
    print("=" * 70)
    print("猫抓引擎 V2 - 集数解析修复 + 反重定向")
    print("=" * 70)

    # Phase 1: 探测
    all_results = {}
    with sync_playwright() as p:
        for sid, url in SERIES_URLS.items():
            try:
                result = probe_series(p, sid, url)
                all_results[sid] = result
            except Exception as e:
                print(f"\n探测 {sid} 失败: {e}")
                all_results[sid] = {"series_id": sid, "title": f"节目{sid}",
                                   "episode_m3u8s": {}, "total_episodes": 0,
                                   "episodes": []}

    with open(os.path.join(PROBE_DIR, "all_results_v2.json"), "w", encoding="utf-8") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)

    # 探测摘要
    print("\n" + "=" * 70)
    print("探测摘要:")
    for sid, r in all_results.items():
        print(f"  [{sid}] {r.get('title', '?')}: "
              f"{r.get('m3u8_count', 0)}/{r.get('total_episodes', 0)} 集有m3u8")
    print("=" * 70)

    # Phase 2: 下载
    print("\nPhase 2: 下载所有集数")
    total_ok = 0
    total_fail = 0
    for sid, result in all_results.items():
        series_name = result.get("title") or f"节目{sid}"
        m3u8s = result.get("episode_m3u8s", {})
        if not m3u8s:
            print(f"\n[{sid}] {series_name}: 无m3u8, 跳过")
            continue
        print(f"\n[{sid}] {series_name}: {len(m3u8s)} 集待下载")
        for ep_str, m3u8_url in sorted(m3u8s.items(), key=lambda x: int(x[0])):
            ep = int(ep_str)
            _, ok, msg = download_episode(ep, m3u8_url, series_name, sid)
            if ok:
                total_ok += 1
                print(f"  ✓ ep{ep:02d}: {msg}")
            else:
                total_fail += 1
                print(f"  ✗ ep{ep:02d}: {msg}")

    print(f"\n{'='*70}")
    print(f"下载完成: 成功 {total_ok}, 失败 {total_fail}")
    print(f"{'='*70}")

    # Phase 3: 验证
    print("\nPhase 3: 验证")
    for sid, result in all_results.items():
        series_name = result.get("title") or f"节目{sid}"
        out_dir = os.path.join(ROOT, series_name)
        if not os.path.exists(out_dir):
            continue
        files = sorted(glob.glob(os.path.join(out_dir, "*.mp4")))
        ok = 0
        for f in files:
            size_mb = os.path.getsize(f) / 1048576
            try:
                r = subprocess.run(
                    [FFPROBE, "-v", "error", "-show_entries", "format=duration",
                     "-of", "default=noprint_wrappers=1:nokey=1", f],
                    capture_output=True, text=True, timeout=30)
                dur = float(r.stdout.strip()) if r.stdout.strip() else 0
            except Exception:
                dur = -1
            s = "✓" if dur >= 60 else "✗"
            if dur >= 60:
                ok += 1
            print(f"  {s} {os.path.basename(f)}: {size_mb:.1f}MB, {dur:.0f}s")
        print(f"  [{sid}] {series_name}: {ok}/{len(files)} 有效")


if __name__ == "__main__":
    main()
