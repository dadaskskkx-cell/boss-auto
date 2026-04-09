#!/bin/bash
set -e

cd "$(dirname "$0")"

if [ ! -f "config/config.yaml" ] && [ -f "config/config.yaml.example" ]; then
  cp "config/config.yaml.example" "config/config.yaml"
  echo "已根据模板生成 config/config.yaml"
fi

if command -v python3.12 >/dev/null 2>&1; then
  PYTHON_BIN="python3.12"
else
  PYTHON_BIN="python3"
fi

if [ ! -d "venv" ]; then
  "$PYTHON_BIN" -m venv venv
fi

source venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt

if grep -q "sk-xxx" "config/config.yaml"; then
  echo
  echo "请先编辑 config/config.yaml，填入你自己的模型 API Key 后再启动。"
  echo "当前已为你生成模板文件：config/config.yaml"
  exit 1
fi

python -m src.profile_editor
