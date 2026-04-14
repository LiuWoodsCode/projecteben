from pathlib import Path
import stat

p = Path("main.py")
s = p.stat()

print("=== Raw stat fields ===")

print("st_mode:", s.st_mode)
print("st_ino:", s.st_ino)
print("st_dev:", s.st_dev)
print("st_nlink:", s.st_nlink)
print("st_uid:", s.st_uid)
print("st_gid:", s.st_gid)
print("st_size:", s.st_size)
print("st_atime:", s.st_atime)
print("st_mtime:", s.st_mtime)
print("st_ctime:", s.st_ctime)

# Linux-specific / commonly available extras
print("st_blksize:", getattr(s, "st_blksize", None))
print("st_blocks:", getattr(s, "st_blocks", None))
print("st_rdev:", getattr(s, "st_rdev", None))

# Nanosecond-resolution timestamps (Python 3.3+)
print("st_atime_ns:", getattr(s, "st_atime_ns", None))
print("st_mtime_ns:", getattr(s, "st_mtime_ns", None))
print("st_ctime_ns:", getattr(s, "st_ctime_ns", None))

# Birth/creation time (may not exist on Linux)
print("st_birthtime:", getattr(s, "st_birthtime", None))
print("st_birthtime_ns:", getattr(s, "st_birthtime_ns", None))