#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
DIST_NAME="boss-auto-script-mac"
DIST_DIR="$ROOT_DIR/dist/$DIST_NAME"
ZIP_PATH="$ROOT_DIR/dist/${DIST_NAME}.zip"

rm -rf "$DIST_DIR"
mkdir -p "$DIST_DIR/config" "$DIST_DIR/src" "$DIST_DIR/docs" "$DIST_DIR/data"

cp "$ROOT_DIR/start.sh" "$DIST_DIR/start.sh"
cp "$ROOT_DIR/launch.command" "$DIST_DIR/launch.command"
cp "$ROOT_DIR/requirements.txt" "$DIST_DIR/requirements.txt"
cp "$ROOT_DIR/README.md" "$DIST_DIR/README.md"

cp "$ROOT_DIR/config/config.yaml.example" "$DIST_DIR/config/config.yaml.example"
cp "$ROOT_DIR/config/config.yaml.example" "$DIST_DIR/config/config.yaml"
cp "$ROOT_DIR/config/profile.yaml" "$DIST_DIR/config/profile.yaml"
cp "$ROOT_DIR/config/profile.yaml.example" "$DIST_DIR/config/profile.yaml.example"

cp "$ROOT_DIR/src/__init__.py" "$DIST_DIR/src/__init__.py"
cp "$ROOT_DIR/src/llm_client.py" "$DIST_DIR/src/llm_client.py"
cp "$ROOT_DIR/src/messenger.py" "$DIST_DIR/src/messenger.py"
cp "$ROOT_DIR/src/ocr_worker.py" "$DIST_DIR/src/ocr_worker.py"
cp "$ROOT_DIR/src/profile_builder.py" "$DIST_DIR/src/profile_builder.py"
cp "$ROOT_DIR/src/profile_editor.py" "$DIST_DIR/src/profile_editor.py"
cp "$ROOT_DIR/src/resume_filter.py" "$DIST_DIR/src/resume_filter.py"
cp "$ROOT_DIR/src/rpa_crawler.py" "$DIST_DIR/src/rpa_crawler.py"
cp "$ROOT_DIR/src/script_runner.py" "$DIST_DIR/src/script_runner.py"

if [ -f "$ROOT_DIR/docs/boss-ban-warning.jpeg" ]; then
  cp "$ROOT_DIR/docs/boss-ban-warning.jpeg" "$DIST_DIR/docs/boss-ban-warning.jpeg"
fi

if [ -f "$ROOT_DIR/docs/同事试用说明.md" ]; then
  cp "$ROOT_DIR/docs/同事试用说明.md" "$DIST_DIR/docs/同事试用说明.md"
fi

if [ -f "$ROOT_DIR/docs/给同事使用说明.md" ]; then
  cp "$ROOT_DIR/docs/给同事使用说明.md" "$DIST_DIR/docs/给同事使用说明.md"
fi

chmod +x "$DIST_DIR/start.sh" "$DIST_DIR/launch.command"

rm -f "$ZIP_PATH"
(
  cd "$ROOT_DIR/dist"
  zip -qr "${DIST_NAME}.zip" "$DIST_NAME"
)

echo "打包完成: $ZIP_PATH"
