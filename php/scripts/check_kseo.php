<?php
/**
 * Kurage SEO（PHP版）の動作確認。ネットワークを使わない項目だけを見る。
 *
 * 重点は3つ。
 *   ・日本語の文字数を全角基準で数えられていること（英語基準だと誤判定する）
 *   ・誤検知を出さないこと（実際に踏んだものを1件ずつ残してある）
 *   ・判定が決定的であること（同じ入力なら同じ点。ぶれると信用されない）
 *
 * 実行: php scripts/check_kseo.php
 */

if (PHP_SAPI !== 'cli') { http_response_code(404); exit; }

require_once __DIR__ . '/../kseo.php';

$failures = 0;
function check(string $label, $actual, $expected): void
{
    global $failures;
    $ok = $actual === $expected;
    if (!$ok) { $failures++; }
    printf("%s %s (期待 %s / 実際 %s)\n", $ok ? 'ok  ' : 'NG  ', $label,
        var_export($expected, true), var_export($actual, true));
}

function page(string $html, string $url = 'https://example.com/page'): array
{
    return kseo_parse_page($url, $html, 200, 0.2);
}

function rules(array $findings): array
{
    return array_map(fn($f) => $f['rule'], $findings);
}

$HEALTHY = '<!doctype html><html lang="ja"><head>'
    . '<meta charset="utf-8">'
    . '<title>名古屋のAI開発会社｜受託開発とAI導入支援</title>'
    . '<meta name="description" content="名古屋でAIシステムの受託開発と導入支援を行っています。要件整理から運用まで一貫して対応し、業務に合わせて設計します。実績と料金の目安を掲載しています。">'
    . '<link rel="canonical" href="https://example.com/page">'
    . '<meta name="viewport" content="width=device-width, initial-scale=1">'
    . '<meta property="og:title" content="名古屋のAI開発会社">'
    . '<meta property="og:description" content="AI受託開発">'
    . '<meta property="og:image" content="https://example.com/og.png">'
    . '<script type="application/ld+json">{"@type":"Organization","name":"X"}</script>'
    . '<script type="application/ld+json">{"@type":"BreadcrumbList"}</script>'
    . '</head><body><h1>名古屋のAI開発</h1><h2>できること</h2><p>'
    . str_repeat('これは十分な長さの本文です。', 40)
    . '</p><a href="https://example.com/other">関連ページ</a><img src="a.png" alt="図"></body></html>';

echo "--- 文字数の数え方 ---\n";
check('全角5文字', kseo_visible_length('あいうえお'), 5);
check('半角10文字は5', kseo_visible_length('abcdefghij'), 5);
// 半角A+I=1.0、全角の活+用=2.0 で合計3.0
check('AI活用は3', kseo_visible_length('AI活用'), 3);
check('空は0', kseo_visible_length(''), 0);

echo "\n--- 正しく作られたページで何も言わないこと（誤検知の総合確認） ---\n";
check('指摘0件', rules(kseo_check_page(page($HEALTHY))), []);

echo "\n--- 実際に踏んだ誤検知の再現 ---\n";
// exbridge.jp を「charset宣言なし」と誤検知した。name= しか見ていなかった。
$equiv = str_replace('<meta charset="utf-8">',
    '<meta http-equiv="Content-Type" content="text/html; charset=utf-8" />', $HEALTHY);
check('http-equiv形式のcharsetを読める', page($equiv)['charset'], 'utf-8');
check('charset誤検知なし', in_array('japanese.charset.missing', rules(kseo_check_page(page($equiv))), true), false);

// 英語ページの言語切替リンク「日本語」3文字で日本語ページ扱いしていた。
$english = '<!doctype html><html lang="en"><head><meta charset="utf-8">'
    . '<title>Kurage SEO for Japanese pages</title>'
    . '<meta name="description" content="' . str_repeat('word ', 30) . '">'
    . '<link rel="canonical" href="https://example.com/page">'
    . '<meta name="viewport" content="width=device-width, initial-scale=1">'
    . '<meta property="og:title" content="t"><meta property="og:description" content="d">'
    . '<meta property="og:image" content="https://example.com/o.png">'
    . '<script type="application/ld+json">{"@type":"BreadcrumbList"}</script>'
    . '</head><body><a href="page.html">日本語</a><h1>Hello</h1><p>'
    . str_repeat('content ', 200) . '</p><a href="https://example.com/x">next</a></body></html>';
check('英語ページにlang不一致を出さない',
    in_array('japanese.lang.mismatch', rules(kseo_check_page(page($english))), true), false);
check('英語ページにlang不足を出さない',
    in_array('japanese.lang.missing', rules(kseo_check_page(page($english))), true), false);

// /index.html から / への canonical は正しい作法。別ページ扱いしていた。
$idx = str_replace('href="https://example.com/page"', 'href="https://example.com/"', $HEALTHY);
check('index.html→/ は矛盾でない',
    page($idx, 'https://example.com/index.html')['canonical_conflict'], false);

echo "\n--- 本物の日本語ページは取りこぼさない ---\n";
$ja_en = str_replace('<html lang="ja">', '<html lang="en">', $HEALTHY);
check('日本語本文でlang=enを指摘',
    in_array('japanese.lang.mismatch', rules(kseo_check_page(page($ja_en))), true), true);

echo "\n--- URLの正規化 ---\n";
check('フラグメント除去', kseo_normalize_url('https://a.jp/x/#top'), 'https://a.jp/x');
check('index.htmlは/と同じ',
    kseo_normalize_url('https://a.jp/index.html'), kseo_normalize_url('https://a.jp/'));
check('sub/index.phpも同様',
    kseo_normalize_url('https://a.jp/sub/index.php'), kseo_normalize_url('https://a.jp/sub/'));

echo "\n--- SSRF（ここが緩むと社内サービスを読まれる） ---\n";
foreach ([
    'http://127.0.0.1:8000/', 'http://localhost/', 'http://192.168.0.14:11434/',
    'http://169.254.169.254/latest/meta-data/', 'http://10.0.0.1/',
    'file:///etc/passwd', 'ftp://example.com/',
] as $bad) {
    [$ok] = kseo_validate_url($bad);
    check('拒否: ' . $bad, $ok, false);
}
[$ok, $normalized] = kseo_validate_url('example.com');
check('公開URLは通る', $ok, true);

echo "\n--- 主要な指摘が出ること ---\n";
$noindex = str_replace('<head>', '<head><meta name="robots" content="noindex">', $HEALTHY);
$f = kseo_check_page(page($noindex));
check('noindexは重大', ($f[0]['severity'] ?? ''), 'critical');
check('noindexで90点台にならない', kseo_score($f, 1)['overall'] <= 75, true);

$long = preg_replace('#<title>.*?</title>#', '<title>' . str_repeat('あ', 40) . '</title>', $HEALTHY);
$hit = array_values(array_filter(kseo_check_page(page($long)), fn($x) => $x['rule'] === 'meta.title.too_long'));
check('長いtitleに実測値がつく', str_contains($hit[0]['evidence'] ?? '', '40文字'), true);

$broken = str_replace('{"@type":"Organization","name":"X"}', '{"@type":,,}', $HEALTHY);
check('壊れたJSON-LDを数える', page($broken)['jsonld_invalid'], 1);

$thin = str_replace(str_repeat('これは十分な長さの本文です。', 40), '短い。', $HEALTHY);
check('薄い本文を指摘', in_array('content.thin', rules(kseo_check_page(page($thin))), true), true);

$nolink = str_replace('<a href="https://example.com/other">関連ページ</a>', '', $HEALTHY);
check('内部リンク0を指摘',
    in_array('content.no_internal_link', rules(kseo_check_page(page($nolink))), true), true);

echo "\n--- 採点 ---\n";
check('健全なページは100点', kseo_score(kseo_check_page(page($HEALTHY)), 1)['overall'], 100);

echo "\n--- 決定的であること（同じ入力なら同じ結果） ---\n";
$a = kseo_score(kseo_check_page(page($noindex)), 1);
$b = kseo_score(kseo_check_page(page($noindex)), 1);
check('2回実行して同じ点', $a['overall'], $b['overall']);
check('2回実行して同じ内訳', $a['counts'], $b['counts']);

echo "\n--- サイト単位（複数ページで初めて分かること） ---\n";
$site = [
    'base_url' => 'https://example.com/', 'robots_txt' => "User-agent: *\n",
    'robots_blocks_all' => false, 'robots_sitemap' => 'https://example.com/sitemap.xml',
    'sitemap_found' => true, 'broken_links' => [],
];
$p1 = kseo_page_summary(page($HEALTHY, 'https://example.com/a'));
$p2 = kseo_page_summary(page($HEALTHY, 'https://example.com/b'));
$p1['title'] = $p2['title'] = 'かぶったタイトル';
$p1['description'] = $p2['description'] = 'かぶった説明文';
$sr = rules(kseo_check_site($site, [$p1, $p2]));
check('重複titleを検出', in_array('meta.title.duplicate', $sr, true), true);
check('重複descriptionを検出', in_array('meta.description.duplicate', $sr, true), true);

$blocked = $site;
$blocked['robots_blocks_all'] = true;
$hit = array_values(array_filter(kseo_check_site($blocked, []),
    fn($x) => $x['rule'] === 'crawl.robots.disallow_all'));
check('robots全拒否は重大', $hit[0]['severity'] ?? '', 'critical');

echo "\n";
if ($failures > 0) {
    echo "NG が {$failures} 件あります。\n";
    exit(1);
}
echo "すべて通りました。\n";
