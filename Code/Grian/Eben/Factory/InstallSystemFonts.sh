#!/usr/bin/env bash
set -euo pipefail

if (( EUID != 0 )); then
    echo "Run this installer as root (sudo)." >&2
    exit 1
fi

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
repo_dir="$(cd -- "$script_dir/../../../.." && pwd -P)"
source_dir="$repo_dir/Code/Salvato/Fonts"
target_dir=/usr/local/share/fonts/projecteben
font_name='Inclusive Sans Regular'

for command in python3 fc-cache fc-match; do
    command -v "$command" >/dev/null 2>&1 || { echo "Missing required command: $command" >&2; exit 1; }
done
[[ -d "$source_dir" ]] || { echo "Missing font folder: $source_dir" >&2; exit 1; }
[[ -f "$source_dir/InclusiveSans/InclusiveSans-Regular.ttf" ]] || {
    echo "Missing Inclusive Sans Regular font." >&2
    exit 1
}

count=0
while IFS= read -r -d '' font; do
    relative="${font#"$source_dir"/}"
    install -D -m 644 -- "$font" "$target_dir/$relative"
    (( count += 1 ))
done < <(find "$source_dir" -type f \( -iname '*.ttf' -o -iname '*.otf' -o -iname '*.ttc' -o -iname '*.otc' \) -print0)
(( count > 0 )) || { echo "No font files found in $source_dir" >&2; exit 1; }

# Give applications using Fontconfig the same sans-serif default.
install -d -m 755 /etc/fonts/conf.d
cat > /etc/fonts/conf.d/99-projecteben-inclusive-sans.conf <<'EOF'
<?xml version="1.0"?>
<!DOCTYPE fontconfig SYSTEM "fonts.dtd">
<fontconfig>
  <alias binding="strong">
    <family>sans-serif</family>
    <prefer><family>Inclusive Sans</family></prefer>
  </alias>
</fontconfig>
EOF
chmod 644 /etc/fonts/conf.d/99-projecteben-inclusive-sans.conf
fc-cache -f "$target_dir"

# Update only gtk-font-name inside [Settings], keeping unrelated GTK options.
python3 - "$font_name" <<'PY'
import os
import pathlib
import pwd
import re
import sys
import tempfile

font_name = sys.argv[1]
section_re = re.compile(r'^\s*\[([^]]+)\]\s*$')
key_re = re.compile(r'^\s*gtk-font-name\s*=')

def update(path, uid=None, gid=None):
    missing = []
    parent = path.parent
    while not parent.exists():
        missing.append(parent)
        parent = parent.parent
    for directory in reversed(missing):
        directory.mkdir()
        if uid is not None:
            os.chown(directory, uid, gid)
    lines = path.read_text().splitlines(keepends=True) if path.exists() else []
    output = []
    in_settings = False
    found_section = False
    wrote_key = False
    for line in lines:
        match = section_re.match(line)
        if match:
            if in_settings and not wrote_key:
                output.append(f'gtk-font-name={font_name}\n')
                wrote_key = True
            in_settings = match.group(1).strip().lower() == 'settings'
            found_section |= in_settings
        if in_settings and key_re.match(line):
            if not wrote_key:
                output.append(f'gtk-font-name={font_name}\n')
                wrote_key = True
            continue
        output.append(line)
    if not wrote_key:
        if not found_section:
            if output and not output[-1].endswith('\n'):
                output.append('\n')
            output.append('[Settings]\n')
        output.append(f'gtk-font-name={font_name}\n')
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o644
    fd, temporary = tempfile.mkstemp(prefix='.settings.', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.writelines(output)
        os.chmod(temporary, mode)
        if uid is not None:
            os.chown(temporary, uid, gid)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)

for version in ('3.0', '4.0'):
    update(pathlib.Path(f'/etc/gtk-{version}/settings.ini'))
    update(pathlib.Path(f'/etc/skel/.config/gtk-{version}/settings.ini'))

for user in pwd.getpwall():
    if user.pw_uid != 0 and user.pw_uid < 1000:
        continue
    if user.pw_shell.endswith(('nologin', 'false')):
        continue
    home = pathlib.Path(user.pw_dir)
    if not home.is_dir() or home.is_symlink():
        continue
    for version in ('3.0', '4.0'):
        update(home / f'.config/gtk-{version}/settings.ini', user.pw_uid, user.pw_gid)
    print(f'Configured GTK for {user.pw_name}')
PY

echo "Installed $count fonts; default GTK font: $font_name. Log out and back in to refresh running applications."
