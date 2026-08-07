"""
批量下载脚本 - 使用猫抓工作流(浏览器嗅探 m3u8 + yt-dlp 并行下载 + ffmpeg 封装)
- 浏览器逐集点击获取 HTML → 提取 m3u8 URL
- yt-dlp hlsnative 8 并发分片下载(pycryptodomex 解 AES-128)
- ffmpeg 重新封装为标准 MP4(faststart, moov atom 前置)
- 按节目名分文件夹,集数命名
"""
import os
import sys
import glob
import time
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed

# 基于脚本自身位置的可移植路径 (脚本位于项目根目录)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FFMPEG = r"D:\software\ffmpeg\bin\ffmpeg.exe"
DOWNLOAD_DIR = os.path.join(BASE_DIR, "downloads")
CONCURRENT_FRAGMENTS = 8
MAX_PARALLEL = 3

os.makedirs(DOWNLOAD_DIR, exist_ok=True)


def download_episode(ep, m3u8_url, series_name, total_eps=None):
    """下载单集: yt-dlp 并行分片下载 → ffmpeg 封装为标准 MP4"""
    tmp_out = os.path.join(DOWNLOAD_DIR, f"{series_name}_ep{ep:02d}.%(ext)s")
    # 清理残留
    for f in glob.glob(os.path.join(DOWNLOAD_DIR, f"{series_name}_ep{ep:02d}.*")):
        try:
            os.remove(f)
        except OSError:
            pass

    # Step 1: yt-dlp hlsnative 并行下载 (输出 .ts/.mp4)
    cmd = [
        sys.executable, "-m", "yt_dlp",
        "--no-playlist",
        "-f", "best",
        "--concurrent-fragments", str(CONCURRENT_FRAGMENTS),
        "--hls-use-mpegts",
        "--no-overwrites",
        "--add-headers", "Referer:https://www.dushe07.com/",
        "--add-headers", "User-Agent:Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "--ffmpeg-location", os.path.dirname(FFMPEG),
        "-o", tmp_out,
        m3u8_url,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=1200)
        if result.returncode != 0:
            err = (result.stderr or "").strip().split("\n")[-1][:200]
            return ep, False, f"yt-dlp: {err}"
        files = glob.glob(os.path.join(DOWNLOAD_DIR, f"{series_name}_ep{ep:02d}.*"))
        if not files:
            return ep, False, "no output file"
        src = max(files, key=os.path.getmtime)

        # Step 2: ffmpeg 重新封装为标准 MP4 (moov atom 前置)
        out_dir = os.path.join(DOWNLOAD_DIR, series_name)
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
        # 清理临时文件
        try:
            os.remove(src)
        except OSError:
            pass
        if ff_result.returncode != 0:
            err = (ff_result.stderr or "").strip().split("\n")[-1][:200]
            return ep, False, f"ffmpeg: {err}"
        size_mb = os.path.getsize(dst) / (1024 * 1024)
        # 验证文件
        probe_cmd = [FFMPEG.replace("ffmpeg.exe", "ffprobe.exe"),
                     "-v", "error", "-show_entries", "format=duration",
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


# 节目定义: (节目名, [(集数, m3u8_url), ...])
SERIES = {
    "不白吃的食神之旅": [
        (1,  "https://play.hhuus.com/play/QbYkjNKe/index.m3u8"),
        (2,  "https://play.hhuus.com/play/Pe9xqNxd/index.m3u8"),
        (3,  "https://play.hhuus.com/play/ZdP20Awa/index.m3u8"),
        (4,  "https://play.hhuus.com/play/vbmLK90a/index.m3u8"),
        (5,  "https://play.hhuus.com/play/NbWg50Jb/index.m3u8"),
        (6,  "https://play.hhuus.com/play/9avXWpmd/index.m3u8"),
        (7,  "https://play.hhuus.com/play/0dNZVB6d/index.m3u8"),
        (8,  "https://play.hhuus.com/play/7e5qmXBe/index.m3u8"),
        (9,  "https://play.hhuus.com/play/Le3okXMe/index.m3u8"),
        (10, "https://play.hhuus.com/play/xboNMlja/index.m3u8"),
        (11, "https://play.hhuus.com/play/oeENJpva/index.m3u8"),
        (12, "https://play.hhuus.com/play/7axZYx9d/index.m3u8"),
        (13, "https://play.hhuus.com/play/negBAEYd/index.m3u8"),
        (14, "https://play.hhuus.com/play/vbmLK9Oa/index.m3u8"),
    ],
}


def main():
    all_results = []
    for series_name, episodes in SERIES.items():
        print(f"\n{'='*60}")
        print(f"开始下载《{series_name}》共 {len(episodes)} 集")
        print(f"{'='*60}")
        start = time.time()
        with ThreadPoolExecutor(max_workers=MAX_PARALLEL) as executor:
            futures = {executor.submit(download_episode, ep, url, series_name): ep
                       for ep, url in episodes}
            for future in as_completed(futures):
                ep, ok, msg = future.result()
                status = "OK" if ok else "FAIL"
                print(f"  第{ep:02d}集: {status} - {msg}")
                all_results.append((series_name, ep, ok, msg))
        elapsed = time.time() - start
        success = sum(1 for s, _, ok, _ in all_results if s == series_name and ok)
        print(f"  《{series_name}》完成: {success}/{len(episodes)}, 耗时 {elapsed:.0f}s")

    print(f"\n{'='*60}")
    print("全部任务汇总")
    print(f"{'='*60}")
    total = len(all_results)
    total_ok = sum(1 for _, _, ok, _ in all_results if ok)
    print(f"总计: {total_ok}/{total} 成功")
    for series_name, ep, ok, msg in all_results:
        status = "OK" if ok else "FAIL"
        print(f"  {series_name} 第{ep:02d}集: {status} - {msg}")


if __name__ == "__main__":
    main()
