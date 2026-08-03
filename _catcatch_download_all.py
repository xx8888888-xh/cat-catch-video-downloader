"""
猫抓引擎驱动 - 4个新节目全集探测+下载

使用 _catcatch_engine.py (猫抓源码检测逻辑) 实现:
  1. 访问每个 play 页面, 用猫抓 findMedia 逻辑捕获 m3u8
  2. 解析页面集数链接
  3. 逐集访问, 捕获每集 m3u8
  4. yt-dlp 并行下载 + ffmpeg 封装为标准 MP4
  5. 按节目名分文件夹, 按集数命名

节目列表:
  26390  - 不白吃话山海经
  240994 - 不白吃古诗词漫游记第二季
  216800 - 不白吃古诗词漫游记
  239430 - 不白吃古诗词漫游记第一季
"""
import os
import re
import sys
import json
import glob
import subprocess
import time
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

# 导入猫抓检测引擎
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _catcatch_engine import (
    find_media, is_stream_url, _is_image_url, _is_ad_url,
    pick_best_url, get_video_state_js, STEALTH_JS, USER_AGENT,
    file_name_parse, check_extension, check_type,
)

# ════════════════════════════════════════════════════════════════════
# 配置
# ════════════════════════════════════════════════════════════════════
ROOT = r"C:\Users\xx\Desktop\reclip-main"
DOWNLOAD_DIR = os.path.join(ROOT, "downloads")
PROBE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_catcatch_probe")
FFMPEG = r"D:\software\ffmpeg\bin\ffmpeg.exe"
FFPROBE = r"D:\software\ffmpeg\bin\ffprobe.exe"
CONCURRENT_FRAGMENTS = 8

os.makedirs(DOWNLOAD_DIR, exist_ok=True)
os.makedirs(PROBE_DIR, exist_ok=True)

# 4个节目的入口 URL (play 页面)
SERIES_URLS = {
    "26390":  "https://www.dushe07.com/play/26390-41-936828.html",
    "240994": "https://www.dushe07.com/play/240994-41-987230.html",
    "216800": "https://www.dushe07.com/play/216800-41-988596.html",
    "239430": "https://www.dushe07.com/play/239430-41-936705.html",
}

# 节目名映射 (探测到的标题中提取)
SERIES_NAMES = {}


def find_episode_links(html, series_id):
    """从HTML中查找所有集数链接 (猫抓 content-script getPage 等效逻辑)

    匹配 /play/SID-EP-SRC.html 格式
    """
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


def find_title_in_html(html):
    """从HTML中提取节目标题"""
    # <title>xxx-毒舌电影</title>
    m = re.search(r'<title>([^<]+)</title>', html, re.IGNORECASE)
    if m:
        title = m.group(1).strip()
        # 去掉 -毒舌电影 后缀
        title = re.sub(r'\s*[-—]\s*毒舌电影.*$', '', title)
        if title and "百度" not in title:
            return title
    # og:title
    m = re.search(r'<meta\s+property="og:title"\s+content="([^"]+)"', html, re.IGNORECASE)
    if m:
        title = m.group(1).strip()
        if title and "百度" not in title:
            return title
    return None


def probe_episode(page, url, series_id, ep_num):
    """用猫抓检测逻辑访问单集页面, 捕获 m3u8

    猫抓 background.js findMedia 逻辑:
      - 拦截所有网络请求
      - 检查扩展名 (Ext列表) + content-type (Type列表)
    猫抓 content-script.js 逻辑:
      - 检查页面 video/audio 元素的 currentSrc
    """
    captured = []  # [(url, response_headers)]

    def on_response(response):
        try:
            u = response.url
            if _is_image_url(u) or _is_ad_url(u):
                return
            # 猫抓 findMedia: 检查扩展名 + content-type
            is_media, ext, ct = find_media(u, response.headers)
            if is_media:
                captured.append((u, response.headers, ext))
        except Exception:
            pass

    page.on("response", on_response)

    print(f"    访问 ep{ep_num}: {url}")
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=25000)
    except PlaywrightTimeout:
        pass
    except Exception as e:
        print(f"      访问错误: {e}")

    # 等待 m3u8 捕获 (猫抓: 轮询直到捕获流URL)
    for i in range(20):
        page.wait_for_timeout(1000)
        # 检查是否已捕获到 m3u8 流
        if any(is_stream_url(u) for u, _, _ in captured):
            break

    # 猫抓 content-script.js: 检查页面 video/audio 元素
    try:
        video_srcs = page.evaluate(get_video_state_js())
        for src in video_srcs:
            if src and not _is_image_url(src) and not _is_ad_url(src):
                captured.append((src, None, None))
    except Exception:
        pass

    # 提取标题 (在重定向前)
    title = None
    try:
        t = page.title()
        if t and "百度" not in t.lower():
            title = t
    except Exception:
        pass

    page.remove_listener("response", on_response)

    # 选择最佳URL (猫抓 _pick_best_url 逻辑)
    best, best_ext = pick_best_url([(u, h) for u, h, _ in captured])
    if best:
        print(f"      ✓ m3u8: {best[:80]}...")
    else:
        # 尝试从HTML内嵌查找
        try:
            html = page.content()
            m = re.search(r'(https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*)', html)
            if m:
                best = m.group(1)
                best_ext = "m3u8"
                print(f"      ✓ HTML内嵌m3u8: {best[:80]}...")
        except Exception:
            pass

    return best, title


def probe_series(playwright, series_id, entry_url):
    """探测一个节目的所有集数和 m3u8"""
    print("\n" + "=" * 70)
    print(f"探测节目 {series_id}: {entry_url}")
    print("=" * 70)

    browser = playwright.chromium.launch(headless=False)
    context = browser.new_context(
        user_agent=USER_AGENT,
        viewport={"width": 1920, "height": 1080},
        locale="zh-CN",
        ignore_https_errors=True,
    )
    context.add_init_script(STEALTH_JS)
    page = context.new_page()

    # Step 1: 访问入口页面, 捕获第1集 m3u8 + 获取集数列表
    entry_ep = int(entry_url.split("-")[1])
    m3u8_ep1, title = probe_episode(page, entry_url, series_id, entry_ep)

    if title:
        title = re.sub(r'\s*[-—]\s*毒舌电影.*$', '', title).strip()
        SERIES_NAMES[series_id] = title
        print(f"  节目名: {title}")
    else:
        SERIES_NAMES[series_id] = f"节目{series_id}"
        print(f"  节目名: (未获取, 使用默认)")

    # Step 2: 获取页面HTML, 解析集数链接
    try:
        html = page.content()
    except Exception:
        html = ""

    # 如果页面被重定向到百度, 保存的HTML无用, 但我们已有 m3u8
    safe_title = re.sub(r'[\\/:*?"<>|]', '_', SERIES_NAMES.get(series_id, series_id))
    html_path = os.path.join(PROBE_DIR, f"page_{series_id}.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)

    episodes = find_episode_links(html, series_id)
    print(f"  页面找到 {len(episodes)} 个集数链接")

    # 如果没找到集数链接, 尝试从已知的URL模式推断
    # dushe07 的 play URL: /play/SID-EP-SRC.html
    # 我们知道入口EP, 需要找到所有EP
    if not episodes:
        # 尝试从HTML中的其他地方找集数信息
        # 猫抓 content-script getPage 逻辑: 直接取页面HTML
        print(f"  页面可能被重定向, 尝试从入口URL推断集数范围...")
        # 从之前的探测知道大概集数, 用API方式获取
        episodes = try_get_episodes_from_api(page, series_id, entry_url, entry_ep)

    # Step 3: 逐集探测 m3u8
    episode_m3u8s = {}
    if m3u8_ep1:
        episode_m3u8s[entry_ep] = m3u8_ep1

    for ep, ep_url, src in episodes:
        if ep == entry_ep and m3u8_ep1:
            continue  # 已有
        # 访问每集页面
        m3u8, _ = probe_episode(page, ep_url, series_id, ep)
        if m3u8:
            episode_m3u8s[ep] = m3u8
        else:
            print(f"      ✗ ep{ep} 未捕获到m3u8")

    browser.close()

    result = {
        "series_id": series_id,
        "title": SERIES_NAMES.get(series_id, ""),
        "entry_url": entry_url,
        "total_found": len(episodes),
        "episodes": [{"ep": ep, "url": u, "src": s} for ep, u, s in episodes],
        "episode_m3u8s": episode_m3u8s,
        "m3u8_count": len(episode_m3u8s),
    }

    result_path = os.path.join(PROBE_DIR, f"result_{series_id}.json")
    with open(result_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"\n  结果: {len(episode_m3u8s)}/{len(episodes)} 集有m3u8")
    return result


def try_get_episodes_from_api(page, series_id, entry_url, entry_ep):
    """尝试从API或页面JS获取集数列表

    dushe07 网站可能通过Ajax加载集数列表, 拦截XHR请求
    """
    episodes = []
    api_responses = []

    def on_response(response):
        u = response.url
        if "api" in u or "vod" in u or "play" in u.lower():
            try:
                body = response.text()
                if "play" in body and series_id in body:
                    api_responses.append(body)
            except Exception:
                pass

    page.on("response", on_response)

    # 尝试访问详情页
    detail_urls = [
        f"https://www.dushe07.com/movie/{series_id}.html",
        f"https://www.dushe07.com/vod/{series_id}.html",
        f"https://www.dushe07.com/index.php?m=vod-play-id-{series_id}-src-1-num-1.html",
    ]

    for durl in detail_urls:
        try:
            page.goto(durl, wait_until="domcontentloaded", timeout=15000)
            page.wait_for_timeout(3000)
            html = page.content()
            eps = find_episode_links(html, series_id)
            if eps:
                episodes = eps
                break
        except Exception:
            continue

    page.remove_listener("response", on_response)

    # 如果还是没找到, 从API响应中解析
    if not episodes and api_responses:
        for body in api_responses:
            # 查找 /play/SID-EP-SRC.html 模式
            eps = find_episode_links(body, series_id)
            if eps:
                episodes = eps
                break

    return episodes


def download_episode(ep, m3u8_url, series_name, series_id):
    """下载单集: yt-dlp 并行下载 → ffmpeg 封装为标准 MP4

    与 _batch_download_all.py 相同的流程
    """
    tmp_out = os.path.join(DOWNLOAD_DIR, f"{series_id}_ep{ep:02d}.%(ext)s")
    # 清理残留
    for f in glob.glob(os.path.join(DOWNLOAD_DIR, f"{series_id}_ep{ep:02d}.*")):
        try:
            os.remove(f)
        except OSError:
            pass

    # Step 1: yt-dlp hlsnative 并行下载
    cmd = [
        sys.executable, "-m", "yt_dlp",
        "--no-playlist",
        "-f", "best",
        "--concurrent-fragments", str(CONCURRENT_FRAGMENTS),
        "--hls-use-mpegts",
        "--no-overwrites",
        "--no-check-certificates",
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

        # Step 2: ffmpeg 重新封装为标准 MP4 (moov atom 前置)
        out_dir = os.path.join(ROOT, series_name)
        os.makedirs(out_dir, exist_ok=True)
        dst = os.path.join(out_dir, f"{series_name}_第{ep:02d}集.mp4")
        if os.path.exists(dst):
            os.remove(dst)
        ff_cmd = [
            FFMPEG, "-y",
            "-i", src,
            "-c", "copy",
            "-movflags", "+faststart",
            dst,
        ]
        ff_result = subprocess.run(ff_cmd, capture_output=True, text=True, timeout=600)
        try:
            os.remove(src)
        except OSError:
            pass
        if ff_result.returncode != 0:
            err = (ff_result.stderr or "").strip().split("\n")[-1][:200]
            return ep, False, f"ffmpeg: {err}"
        size_mb = os.path.getsize(dst) / (1024 * 1024)

        # 验证文件
        probe_cmd = [
            FFPROBE, "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", dst
        ]
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
    print("猫抓引擎驱动 - 4个新节目探测+下载")
    print("=" * 70)

    # Phase 1: 探测所有节目
    all_results = {}
    with sync_playwright() as p:
        for sid, url in SERIES_URLS.items():
            result = probe_series(p, sid, url)
            all_results[sid] = result

    # 保存总结果
    summary_path = os.path.join(PROBE_DIR, "all_results.json")
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)

    # Phase 2: 下载所有集数
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

    print("\n" + "=" * 70)
    print(f"下载完成: 成功 {total_ok}, 失败 {total_fail}")
    print("=" * 70)

    # Phase 3: 验证
    print("\nPhase 3: 验证所有视频")
    for sid, result in all_results.items():
        series_name = result.get("title") or f"节目{sid}"
        out_dir = os.path.join(ROOT, series_name)
        if not os.path.exists(out_dir):
            continue
        files = sorted(glob.glob(os.path.join(out_dir, "*.mp4")))
        print(f"\n[{sid}] {series_name}: {len(files)} 个文件")
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
            print(f"  {status} {os.path.basename(f)}: {size_mb:.1f}MB, {dur:.0f}s")


if __name__ == "__main__":
    main()
