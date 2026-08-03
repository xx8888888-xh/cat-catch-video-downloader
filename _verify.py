import os, glob, subprocess, sys

FFPROBE = r"D:\software\ffmpeg\bin\ffprobe.exe"
d = r"C:\Users\xx\Desktop\reclip-main\不白吃的食神之旅"
files = sorted(glob.glob(os.path.join(d, "*.mp4")))
print(f"Total: {len(files)}/14")
print("-" * 60)
ok = 0
for f in files:
    size_mb = os.path.getsize(f) / 1048576
    try:
        r = subprocess.run(
            [FFPROBE, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", f],
            capture_output=True, text=True, timeout=30)
        dur = float(r.stdout.strip()) if r.stdout.strip() else 0
    except Exception as e:
        dur = -1
    status = "OK" if dur >= 60 else "FAIL"
    if status == "OK":
        ok += 1
    print(f"  {status} {os.path.basename(f)}: {size_mb:.1f}MB, {dur:.0f}s")
print("-" * 60)
print(f"Result: {ok}/{len(files)} valid")
