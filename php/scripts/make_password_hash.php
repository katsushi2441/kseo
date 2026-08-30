<?php
// 管理画面のパスワードハッシュを作る。
// 実行: php scripts/make_password_hash.php 'あなたのパスワード'
if (PHP_SAPI !== 'cli') { http_response_code(404); exit; }
if ($argc < 2) { fwrite(STDERR, "使い方: php scripts/make_password_hash.php 'パスワード'\n"); exit(1); }
echo password_hash($argv[1], PASSWORD_DEFAULT), "\n";
