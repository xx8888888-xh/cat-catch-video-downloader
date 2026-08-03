"""
下载新URL的视频 - 使用已捕获的m3u8
先以series_id为临时文件夹名下载, 后续重命名
"""
import os, sys, glob, subprocess, time
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = r"C:\Users\xx\Desktop\reclip-main"
FFMPEG = r"D:\software\ffmpeg\bin\ffmpeg.exe"
DOWNLOAD_DIR = os.path.join(ROOT, "downloads")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)
CONCURRENT_FRAGMENTS = 8

# 已捕获的m3u8 (series_id -> m3u8_url)
M3U8_MAP = {
    "26390": "https://142.248.97.96:21306/data7/jvods/hls/dhz/26/25453/25010223/3_26390_937124/1280/index.m3u8?appId=dsdy&sign=2fd7edf321e26ed2d9aa2032cb4529f3&timestamp=1785766329&ref=0",
    "240994": "https://208.69.102.160:21306/data3/jvods/hls/dhz/28/27402/25010413/3_240994_987387/1280/index.m3u8?appId=dsdy&sign=15dfd882376c11513aedb044f38ce185&timestamp=1785766335&ref=0",
    "239430": "https://142.248.96.244:21306/data6/jvods/hls/dhz/26/25448/25010223/3_239430_936997/1280/index.m3u8?appId=dsdy&sign=54156ead6a1c7c48d4d0f5f176071c42&timestamp=1785766359&ref=0",
}


def download_one(series_id, m3u8_url):
    """下载单个视频"""
    tmp_out = os.path.join(DOWNLOAD_DIR, f"new_{series_id}.%(ext)s")
    # 清理残留
    for f in glob.glob(os.path.join(DOWNLOAD_DIR, f"new_{series_id}.*")):
        try:
            os.remove(f)
        except OSError:
            pass

    cmd = [
        sys.executable, "-m", "yt_dlp",
        "--no-playlist",
        "-f", "best",
        "--concurrent-fragments", str(CONCURRENT_FRAGMENTS),
        "--hls-use-mpegts",
        "--no-overwrites",
        "--no-check-certificates",
        "--add-headers", "Referer:https://www.dushe07.com/",
        "--add-headers", "User-Agent:Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "--ffmpeg-location", os.path.dirname(FFMPEG),
        "-o", tmp_out,
        m3u8_url,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=1200)
        if result.returncode != 0:
            err = (result.stderr or "").strip().split("\n")[-1][:200]
            return series_id, False, f"yt-dlp: {err}"
        files = glob.glob(os.path.join(DOWNLOAD_DIR, f"new_{series_id}.*"))
        if not files:
            return series_id, False, "no output file"
        src = max(files, key=os.path.getmtime)

        # ffmpeg封装
        out_dir = os.path.join(ROOT, f"_{series_id}")
        os.makedirs(out_dir, exist_ok=True)
        dst = os.path.join(out_dir, f"{series_id}.mp4")
        if os.path.exists(dst):
            os.remove(dst)
        ff_cmd = [FFMPEG, "-y", "-i", src, "-c", "copy", "-movflags", "+faststart", dst]
        ff_result = subprocess.run(ff_cmd, capture_output=True, text=True, timeout=600)
        try:
            os.remove(src)
        except OSError:
            pass
        if ff_result.returncode != 0:
            return series_id, False, f"ffmpeg: {(ff_result.stderr or '')[-200:]}"
        size_mb = os.path.getsize(dst) / (1024 * 1024)
        probe_cmd = [FFMPEG.replace("ffmpeg.exe", "ffprobe.exe"),
                     "-v", "error", "-show_entries", "format=duration",
                     "-of", "default=noprint_wrappers=1:nokey=1", dst]
        probe = subprocess.run(probe_cmd, capture_output=True, text=True, timeout=30)
        duration = float(probe.stdout.strip()) if probe.stdout.strip() else 0
        return series_id, True, f"{size_mb:.1f}MB, {duration:.0f}s"
    except subprocess.TimeoutExpired:
        return series_id, False, "timeout"
    except Exception as e:
        return series_id, False, str(e)


def main():
    print(f"下载 {len(M3U8_MAP)} 个视频...")
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {executor.submit(download_one, sid, url): sid for sid, url in M3U8_MAP.items()}
        for future in as_completed(futures):
            sid, ok, msg = future.result()
            print(f"  {sid}: {'OK' if ok else 'FAIL'} - {msg}")


if __name__ == "__main__":
    main()
