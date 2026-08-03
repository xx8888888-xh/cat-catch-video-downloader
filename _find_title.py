import re, os, json

HTML_DIR = r"C:\Users\xx\Desktop\reclip-main\cat-catch-master\_probe_multi"
files = ["page_26390.html", "page_240994.html", "page_239430.html", "page_216800.html"]

# patterns for video metadata
patterns = [
    (r'"title"\s*:\s*"([^"]{3,100})"', "title"),
    (r'"name"\s*:\s*"([^"]{3,100})"', "name"),
    (r'"vod_name"\s*:\s*"([^"]{3,100})"', "vod_name"),
    (r'"videoTitle"\s*:\s*"([^"]{3,100})"', "videoTitle"),
    (r'"filename"\s*:\s*"([^"]{3,100})"', "filename"),
    (r'<h1[^>]*>([^<]{3,80})</h1>', "h1"),
    (r'<h2[^>]*>([^<]{3,80})</h2>', "h2"),
    (r'property="og:title"\s+content="([^"]{3,100})"', "og:title"),
    (r'player_aaaa\s*=\s*(\{[^}]+\})', "player_aaaa"),
]

for fname in files:
    fpath = os.path.join(HTML_DIR, fname)
    if not os.path.exists(fpath):
        continue
    html = open(fpath, encoding="utf-8", errors="ignore").read()
    print(f"\n--- {fname} ({len(html)} chars) ---")
    for pat, label in patterns:
        matches = re.findall(pat, html)
        for m in matches[:5]:
            if "baidu" in m.lower() or len(m) < 3:
                continue
            print(f"  {label}: {m!r}")
    # Also search for any URL with the series id that might have a title
    sid = fname.split("_")[1].split(".")[0]
    # Search for episode links that might indicate multi-episode
    ep_links = re.findall(r'/play/' + sid + r'-(\d+)-(\d+)\.html', html)
    if ep_links:
        print(f"  Episode links found: {len(ep_links)}")
        for src, pid in ep_links[:10]:
            print(f"    src={src} pid={pid}")
