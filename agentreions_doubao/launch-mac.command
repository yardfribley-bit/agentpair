#!/bin/zsh
set -eu
task_root="${0:A:h}"
cd "$task_root"
if [[ -x "$task_root/.venv/bin/python" ]]; then
  exec "$task_root/.venv/bin/python" "$task_root/desktop_main.py"
elif command -v python3 >/dev/null 2>&1; then
  exec python3 "$task_root/desktop_main.py"
else
  print '请先安装 Python 3.12+ 和 PySide6 Essentials。'
  exit 1
fi
