"""验证所有4个节目的下载结果"""
import os
import glob
import subprocess

FFPROBE = r"D:\software\ffmpeg\bin\ffprobe.exe"
# 基于脚本自身位置的可移植路径 (脚本位于项目根目录)
ROOT = os.path.dirname(os.path.abspath(__file__))
DOWNLOAD_DIR = os.path.join(ROOT, "downloads")

SERIES = [
    ("不白吃话山海经", 41),
    ("不白吃古诗词漫游记第二季", 39),
    ("不白吃古诗词漫游记", 46),
    ("不白吃古诗词漫游记第一季", 45),
]

print("=" * 70)
print("下载验证报告")
print("=" * 70)

total_ok = 0
total_files = 0
total_missing = 0

for name, expected in SERIES:
    d = os.path.join(DOWNLOAD_DIR, name)
    files = sorted(glob.glob(os.path.join(d, "*.mp4"))) if os.path.exists(d) else []
    print(f"\n[{name}] 期望 {expected} 集, 实际 {len(files)} 文件")

    ok = 0
    fail = 0
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

        if dur >= 60:
            ok += 1
        else:
            fail += 1
            print(f"  ✗ {os.path.basename(f)}: {size_mb:.1f}MB, {dur:.0f}s")

    missing = expected - len(files)
    total_ok += ok
    total_files += len(files)
    total_missing += missing

    print(f"  结果: {ok} 有效 / {len(files)} 文件 / {expected} 期望"
          + (f" ({missing} 缺失)" if missing > 0 else ""))

print(f"\n{'='*70}")
print(f"总计: {total_ok} 有效 / {total_files} 文件 / {total_missing} 缺失")
print(f"{'='*70}")
