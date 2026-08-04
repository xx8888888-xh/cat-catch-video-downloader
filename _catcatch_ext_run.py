"""
直接加载猫抓扩展到浏览器中运行 - 最忠实的"用猫抓源码"方式

工作流程:
  1. 启动 Chromium 并加载 cat-catch 扩展 (manifest.json)
  2. 猫抓 background.js 自动通过 chrome.webRequest 拦截所有网络请求
  3. 猫抓用 init.js 的 Ext/Type 列表检测媒体URL
  4. 访问每个 play 页面, 猫抓自动捕获 m3u8
  5. 从猫抓的 chrome.storage 读取捕获的数据
  6. 下载所有集数

同时用猫抓的 content-script.js 逻辑检测页面 video/audio 元素。
"""
import os
import re
import sys
import json
import glob
import time
import subprocess
from urllib.parse import urlparse, unquote
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

# 4个节目的入口 URL
SERIES_URLS = {
    "26390":  "https://www.dushe07.com/play/26390-41-936828.html",
    "240994": "https://www.dushe07.com/play/240994-41-987230.html",
    "216800": "https://www.dushe07.com/play/216800-41-988596.html",
    "239430": "https://www.dushe07.com/play/239430-41-936705.html",
}

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN','zh','en']});
window.chrome = window.chrome || {runtime: {}};
Object.defineProperty(navigator, 'platform', {get: () => 'Win32'});
"""

# 猫抓 init.js 中的扩展名列表 (state=true)
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


def is_media_url(url):
    """猫抓 findMedia 检测: 检查扩展名"""
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
    """猫抓: 只有 m3u8 是可信的视频流信号"""
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


def find_episode_links(html, series_id):
    """查找所有集数链接 /play/SID-EP-SRC.html"""
    pat = re.compile(
        r'href=["\'](/play/(\d+)-(\d+)-(\d+)\.html)["\']',
        re.IGNORECASE,
    )
    matches = pat.findall(html)
    links = []
    seen = set()
    for full_path, sid, ep, src in matches:
        if sid != series_id:
            continue
        ep_int = int(ep)
        if ep_int in seen:
            continue
        seen.add(ep_int)
        url = "https://www.dushe07.com" + full_path
        links.append((ep_int, url, src))
    links.sort(key=lambda x: x[0])
    return links


def get_catcatch_storage_script():
    """读取猫抓 chrome.storage.local 中的 MediaData

    猫抓 background.js save() 函数将捕获的数据存入 chrome.storage
    数据格式: { MediaData: { tabId: [ {url, ext, type, ...}, ... ] } }
    """
    return """
    async () => {
        try {
            // 猫抓将数据存入 chrome.storage.session 或 chrome.storage.local
            const sessionData = await chrome.storage.session.get('MediaData').catch(() => ({}));
            const localData = await chrome.storage.local.get('MediaData').catch(() => ({}));
            const mediaData = sessionData.MediaData || localData.MediaData || {};
            const results = [];
            for (const [tabId, items] of Object.entries(mediaData)) {
                if (Array.isArray(items)) {
                    for (const item of items) {
                        results.push({
                            url: item.url,
                            ext: item.ext,
                            type: item.type,
                            name: item.name,
                            title: item.title,
                            tabId: tabId
                        });
                    }
                }
            }
            return results;
        } catch(e) {
            return { error: e.message };
        }
    }
    """


def get_page_videos_js():
    """猫抓 content-script.js getVideoState: 检测页面 video/audio"""
    return """
    () => {
        const results = [];
        document.querySelectorAll("video, audio").forEach(v => {
            if (v.currentSrc && v.currentSrc !== "") results.push(v.currentSrc);
        });
        return results;
    }
    """


def probe_with_catcatch(playwright, series_id, entry_url):
    """使用加载了猫抓扩展的浏览器探测节目

    猫抓扩展会:
      1. background.js 通过 chrome.webRequest.onSendHeaders/onResponseStarted 拦截所有请求
      2. findMedia() 用 init.js 的 Ext/Type 列表检测媒体URL
      3. 将捕获的数据存入 chrome.storage
    """
    print(f"\n{'='*70}")
    print(f"探测节目 {series_id}: {entry_url}")
    print(f"{'='*70}")

    # 启动带猫抓扩展的 Chromium
    # Playwright 要求: 加载扩展必须用 headed 模式
    browser = playwright.chromium.launch(
        headless=False,
        args=[
            f"--disable-extensions-except={CATCATCH_DIR}",
            f"--load-extension={CATCATCH_DIR}",
            "--disable-blink-features=AutomationControlled",
        ]
    )
    context = browser.new_context(
        user_agent=USER_AGENT,
        viewport={"width": 1920, "height": 1080},
        locale="zh-CN",
        ignore_https_errors=True,
    )
    context.add_init_script(STEALTH_JS)
    page = context.new_page()

    # 网络请求拦截 (猫抓 background.js 的等效逻辑)
    captured_media = []  # 猫抓 findMedia 捕获的媒体URL
    captured_streams = []  # m3u8 流URL

    def on_request(request):
        url = request.url
        if is_media_url(url):
            captured_media.append(url)
            if is_stream_url(url):
                captured_streams.append(url)
                print(f"  [猫抓捕获] m3u8: {url[:80]}...")

    def on_response(response):
        url = response.url
        # 猫抓 CheckType: 检查 content-type
        try:
            headers = response.headers
            ct = headers.get("content-type", "").split(";")[0].strip().lower()
            if ct in ("application/vnd.apple.mpegurl", "application/x-mpegurl",
                       "application/mpegurl", "application/octet-stream-m3u8"):
                if url not in captured_streams:
                    captured_streams.append(url)
                    print(f"  [猫抓Type检测] m3u8: {url[:80]}...")
        except Exception:
            pass

    page.on("request", on_request)
    page.on("response", on_response)

    # Step 1: 访问入口页面
    entry_ep = int(entry_url.split("-")[1])
    print(f"\n  访问入口页 (ep{entry_ep})...")
    try:
        page.goto(entry_url, wait_until="domcontentloaded", timeout=25000)
    except PlaywrightTimeout:
        print("  超时, 继续等待...")
    except Exception as e:
        print(f"  访问错误: {e}")

    # 等待猫抓捕获 m3u8 (轮询, 猫抓也是轮询检测)
    for i in range(25):
        page.wait_for_timeout(1000)
        if captured_streams:
            break

    # 获取标题
    title = None
    try:
        t = page.title()
        if t and "百度" not in t.lower() and "百度" not in t:
            title = re.sub(r'\s*[-—]\s*毒舌电影.*$', '', t).strip()
    except Exception:
        pass

    # 如果标题为空, 尝试从 og:title 获取
    if not title:
        try:
            og_title = page.evaluate("""
                () => {
                    const meta = document.querySelector('meta[property="og:title"]');
                    return meta ? meta.content : '';
                }
            """)
            if og_title and "百度" not in og_title.lower():
                title = og_title.strip()
        except Exception:
            pass

    if not title:
        title = f"节目{series_id}"

    print(f"  节目名: {title}")

    # 保存页面HTML
    try:
        html = page.content()
        safe = re.sub(r'[\\/:*?"<>|]', '_', title)
        with open(os.path.join(PROBE_DIR, f"page_{series_id}.html"), "w", encoding="utf-8") as f:
            f.write(html)
    except Exception:
        html = ""

    # Step 2: 查找集数链接
    episodes = find_episode_links(html, series_id)
    print(f"  页面找到 {len(episodes)} 个集数链接")

    # 如果页面被重定向, 尝试详情页
    if not episodes:
        print(f"  尝试详情页...")
        detail_urls = [
            f"https://www.dushe07.com/movie/{series_id}.html",
            f"https://www.dushe07.com/vod/{series_id}.html",
            f"https://www.dushe07.com/index.php?m=vod-play-id-{series_id}-src-1-num-1.html",
        ]
        for durl in detail_urls:
            try:
                page.goto(durl, wait_until="domcontentloaded", timeout=15000)
                page.wait_for_timeout(3000)
                html2 = page.content()
                episodes = find_episode_links(html2, series_id)
                if episodes:
                    print(f"  详情页找到 {len(episodes)} 个集数链接")
                    with open(os.path.join(PROBE_DIR, f"detail_{series_id}.html"), "w", encoding="utf-8") as f:
                        f.write(html2)
                    break
            except Exception:
                continue

    # Step 3: 逐集访问, 捕获 m3u8
    episode_m3u8s = {}

    # 入口集的 m3u8
    if captured_streams:
        # 去重, 取第一个m3u8
        entry_m3u8 = captured_streams[0]
        episode_m3u8s[entry_ep] = entry_m3u8
        print(f"\n  ✓ ep{entry_ep}: {entry_m3u8[:80]}...")

    # 其他集
    for ep, ep_url, src in episodes:
        if ep == entry_ep:
            continue
        # 清空之前的捕获
        captured_streams.clear()

        print(f"\n  访问 ep{ep}...")
        try:
            page.goto(ep_url, wait_until="domcontentloaded", timeout=25000)
        except PlaywrightTimeout:
            pass
        except Exception as e:
            print(f"    访问错误: {e}")

        # 等待捕获
        for i in range(20):
            page.wait_for_timeout(1000)
            if captured_streams:
                break

        if captured_streams:
            episode_m3u8s[ep] = captured_streams[0]
            print(f"    ✓ m3u8: {captured_streams[0][:80]}...")
        else:
            # 猫抓 content-script: 检查 video/audio 元素
            try:
                video_srcs = page.evaluate(get_page_videos_js())
                for vs in video_srcs:
                    if ".m3u8" in vs.lower():
                        episode_m3u8s[ep] = vs
                        print(f"    ✓ video元素: {vs[:80]}...")
                        break
            except Exception:
                pass

            if ep not in episode_m3u8s:
                # 尝试从HTML内嵌查找
                try:
                    html3 = page.content()
                    m = re.search(r'(https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*)', html3)
                    if m:
                        episode_m3u8s[ep] = m.group(1)
                        print(f"    ✓ HTML内嵌: {m.group(1)[:80]}...")
                    else:
                        print(f"    ✗ 未捕获到m3u8")
                except Exception:
                    print(f"    ✗ 未捕获到m3u8")

    # 读取猫抓扩展存储的数据
    try:
        catcatch_data = page.evaluate(get_catcatch_storage_script())
        if isinstance(catcatch_data, list) and catcatch_data:
            print(f"\n  猫抓存储: {len(catcatch_data)} 条记录")
            # 查找未捕获到的集数
            for item in catcatch_data:
                url = item.get("url", "")
                if ".m3u8" in url.lower() and url not in [v for v in episode_m3u8s.values()]:
                    print(f"    猫抓存储中的m3u8: {url[:80]}...")
    except Exception as e:
        print(f"  读取猫抓存储失败: {e}")

    browser.close()

    result = {
        "series_id": series_id,
        "title": title,
        "entry_url": entry_url,
        "total_episodes": len(episodes),
        "episodes": [{"ep": ep, "url": u, "src": s} for ep, u, s in episodes],
        "episode_m3u8s": episode_m3u8s,
        "m3u8_count": len(episode_m3u8s),
    }

    result_path = os.path.join(PROBE_DIR, f"result_{series_id}.json")
    with open(result_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"\n  结果: {len(episode_m3u8s)} 集有m3u8 / {len(episodes)} 集总数")
    return result


def download_episode(ep, m3u8_url, series_name, series_id):
    """下载单集: yt-dlp 并行下载 → ffmpeg 封装"""
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
            err = (result.stderr or "").strip().split("\n")[-1][:200]
            return ep, False, f"yt-dlp: {err}"
        files = glob.glob(os.path.join(DOWNLOAD_DIR, f"{series_id}_ep{ep:02d}.*"))
        if not files:
            return ep, False, "no output file"
        src = max(files, key=os.path.getmtime)

        out_dir = os.path.join(DOWNLOAD_DIR, series_name)
        os.makedirs(out_dir, exist_ok=True)
        dst = os.path.join(out_dir, f"{series_name}_第{ep:02d}集.mp4")
        if os.path.exists(dst):
            os.remove(dst)
        ff_cmd = [FFMPEG, "-y", "-i", src, "-c", "copy", "-movflags", "+faststart", dst]
        ff_result = subprocess.run(ff_cmd, capture_output=True, text=True, timeout=600)
        try:
            os.remove(src)
        except OSError:
            pass
        if ff_result.returncode != 0:
            return ep, False, f"ffmpeg: {(ff_result.stderr or '')[-200:]}"
        size_mb = os.path.getsize(dst) / (1024 * 1024)
        probe_cmd = [FFPROBE, "-v", "error", "-show_entries", "format=duration",
                     "-of", "default=noprint_wrappers=1:nokey=1", dst]
        probe = subprocess.run(probe_cmd, capture_output=True, text=True, timeout=30)
        duration = float(probe.stdout.strip()) if probe.stdout.strip() else 0
        if duration < 60:
            return ep, False, f"file too short: {duration}s"
        return ep, True, f"{os.path.basename(dst)} ({size_mb:.1f}MB, {duration:.0f}s)"
    except subprocess.TimeoutExpired:
        return ep, False, "timeout"
    except Exception as e:
        return ep, False, str(e)


def main():
    print("=" * 70)
    print("猫抓扩展直接加载运行 - 探测+下载4个节目")
    print("=" * 70)

    # Phase 1: 探测
    all_results = {}
    with sync_playwright() as p:
        for sid, url in SERIES_URLS.items():
            try:
                result = probe_with_catcatch(p, sid, url)
                all_results[sid] = result
            except Exception as e:
                print(f"\n探测 {sid} 失败: {e}")
                all_results[sid] = {"series_id": sid, "title": f"节目{sid}",
                                   "episode_m3u8s": {}, "total_episodes": 0}

    summary_path = os.path.join(PROBE_DIR, "all_results.json")
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)

    # 打印探测摘要
    print("\n" + "=" * 70)
    print("探测摘要:")
    print("=" * 70)
    for sid, r in all_results.items():
        print(f"  [{sid}] {r.get('title', '?')}: "
              f"{r.get('m3u8_count', 0)}/{r.get('total_episodes', 0)} 集有m3u8")

    # Phase 2: 下载
    print("\n" + "=" * 70)
    print("Phase 2: 下载所有集数")
    print("=" * 70)

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
            ep_ok, ok, msg = download_episode(ep, m3u8_url, series_name, sid)
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
    print("\nPhase 3: 验证所有视频")
    for sid, result in all_results.items():
        series_name = result.get("title") or f"节目{sid}"
        out_dir = os.path.join(DOWNLOAD_DIR, series_name)
        if not os.path.exists(out_dir):
            continue
        files = sorted(glob.glob(os.path.join(out_dir, "*.mp4")))
        ok_count = 0
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
            status = "✓" if dur >= 60 else "✗"
            if dur >= 60:
                ok_count += 1
            print(f"  {status} {os.path.basename(f)}: {size_mb:.1f}MB, {dur:.0f}s")
        print(f"  [{sid}] {series_name}: {ok_count}/{len(files)} 有效")


if __name__ == "__main__":
    main()
