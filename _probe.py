"""
Probe dushe07.com to:
1. Find the series detail page and total episode count
2. Test m3u8 extraction on episode 1 (network + HTML)
3. Save HTML for inspection
"""
import os
import re
import sys
import json
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

# Reuse the parent project's extractor module
sys.path.insert(0, r"C:\Users\xx\Desktop\reclip-main")
from browser_extractor import VIDEO_URL_PATTERNS, _is_image_url, _is_ad_url, _is_stream_url

SERIES_ID = "239643"
OUT_DIR = r"C:\Users\xx\Desktop\reclip-main\downloads"
HTML_DUMP = r"C:\Users\xx\Desktop\reclip-main\cat-catch-master\_probe_html"
os.makedirs(OUT_DIR, exist_ok=True)
os.makedirs(HTML_DUMP, exist_ok=True)

DETAIL_CANDIDATES = [
    f"https://www.dushe07.com/movie/{SERIES_ID}.html",
    f"https://www.dushe07.com/vod/{SERIES_ID}.html",
    f"https://www.dushe07.com/index.php?m=vod-play-id-{SERIES_ID}-src-1-num-1.html",
    f"https://www.dushe07.com/play/{SERIES_ID}-1-947653.html",
]

stealth_js = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN','zh','en']});
window.chrome = window.chrome || {runtime: {}};
Object.defineProperty(navigator, 'platform', {get: () => 'Win32'});
"""


def find_episode_links(html, base_url="https://www.dushe07.com"):
    """Find all episode links matching /play/SID-EP-SRC.html"""
    pat = re.compile(
        r'href=["\'](/play/(\d+)-(\d+)-(\d+)\.html)["\']',
        re.IGNORECASE,
    )
    matches = pat.findall(html)
    links = []
    seen = set()
    for full_path, sid, ep, src in matches:
        if sid != SERIES_ID:
            continue
        url = base_url + full_path
        ep_int = int(ep)
        if ep_int in seen:
            continue
        seen.add(ep_int)
        links.append((ep_int, url, src))
    links.sort(key=lambda x: x[0])
    return links


def probe():
    captured_urls = []
    page_title = ""
    links = []

    def handle_request(request):
        url = request.url
        if _is_image_url(url):
            return
        for pattern in VIDEO_URL_PATTERNS:
            match = pattern.search(url)
            if match:
                captured_urls.append(match.group(1))
                break

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
            locale="zh-CN",
        )
        context.add_init_script(stealth_js)
        page = context.new_page()
        page.on("request", handle_request)

        print("=" * 60)
        print("[A] Looking for series detail page (episode list)")
        print("=" * 60)
        found_detail = False
        for url in DETAIL_CANDIDATES:
            print(f"\nTrying: {url}")
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=20000)
            except PlaywrightTimeout:
                print("  timeout (continuing)")
            except Exception as e:
                print(f"  error: {e}")
                continue
            page.wait_for_timeout(5000)
            try:
                t = page.title()
                print(f"  title: {t!r}")
                if "baidu" in t.lower():
                    print("  -> redirected to baidu, skip")
                    continue
            except Exception:
                pass
            html = page.content()
            links = find_episode_links(html)
            print(f"  found {len(links)} episode links")
            if links:
                found_detail = True
                with open(os.path.join(HTML_DUMP, f"detail_{SERIES_ID}.html"), "w", encoding="utf-8") as f:
                    f.write(html)
                print(f"  Episodes:")
                for ep, u, src in links:
                    print(f"    ep{ep} (src={src}): {u}")
                m = re.search(r'共\s*(\d+)\s*集', html)
                if m:
                    print(f"  Stated total: {m.group(1)} episodes")
                break

        if not found_detail:
            print("\n[B] No detail page found, trying episode 1 page for episode list")
            url = f"https://www.dushe07.com/play/{SERIES_ID}-1-947653.html"
            print(f"Trying: {url}")
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=20000)
            except PlaywrightTimeout:
                print("  timeout (continuing)")
            except Exception as e:
                print(f"  error: {e}")
            page.wait_for_timeout(8000)
            html = page.content()
            with open(os.path.join(HTML_DUMP, f"play_ep1_{SERIES_ID}.html"), "w", encoding="utf-8") as f:
                f.write(html)
            links = find_episode_links(html)
            print(f"  found {len(links)} episode links on ep1 page")
            if links:
                for ep, u, src in links:
                    print(f"    ep{ep} (src={src}): {u}")

        try:
            page_title = page.title()
            if "baidu" in page_title.lower():
                page_title = ""
        except Exception:
            pass

        print("\n[C] Waiting for m3u8 capture...")
        for _ in range(15):
            page.wait_for_timeout(1000)
            if any(_is_stream_url(u) for u in captured_urls):
                break

        print(f"\n  Total captured URLs: {len(captured_urls)}")
        for u in captured_urls:
            print(f"    {u}")

        browser.close()

    clean = [u for u in dict.fromkeys(captured_urls) if not _is_image_url(u) and not _is_ad_url(u)]
    m3u8 = [u for u in clean if ".m3u8" in u.lower()]
    best = m3u8[0] if m3u8 else (clean[0] if clean else None)

    result = {
        "title": page_title,
        "episodes": [{"ep": ep, "url": u, "src": src} for ep, u, src in links],
        "captured_count": len(captured_urls),
        "captured_urls": captured_urls,
        "best_m3u8": best,
    }
    with open(os.path.join(HTML_DUMP, "probe_result.json"), "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print("\n" + "=" * 60)
    print("PROBE RESULT")
    print("=" * 60)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    probe()
