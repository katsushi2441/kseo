#!/usr/bin/env python3
"""PHP版とPython版が同じURLで同じ結果を出すか突き合わせる。

移植でいちばん危ないのは「本家と食い違うこと」。点数が違えば、どちらを
信じればいいのか利用者に説明できなくなる。実サイトを両方にかけて、
総合点・重大度の内訳・検出した規則IDの集合が一致することを確かめる。

実行: python3 scripts/crosscheck_php_python.py <URL> [ページ数]
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import audit_service  # noqa: E402

PHP_RUNNER = """<?php
require_once __DIR__ . '/../php/kseo.php';
$audit = kseo_run_audit($argv[1], (int)$argv[2]);
if (isset($audit['error'])) {
    fwrite(STDERR, $audit['error'] . "\\n");
    exit(1);
}
echo json_encode([
    'overall'  => $audit['score']['overall'],
    'counts'   => $audit['score']['counts'],
    'pages'    => $audit['score']['pages'],
    'rules'    => array_values(array_map(fn($f) => $f['rule'], $audit['findings'])),
    'categories' => array_map(fn($c) => $c['score'], $audit['score']['categories']),
], JSON_UNESCAPED_UNICODE);
"""


def run_php(url: str, pages: int) -> dict:
    runner = ROOT / "scripts" / "_php_runner.php"
    runner.write_text(PHP_RUNNER)
    try:
        proc = subprocess.run(
            ["php", str(runner), url, str(pages)],
            capture_output=True, text=True, timeout=600, cwd=ROOT,
        )
        if proc.returncode != 0:
            raise SystemExit(f"PHP側が失敗しました: {proc.stderr.strip()[:300]}")
        return json.loads(proc.stdout)
    finally:
        runner.unlink(missing_ok=True)


def run_python(url: str, pages: int) -> dict:
    result = asyncio.run(audit_service.run_audit(url, pages))
    score = result["score"]
    return {
        "overall": score["overall"],
        "counts": score["counts"],
        "pages": score["pages"],
        "rules": [f.rule for f in result["findings"]],
        "categories": {k: v["score"] for k, v in score["categories"].items()},
    }


def main() -> int:
    if len(sys.argv) < 2:
        print("使い方: python3 scripts/crosscheck_php_python.py <URL> [ページ数]")
        return 2
    url = sys.argv[1]
    pages = int(sys.argv[2]) if len(sys.argv) > 2 else 3

    print(f"対象: {url} ({pages}ページ)")
    print("PHP版を実行...")
    php = run_php(url, pages)
    print("Python版を実行...")
    py = run_python(url, pages)

    ok = True

    def compare(label: str, a, b) -> None:
        nonlocal ok
        same = a == b
        if not same:
            ok = False
        mark = "一致  " if same else "不一致"
        print(f"  {mark} {label}: PHP={a} / Python={b}")

    print("\n結果:")
    compare("総合点", php["overall"], py["overall"])
    compare("ページ数", php["pages"], py["pages"])
    compare("重大度の内訳", php["counts"], py["counts"])
    compare("カテゴリ別", php["categories"], py["categories"])

    php_rules = sorted(php["rules"])
    py_rules = sorted(py["rules"])
    compare("規則IDの並び", php_rules, py_rules)

    if php_rules != py_rules:
        only_php = sorted(set(php_rules) - set(py_rules))
        only_py = sorted(set(py_rules) - set(php_rules))
        if only_php:
            print(f"    PHPだけが出した: {only_php}")
        if only_py:
            print(f"    Pythonだけが出した: {only_py}")

    print()
    if ok:
        print("両実装は一致しました。")
        return 0
    print("食い違いがあります。どちらかが不具合です。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
