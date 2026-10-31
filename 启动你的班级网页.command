#!/bin/bash
# 双击后启动当前工作目录下的最新本地网页；底库与活动照片都可在网页上传。
set -euo pipefail
cd "$(dirname "$0")"

if [[ ! -x ".venv/bin/python" ]]; then
  echo "没有找到项目虚拟环境 .venv。"
  read -r -p "按回车退出…"
  exit 1
fi

# 从正式人员库读取有效班级。只存在旧缓存目录、但没有人员名单的班级不会误显示。
# 用 while-read 而不是 mapfile：macOS 自带的 /bin/bash 3.2 没有 mapfile。
CLASSES=()
while IFS= read -r _line; do
  [[ -n "$_line" ]] && CLASSES+=("$_line")
done < <(.venv/bin/python - <<'PY'
from photoorg.config import AppPaths
from photoorg.db import PersonStore
for c in PersonStore(AppPaths('.').persons_db).classes():
    print(c['name'])
PY
)

CLASS_NAME="${1:-}"
if [[ -z "$CLASS_NAME" ]]; then
  if [[ ${#CLASSES[@]} -eq 0 ]]; then
    echo "还没有班级。请输入要新建的班级名（例如：三年二班）："
  else
    echo "现有班级："
    for i in "${!CLASSES[@]}"; do echo "  $((i+1)). ${CLASSES[$i]}"; done
    echo "请输入序号，或直接输入新班级名："
  fi
  read -r CLASS_NAME
  if [[ "$CLASS_NAME" =~ ^[0-9]+$ ]] && (( CLASS_NAME >= 1 && CLASS_NAME <= ${#CLASSES[@]} )); then
    CLASS_NAME="${CLASSES[$((CLASS_NAME-1))]}"
  fi
fi
if [[ -z "$CLASS_NAME" ]]; then
  echo "班级名不能为空。"
  read -r -p "按回车退出…"
  exit 1
fi

# 新班级先初始化，使网页能立即显示并使用网页上传底库/活动照片。
CLASS_NAME="$CLASS_NAME" .venv/bin/python - <<'PY'
import os
from photoorg.config import AppPaths
from photoorg.db import PersonStore
name = os.environ['CLASS_NAME']
app = AppPaths('.')
if not PersonStore(app.persons_db).klass(name):
    app.ensure()
    PersonStore(app.persons_db).upsert_class(name)
    print(f"已创建新班级：{name}")
PY

ENCODED_CLASS=$(CLASS_NAME="$CLASS_NAME" .venv/bin/python - <<'PY'
import os
from urllib.parse import quote
print(quote(os.environ['CLASS_NAME']))
PY
)
PORT=8765
URL="http://127.0.0.1:${PORT}/?class=$ENCODED_CLASS&v=$(date +%s)"
# 防止遗留旧服务占用 8765，浏览器因此加载到之前的静态网页。
OLD_PIDS=$(lsof -tiTCP:"$PORT" -sTCP:LISTEN 2>/dev/null || true)
if [[ -n "$OLD_PIDS" ]]; then
  echo "正在停止旧的网页服务（端口 ${PORT}）…"
  kill $OLD_PIDS 2>/dev/null || true
  sleep 1
fi
echo "正在启动最新本机网页服务…"
echo "浏览器地址：$URL"
echo "停止服务：回到这个终端按 Ctrl+C"
.venv/bin/photoorg review --class "$CLASS_NAME" --port "$PORT" --no-browser &
SERVER_PID=$!
trap 'kill "$SERVER_PID" 2>/dev/null || true' EXIT INT TERM
for _ in {1..20}; do
  if curl -fsS "http://127.0.0.1:${PORT}/api/health" >/dev/null 2>&1; then break; fi
  sleep 0.25
done
open "$URL" || true
wait "$SERVER_PID"
