#!/bin/bash
# 生成 Windows 传输整包（源码 + 离线模型 + Python 安装包 + bat）
cd "$(dirname "$0")" || exit 1

if [ -x ".venv/bin/python" ]; then
  PY=".venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PY="python3"
else
  echo "没有找到 Python 3。请先安装 Python 3.11。"
  read -r -p "按回车关闭..." _
  exit 1
fi

"$PY" packaging/make_transfer_zip.py "$@"
RC=$?

echo
read -r -p "按回车关闭..." _
exit "$RC"
