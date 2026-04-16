#!/bin/bash
set -e

# Project Ironmouse (and Project Eben as a whole) doesn't use systemd-timesyncd
# so we use a custom script built for eben to sync time
exec /home/dapixelprowler/adapter/.venv/bin/python timeset.py