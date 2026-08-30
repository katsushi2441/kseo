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

# kseo.exbridge.jp のLP（英語=index.html / 日本語=kseo.html）
lp_remote="/web/kseo_exbridge_jp"
upload_lp() {
  curl --fail --silent --show-error --ftp-create-dirs -T "$1" \
    "ftp://${FTP_USER}:${FTP_PASS}@${FTP_HOST}${lp_remote}/$2"
  echo "deployed: kseo.exbridge.jp/$2"
}
upload_lp landing/index.html               index.html
upload_lp landing/kseo.html                kseo.html
upload_lp landing/assets/kurage_avatar.png  assets/kurage_avatar.png
upload_lp landing/assets/kurage_avatar.webp assets/kurage_avatar.webp
upload_lp landing/assets/ogp.png            assets/ogp.png
upload_lp landing/robots.txt                robots.txt
upload_lp landing/sitemap.xml               sitemap.xml

echo "published: https://kurage.exbridge.jp/kseo.php"
echo "published: https://kseo.exbridge.jp/ (en) / https://kseo.exbridge.jp/kseo.html (ja)"
