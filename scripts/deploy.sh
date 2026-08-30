#!/usr/bin/env bash
# kseo の公開ファイルを heteml (kurage.exbridge.jp) へ配置する。
# 秘密は aixec/.env から読み、このリポジトリには置かない。
set -euo pipefail
cd "$(dirname "$0")/.."
set -a
. /home/kojima/work/aixec/.env
set +a

remote="/web/kurage_exbridge_jp"
upload() {
  curl --fail --silent --show-error --ftp-create-dirs -T "$1" \
    "ftp://${FTP_USER}:${FTP_PASS}@${FTP_HOST}${remote}/$2"
  echo "deployed: $2"
}

if [[ ! -f public/kseo_config.php ]]; then
  echo "missing public/kseo_config.php (KSEO_API_BASE / KSEO_API_TOKEN)" >&2
  exit 1
fi

upload public/kseo.php        kseo.php
upload public/kseo_config.php kseo_config.php
upload static/styles.css      assets/kseo.css
upload static/app.js          assets/kseo.js

echo "published: https://kurage.exbridge.jp/kseo.php"
