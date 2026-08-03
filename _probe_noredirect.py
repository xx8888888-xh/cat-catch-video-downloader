"""
阻止百度重定向, 获取原始页面内容
使用 page.route 阻止对 baidu.com 的请求
"""
import os, re, sys, json
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

OUT_DIR = r"C:\Users\xx\Desktop\reclip-main\cat-catch-master\_probe_multi"
os.makedirs(OUT_DIR, exist_ok=True)

URLS = {
    "26390": "https://www.dushe07.com/play/26390-41-936828.html",
    "240994": "https://www.dushe07.com/play/240994-41-987230.html",
    "216800": "https://www.dushe07.com/play/216800-41-988596.html",
    "239430": "https://www.dushe07.com/play/239430-41-936705.html",
}

stealth_js = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN','zh','en']});
window.chrome = window.chrome || {runtime: {}};
Object.defineProperty(navigator, 'platform', {get: () => 'Win32'});
"""


def find_title_and_episodes(html, series_id):
    """从HTML中提取节目名和集数链接"""
    # 节目名: 尝试多种模式
    title = None
    title_patterns = [
        r'<h1[^>]*>([^<]{3,100})</h1>',
        r'<h2[^>]*>([^<]{3,100})</h2>',
        r'class="[^"]*title[^"]*"[^>]*>([^<]{3,80})<',
        r'property="og:title"\s+content="([^"]{3,100})"',
        r'<title>([^<]{3,100})</title>',
        r'"vod_name"\s*:\s*"([^"]{3,100})"',
        r'"title"\s*:\s*"([^"]{3,100})"',
        r'data-name="([^"]{3,100})"',
    ]
    for pat in title_patterns:
        m = re.search(pat, html)
        if m:
            t = m.group(1).strip()
            if "baidu" not in t.lower() and "百度" not in t and len(t) > 2:
                title = t
                break

    # 集数链接
    ep_pat = re.compile(r'/play/(\d+)-(\d+)-(\d+)\.html', re.IGNORECASE)
    matches = ep_pat.findall(html)
    links = []
    seen = set()
    for sid, mid, pid in matches:
        if sid != series_id:
            continue
        key = (mid, pid)
        if key in seen:
            continue
        seen.add(key)
        links.append({"mid": mid, "pid": pid})
    return title, links


def main():
    results = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
            locale="zh-CN",
        )
        context.add_init_script(stealth_js)
        page = context.new_page()

        # 阻止对 baidu.com 的请求 (防止重定向)
        def block_baidu(route, request):
            if "baidu" in request.url.lower():
                route.abort()
            else:
                route.continue_()

        page.route("**/*", block_baidu)

        for sid, url in URLS.items():
            print(f"\n{'='*50}")
            print(f"探测 {sid}: {url}")
            print(f"{'='*50}")
            try:
                # 使用 commit: 页面刚收到响应, JS还没执行
                page.goto(url, wait_until="commit", timeout=15000)
            except PlaywrightTimeout:
                print("  commit超时, 尝试domcontentloaded...")
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=15000)
                except Exception:
                    pass
            except Exception as e:
                print(f"  导航错误: {e}")

            # 立即获取HTML
            try:
                html = page.content()
                print(f"  HTML长度: {len(html)}")
                # 保存
                with open(os.path.join(OUT_DIR, f"noredirect_{sid}.html"), "w", encoding="utf-8") as f:
                    f.write(html)

                title, links = find_title_and_episodes(html, sid)
                print(f"  节目名: {title!r}")
                print(f"  集数链接: {len(links)}")
                for link in links[:30]:
                    print(f"    mid={link['mid']} pid={link['pid']}")
                results[sid] = {"title": title, "episodes": links}

                # 检查页面标题
                try:
                    page_title = page.title()
                    print(f"  页面标题: {page_title!r}")
                except Exception:
                    pass

            except Exception as e:
                print(f"  获取内容错误: {e}")
                results[sid] = None

            # 等待一下让JS执行 (可能有异步加载的集数列表)
            page.wait_for_timeout(5000)
            try:
                html2 = page.content()
                if len(html2) != len(html):
                    print(f"  HTML变化: {len(html)} -> {len(html2)}")
                    with open(os.path.join(OUT_DIR, f"noredirect2_{sid}.html"), "w", encoding="utf-8") as f:
                        f.write(html2)
                    title2, links2 = find_title_and_episodes(html2, sid)
                    if title2 and not title:
                        print(f"  更新节目名: {title2!r}")
                        results[sid] = {"title": title2, "episodes": links2}
                    if links2 and not links:
                        print(f"  更新集数链接: {len(links2)}")
                        results[sid]["episodes"] = links2
            except Exception:
                pass

        browser.close()

    # 保存结果
    with open(os.path.join(OUT_DIR, "noredirect_result.json"), "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print(f"\n{'='*50}")
    print("汇总:")
    for sid, r in results.items():
        if r:
            print(f"  {sid}: name={r.get('title')}, eps={len(r.get('episodes', []))}")
        else:
            print(f"  {sid}: 失败")


if __name__ == "__main__":
    main()
