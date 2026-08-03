"""
多节目探测脚本 - 对4个新URL执行猫抓工作流:
1. 访问每个URL, 获取页面标题(节目名)和HTML
2. 从HTML中解析所有集数链接 (/play/{sid}-{src}-{pid}.html)
3. 逐集访问, 通过网络拦截捕获 m3u8 URL (复用 browser_extractor 逻辑)
4. 输出 JSON 供下载脚本使用
"""
import os
import re
import sys
import json
import time

sys.path.insert(0, r"C:\Users\xx\Desktop\reclip-main")
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
from browser_extractor import VIDEO_URL_PATTERNS, _is_image_url, _is_ad_url, _is_stream_url

# 4个新URL (其中一个重复, 去重后4个独立节目)
TARGET_URLS = [
    "https://www.dushe07.com/play/26390-41-936828.html",
    "https://www.dushe07.com/play/240994-41-987230.html",
    "https://www.dushe07.com/play/216800-41-988596.html",
    "https://www.dushe07.com/play/239430-41-936705.html",
]

OUT_DIR = r"C:\Users\xx\Desktop\reclip-main\cat-catch-master\_probe_multi"
os.makedirs(OUT_DIR, exist_ok=True)

stealth_js = """
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


def parse_series_id(url):
    """从 /play/{sid}-{src}-{pid}.html 提取 series id"""
    m = re.search(r'/play/(\d+)-(\d+)-(\d+)\.html', url)
    if m:
        return m.group(1), m.group(2), m.group(3)
    return None, None, None


def find_episode_links(html, series_id):
    """从HTML中找到该series的所有集数链接
    
    URL格式: /play/{sid}-{src}-{pid}.html
    其中 sid=series id, src=source线路(固定为41), pid=play id
    集数通过页面上的链接顺序或文本确定
    """
    # 匹配所有 play 链接
    pat = re.compile(
        r'href=["\'](/play/(\d+)-(\d+)-(\d+)\.html)["\']',
        re.IGNORECASE,
    )
    matches = pat.findall(html)
    links = []
    seen_pids = set()
    for full_path, sid, src, pid in matches:
        if sid != series_id:
            continue
        if pid in seen_pids:
            continue
        seen_pids.add(pid)
        url = "https://www.dushe07.com" + full_path
        links.append({"url": url, "src": src, "pid": pid})
    return links


def find_m3u8_in_html(html):
    """直接从HTML/JS中提取m3u8 URL (cat-catch内容脚本思路)"""
    results = []
    for pattern in VIDEO_URL_PATTERNS:
        for m in pattern.finditer(html):
            url = m.group(1)
            if not _is_image_url(url) and not _is_ad_url(url):
                results.append(url)
    # 去重保序
    return list(dict.fromkeys(results))


def extract_series_info(page, url, series_id, source_id):
    """访问一个URL, 获取节目名和所有集数链接, 并捕获m3u8"""
    captured_urls = []

    def handle_request(request):
        u = request.url
        if _is_image_url(u):
            return
        for pattern in VIDEO_URL_PATTERNS:
            m = pattern.search(u)
            if m:
                captured_urls.append(m.group(1))
                break

    page.on("request", handle_request)

    print(f"\n  访问: {url}")
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=25000)
    except PlaywrightTimeout:
        print("  超时, 继续等待...")
    except Exception as e:
        print(f"  错误: {e}")
        return None

    # 等待页面加载和JS执行
    page.wait_for_timeout(6000)

    title = ""
    try:
        title = page.title()
        if "baidu" in title.lower():
            print("  -> 重定向到百度, 跳过")
            return None
    except Exception:
        pass

    html = page.content()
    
    # 保存HTML供调试
    safe_name = re.sub(r'[\\/:*?"<>|]', '_', title or series_id)
    with open(os.path.join(OUT_DIR, f"page_{series_id}.html"), "w", encoding="utf-8") as f:
        f.write(html)

    # 查找集数链接
    episodes = find_episode_links(html, series_id)
    print(f"  标题: {title!r}")
    print(f"  找到 {len(episodes)} 个集数链接")

    # 尝试从HTML中直接提取m3u8
    html_m3u8 = find_m3u8_in_html(html)
    if html_m3u8:
        print(f"  HTML内嵌m3u8: {html_m3u8[:3]}")

    # 等待网络捕获m3u8
    for _ in range(10):
        page.wait_for_timeout(1000)
        if any(_is_stream_url(u) for u in captured_urls):
            break

    # 合并HTML和网络捕获的m3u8
    all_urls = list(dict.fromkeys(html_m3u8 + captured_urls))
    m3u8_urls = [u for u in all_urls if ".m3u8" in u.lower() and not _is_ad_url(u)]
    
    print(f"  网络捕获URL: {len(captured_urls)}个, m3u8: {len(m3u8_urls)}个")
    for u in m3u8_urls[:3]:
        print(f"    m3u8: {u}")

    # 移除事件监听器
    page.remove_listener("request", handle_request)

    return {
        "series_id": series_id,
        "title": title,
        "episodes": episodes,
        "current_m3u8": m3u8_urls[0] if m3u8_urls else None,
        "all_m3u8": m3u8_urls,
    }


def extract_episode_m3u8(page, url, timeout=18000):
    """访问单集页面, 捕获其m3u8 URL"""
    captured = []

    def handle_request(request):
        u = request.url
        if _is_image_url(u):
            return
        for pattern in VIDEO_URL_PATTERNS:
            m = pattern.search(u)
            if m:
                captured.append(m.group(1))
                break

    page.on("request", handle_request)
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=timeout)
    except PlaywrightTimeout:
        pass
    except Exception as e:
        print(f"      错误: {e}")
        page.remove_listener("request", handle_request)
        return None

    # 轮询等待m3u8
    for _ in range(12):
        page.wait_for_timeout(1000)
        streams = [u for u in captured if _is_stream_url(u)]
        if streams:
            page.remove_listener("request", handle_request)
            return streams[0]

    # 也检查HTML内嵌
    try:
        html = page.content()
        html_m3u8 = find_m3u8_in_html(html)
        if html_m3u8:
            page.remove_listener("request", handle_request)
            return html_m3u8[0]
    except Exception:
        pass

    page.remove_listener("request", handle_request)
    return None


def probe_all():
    results = {}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
            locale="zh-CN",
        )
        context.add_init_script(stealth_js)
        page = context.new_page()

        for url in TARGET_URLS:
            series_id, source_id, play_id = parse_series_id(url)
            if not series_id:
                print(f"\n无法解析URL: {url}")
                continue

            print(f"\n{'='*60}")
            print(f"探测节目 series_id={series_id}")
            print(f"{'='*60}")

            info = extract_series_info(page, url, series_id, source_id)
            if not info:
                print("  探测失败, 跳过")
                results[series_id] = None
                continue

            # 如果有多个集数, 逐集提取m3u8
            episodes = info["episodes"]
            if len(episodes) <= 1:
                # 只有一集, 用已捕获的m3u8
                info["episode_m3u8s"] = {1: info["current_m3u8"]} if info["current_m3u8"] else {}
            else:
                print(f"\n  开始逐集提取m3u8 ({len(episodes)}集)...")
                info["episode_m3u8s"] = {}
                for i, ep in enumerate(episodes, 1):
                    ep_url = ep["url"]
                    print(f"    [{i}/{len(episodes)}] {ep_url}")
                    m3u8 = extract_episode_m3u8(page, ep_url)
                    if m3u8:
                        info["episode_m3u8s"][i] = m3u8
                        print(f"      OK: {m3u8}")
                    else:
                        print(f"      FAIL: 未捕获到m3u8")

            results[series_id] = info

        browser.close()

    # 保存结果
    out_file = os.path.join(OUT_DIR, "probe_multi_result.json")
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print(f"\n{'='*60}")
    print("探测完成, 结果保存到:", out_file)
    print(f"{'='*60}")
    for sid, info in results.items():
        if not info:
            print(f"  {sid}: 失败")
            continue
        title = info.get("title", "未知")
        eps = info.get("episode_m3u8s", {})
        ok = sum(1 for v in eps.values() if v)
        print(f"  {title} (id={sid}): {ok}/{len(eps)}集 m3u8已捕获")

    return results


if __name__ == "__main__":
    probe_all()
