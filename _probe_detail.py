"""
探测详情页获取节目名和集数列表
尝试多种URL格式, 使用更强的反检测
"""
import os, re, sys, json, time
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

sys.path.insert(0, r"C:\Users\xx\Desktop\reclip-main")
from browser_extractor import VIDEO_URL_PATTERNS, _is_image_url, _is_ad_url, _is_stream_url

SERIES_IDS = ["26390", "240994", "216800", "239430"]
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
Object.defineProperty(navigator, 'maxTouchPoints', {get: () => 0});
Object.defineProperty(navigator, 'vendor', {get: () => 'Google Inc.'});
Object.defineProperty(navigator, 'appVersion', {get: () => '5.0 (Windows NT 10.0; Win64; x64)'});
window.outerWidth = 1920;
window.outerHeight = 1080;
"""

def find_all_play_links(html, series_id):
    """找到所有/play/{sid}-*-{pid}.html链接"""
    pat = re.compile(r'/play/(\d+)-(\d+)-(\d+)\.html', re.IGNORECASE)
    matches = pat.findall(html)
    links = []
    seen = set()
    for sid, mid, pid in matches:
        if sid != series_id:
            continue
        key = (mid, pid)
        if key in seen:
            continue
        seen.add(key)
        links.append({"mid": mid, "pid": pid, "url": f"https://www.dushe07.com/play/{sid}-{mid}-{pid}.html"})
    return links


def find_title_in_html(html):
    """从HTML中提取节目名"""
    patterns = [
        r'<title>([^<]{3,100})</title>',
        r'<h1[^>]*>([^<]{3,100})</h1>',
        r'<h2[^>]*class="[^"]*title[^"]*"[^>]*>([^<]{3,100})</h2>',
        r'class="[^"]*title[^"]*"[^>]*>([^<]{3,100})<',
        r'<a[^>]*class="[^"]*active[^"]*"[^>]*>([^<]{3,100})</a>',
        r'property="og:title"\s+content="([^"]{3,100})"',
        r'"title"\s*:\s*"([^"]{3,100})"',
        r'"name"\s*:\s*"([^"]{3,100})"',
        r'"vod_name"\s*:\s*"([^"]{3,100})"',
    ]
    for pat in patterns:
        m = re.search(pat, html)
        if m:
            title = m.group(1).strip()
            if "baidu" not in title.lower() and "百度" not in title:
                return title
    return None


def probe_detail(page, series_id):
    """探测一个series的详情页"""
    # 尝试多种URL格式
    candidates = [
        f"https://www.dushe07.com/movie/{series_id}.html",
        f"https://www.dushe07.com/vod/{series_id}.html",
        f"https://www.dushe07.com/index.php?m=vod-play-id-{series_id}-src-1-num-1.html",
        f"https://www.dushe07.com/index.php?m=vod-detail-id-{series_id}.html",
        f"https://www.dushe07.com/v_show/id_{series_id}.html",
    ]

    for url in candidates:
        print(f"\n  尝试: {url}")
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=20000)
        except PlaywrightTimeout:
            print("  超时, 继续...")
        except Exception as e:
            print(f"  错误: {e}")
            continue

        page.wait_for_timeout(4000)

        try:
            title = page.title()
            print(f"  页面标题: {title!r}")
            if "baidu" in title.lower():
                print("  -> 重定向到百度, 尝试下一个URL")
                continue
        except Exception:
            pass

        html = page.content()
        # 保存HTML
        with open(os.path.join(OUT_DIR, f"detail_{series_id}.html"), "w", encoding="utf-8") as f:
            f.write(html)

        # 查找节目名
        series_name = find_title_in_html(html)
        if series_name:
            print(f"  节目名: {series_name!r}")

        # 查找集数链接
        links = find_all_play_links(html, series_id)
        print(f"  集数链接: {len(links)}")
        for link in links[:20]:
            print(f"    mid={link['mid']} pid={link['pid']}: {link['url']}")

        if series_name or links:
            return {"title": series_name, "episodes": links, "url": url}

    # 所有URL都失败了, 尝试访问play页面获取标题
    print(f"\n  详情页全部失败, 尝试play页面...")
    play_urls = {
        "26390": "https://www.dushe07.com/play/26390-41-936828.html",
        "240994": "https://www.dushe07.com/play/240994-41-987230.html",
        "216800": "https://www.dushe07.com/play/216800-41-988596.html",
        "239430": "https://www.dushe07.com/play/239430-41-936705.html",
    }
    url = play_urls.get(series_id)
    if url:
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=20000)
        except Exception:
            pass
        page.wait_for_timeout(3000)
        # 在redirect之前快速获取content
        try:
            html = page.content()
            series_name = find_title_in_html(html)
            if series_name:
                print(f"  从play页获取节目名: {series_name!r}")
            links = find_all_play_links(html, series_id)
            print(f"  play页集数链接: {len(links)}")
            return {"title": series_name, "episodes": links, "url": url}
        except Exception as e:
            print(f"  错误: {e}")

    return None


def main():
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

        for sid in SERIES_IDS:
            print(f"\n{'='*60}")
            print(f"探测详情页 series_id={sid}")
            print(f"{'='*60}")
            result = probe_detail(page, sid)
            results[sid] = result

        browser.close()

    # 保存结果
    out_file = os.path.join(OUT_DIR, "detail_result.json")
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print(f"\n{'='*60}")
    print("结果:")
    for sid, r in results.items():
        if r:
            print(f"  {sid}: name={r.get('title')}, episodes={len(r.get('episodes', []))}")
        else:
            print(f"  {sid}: 失败")


if __name__ == "__main__":
    main()
