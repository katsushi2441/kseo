<?php
/**
 * Kurage SEO（PHP版）— 日本語サイトのSEOを1ファイルで診断する。
 *
 * 【何をするか】
 * 与えられたURLをクロールして、10カテゴリの技術SEOを点検する。
 * タイトルの長さは全角で数え、文字コード宣言・lang属性・文字化けまで見る。
 *
 * 【LLMを使わない】
 * 判定はすべて「測って閾値と比べる」だけ。ここに生成AIを挟むと結果が実行ごとに
 * ぶれて「直したのに点が下がった」が起きる。診断は決定的でなければならない。
 *
 * 【本家と食い違わせない】
 * Python版（github.com/katsushi2441/kseo）と、規則ID・カテゴリ名・閾値・
 * 採点式をすべて揃えてある。同じURLを両方にかけて違う点が出たら、それは不具合。
 * scripts/check_kseo.php がこの一致を含めて検査する。
 *
 * 【外部ライブラリを使わない】
 * DOMDocument も使わない。共有レンタルサーバーでは ext-dom が入っていない
 * ことがあるため、解析は正規表現で行う。DB・Composer・npm は不要。
 *
 * PHP 8.0 以上。
 *
 * MIT License / 株式会社エクスブリッジ
 */

declare(strict_types=1);

// ---------------------------------------------------------------------------
// 設定
// ---------------------------------------------------------------------------

$config_file = __DIR__ . '/kseo_config.php';
if (is_file($config_file)) {
    require_once $config_file;
}

if (!defined('KSEO_TITLE'))        { define('KSEO_TITLE', 'SEO診断'); }
if (!defined('KSEO_PASSWORD_HASH')){ define('KSEO_PASSWORD_HASH', ''); }
if (!defined('KSEO_DATA_DIR'))     { define('KSEO_DATA_DIR', __DIR__ . '/kseo_data'); }
if (!defined('KSEO_MAX_PAGES'))    { define('KSEO_MAX_PAGES', 20); }
if (!defined('KSEO_CRAWL_DELAY'))  { define('KSEO_CRAWL_DELAY', 1.0); }
if (!defined('KSEO_TIMEOUT'))      { define('KSEO_TIMEOUT', 20); }
if (!defined('KSEO_MAX_HTML'))     { define('KSEO_MAX_HTML', 3 * 1024 * 1024); }
if (!defined('KSEO_KEEP_AUDITS'))  { define('KSEO_KEEP_AUDITS', 50); }
if (!defined('KSEO_USER_AGENT'))   { define('KSEO_USER_AGENT', 'KurageSEO/1.0 (+https://kseo.exbridge.jp/)'); }
if (!defined('KSEO_ALLOW_PRIVATE')){ define('KSEO_ALLOW_PRIVATE', false); }

// ---------------------------------------------------------------------------
// 判定の閾値。Python版 app/audit_rules.py と同じ値にしてある。
// ---------------------------------------------------------------------------

const KSEO_TITLE_MAX = 32;          // 全角。日本語のタイトルはここで切れ始める
const KSEO_TITLE_MIN = 10;
const KSEO_DESC_MAX = 120;
const KSEO_DESC_MIN = 50;
const KSEO_THIN_CONTENT = 300;
const KSEO_MAX_LINKS = 200;
const KSEO_MAX_BLOCKING_SCRIPTS = 3;
const KSEO_SLOW_SEC = 1.5;
const KSEO_VERY_SLOW_SEC = 3.0;

// 日本語ページと見なす下限。英語ページの言語切替リンク「日本語」の3文字だけで
// 日本語ページ扱いすると、lang="en" が正しいページに不一致を出してしまう。
const KSEO_JA_MIN_CHARS = 40;
const KSEO_JA_MIN_RATIO = 0.08;

const KSEO_SEVERITY_WEIGHT = ['critical' => 25, 'warning' => 10, 'info' => 3];

function kseo_categories(): array
{
    return [
        'crawl'      => 'クロール・インデックス',
        'meta'       => 'タイトル・説明文',
        'heading'    => '見出し構造',
        'structured' => '構造化データ',
        'social'     => 'SNS表示(OGP)',
        'content'    => 'コンテンツ',
        'mobile'     => 'モバイル対応',
        'speed'      => '表示速度',
        'security'   => 'HTTPS・安全性',
        'japanese'   => '日本語サイト固有',
    ];
}

// ---------------------------------------------------------------------------
// 文字数の数え方
// ---------------------------------------------------------------------------

/**
 * 検索結果での見え方に近い文字数。全角=1、半角=0.5 で数えて切り上げる。
 *
 * 英語圏のツールは半角60文字で判定するが、日本語をその物差しで測ると
 * 実際には切れているのに「問題なし」と出る。「AI活用」は3文字。
 */
function kseo_visible_length(string $text): int
{
    if ($text === '') {
        return 0;
    }
    $width = 0.0;
    $chars = preg_split('//u', $text, -1, PREG_SPLIT_NO_EMPTY) ?: [];
    foreach ($chars as $ch) {
        $width += kseo_is_wide($ch) ? 1.0 : 0.5;
    }
    return (int)ceil($width - 0.0001);
}

/** East Asian Width が W/F/A のものを全角とみなす。 */
function kseo_is_wide(string $ch): bool
{
    $cp = kseo_codepoint($ch);
    if ($cp < 0x1100) {
        return false;
    }
    $ranges = [
        [0x1100, 0x115F], [0x2E80, 0x303E], [0x3041, 0x33FF],
        [0x3400, 0x4DBF], [0x4E00, 0x9FFF], [0xA000, 0xA4CF],
        [0xAC00, 0xD7A3], [0xF900, 0xFAFF], [0xFE10, 0xFE19],
        [0xFE30, 0xFE6F], [0xFF00, 0xFF60], [0xFFE0, 0xFFE6],
        [0x1F300, 0x1F64F], [0x1F900, 0x1F9FF], [0x20000, 0x2FFFD],
    ];
    foreach ($ranges as [$lo, $hi]) {
        if ($cp >= $lo && $cp <= $hi) {
            return true;
        }
    }
    return false;
}

function kseo_codepoint(string $ch): int
{
    $bytes = unpack('C*', $ch);
    if (!$bytes) {
        return 0;
    }
    $b = array_values($bytes);
    $n = count($b);
    if ($n === 1) { return $b[0]; }
    if ($n === 2) { return (($b[0] & 0x1F) << 6) | ($b[1] & 0x3F); }
    if ($n === 3) { return (($b[0] & 0x0F) << 12) | (($b[1] & 0x3F) << 6) | ($b[2] & 0x3F); }
    if ($n === 4) {
        return (($b[0] & 0x07) << 18) | (($b[1] & 0x3F) << 12)
             | (($b[2] & 0x3F) << 6) | ($b[3] & 0x3F);
    }
    return 0;
}

/** 本文が日本語で書かれていると言えるか。数文字の混在では真としない。 */
function kseo_has_japanese(string $text): bool
{
    if ($text === '') {
        return false;
    }
    $hits = preg_match_all('/[\x{3041}-\x{309F}\x{30A1}-\x{30FA}\x{4E00}-\x{9FFF}]/u', $text);
    if ($hits === false || $hits < KSEO_JA_MIN_CHARS) {
        return false;
    }
    $total = preg_match_all('/./u', $text) ?: 1;
    return ($hits / $total) >= KSEO_JA_MIN_RATIO;
}

function kseo_norm(string $text): string
{
    return trim((string)preg_replace('/\s+/u', ' ', $text));
}

// ---------------------------------------------------------------------------
// URLの安全確認（SSRF対策）
// ---------------------------------------------------------------------------

const KSEO_BLOCKED_PORTS = [22, 23, 25, 445, 3306, 5432, 6379, 11211, 11434];

/**
 * 公開されたhttp(s) URLだけを通す。内部宛ては名前解決の結果で弾く。
 *
 * ホスト名で書かれた内部宛て（router.local など）を防ぐため、文字列ではなく
 * 解決後のIPを見る。ここが緩むと、このサーバーを踏み台に社内のサービスを
 * 読まれる。社内サイトを診断したい場合だけ KSEO_ALLOW_PRIVATE を立てるが、
 * そのときは絶対に外部へ公開しないこと。
 *
 * @return array{0:bool,1:string} [OKか, 正規化URLまたはエラー文]
 */
function kseo_validate_url(string $raw): array
{
    $value = trim($raw);
    if ($value === '') {
        return [false, 'URLを入力してください。'];
    }
    if (!str_contains($value, '://')) {
        $value = 'https://' . $value;
    }
    $parts = parse_url($value);
    if ($parts === false || empty($parts['scheme']) || empty($parts['host'])) {
        return [false, 'URLを読み取れませんでした。'];
    }
    if (!in_array(strtolower($parts['scheme']), ['http', 'https'], true)) {
        return [false, 'http:// または https:// のURLを指定してください。'];
    }
    $port = $parts['port'] ?? null;
    if ($port !== null && in_array((int)$port, KSEO_BLOCKED_PORTS, true)) {
        return [false, 'そのポートへの接続は許可していません。'];
    }
    if (KSEO_ALLOW_PRIVATE) {
        return [true, $value];
    }

    $host = $parts['host'];
    $ips = [];
    $v4 = @gethostbynamel($host);
    if (is_array($v4)) {
        $ips = $v4;
    }
    $v6 = @dns_get_record($host, DNS_AAAA);
    if (is_array($v6)) {
        foreach ($v6 as $row) {
            if (!empty($row['ipv6'])) { $ips[] = $row['ipv6']; }
        }
    }
    if (filter_var($host, FILTER_VALIDATE_IP)) {
        $ips[] = $host;
    }
    if (!$ips) {
        return [false, 'ホスト名を解決できませんでした: ' . $host];
    }
    foreach ($ips as $ip) {
        $public = filter_var(
            $ip,
            FILTER_VALIDATE_IP,
            FILTER_FLAG_NO_PRIV_RANGE | FILTER_FLAG_NO_RES_RANGE
        );
        if ($public === false) {
            return [false, '内部ネットワークのアドレス(' . $ip . ')に解決されるURLは監査できません。'];
        }
    }
    return [true, $value];
}

/** フラグメント・末尾スラッシュ・既定ファイル名の揺れを吸収する。 */
function kseo_normalize_url(string $url): string
{
    $p = parse_url($url);
    if ($p === false || empty($p['scheme']) || empty($p['host'])) {
        return $url;
    }
    $path = $p['path'] ?? '/';
    // /index.html から / への canonical は正しい作法なので、別ページ扱いしない。
    foreach (['index.html', 'index.htm', 'index.php', 'default.html'] as $name) {
        if (str_ends_with($path, '/' . $name)) {
            $path = substr($path, 0, -strlen($name));
            break;
        }
    }
    if (strlen($path) > 1 && str_ends_with($path, '/')) {
        $path = rtrim($path, '/');
    }
    if ($path === '') {
        $path = '/';
    }
    $out = strtolower($p['scheme']) . '://' . strtolower($p['host']);
    if (!empty($p['port'])) {
        $out .= ':' . $p['port'];
    }
    $out .= $path;
    if (!empty($p['query'])) {
        $out .= '?' . $p['query'];
    }
    return $out;
}

/** 相対URLを絶対URLにする。 */
function kseo_absolute_url(string $base, string $href): string
{
    $href = trim($href);
    if ($href === '') {
        return '';
    }
    if (preg_match('#^[a-zA-Z][a-zA-Z0-9+.-]*://#', $href)) {
        return $href;
    }
    $b = parse_url($base);
    if ($b === false || empty($b['scheme']) || empty($b['host'])) {
        return '';
    }
    $root = $b['scheme'] . '://' . $b['host'] . (empty($b['port']) ? '' : ':' . $b['port']);
    if (str_starts_with($href, '//')) {
        return $b['scheme'] . ':' . $href;
    }
    if (str_starts_with($href, '/')) {
        return $root . $href;
    }
    if (str_starts_with($href, '?')) {
        return $root . ($b['path'] ?? '/') . $href;
    }
    $dir = $b['path'] ?? '/';
    $dir = str_ends_with($dir, '/') ? $dir : dirname($dir) . '/';
    if ($dir === '//') {
        $dir = '/';
    }
    $path = $dir . $href;
    // ../ を畳む
    $segments = [];
    foreach (explode('/', $path) as $seg) {
        if ($seg === '.' || $seg === '') { continue; }
        if ($seg === '..') { array_pop($segments); continue; }
        $segments[] = $seg;
    }
    return $root . '/' . implode('/', $segments);
}

// ---------------------------------------------------------------------------
// 取得
// ---------------------------------------------------------------------------

/**
 * URLを1件取りに行く。
 *
 * @return array{status:int,body:string,ctype:string,elapsed:float,final:string,error:string}
 */
function kseo_fetch(string $url): array
{
    $started = microtime(true);
    $result = [
        'status' => 0, 'body' => '', 'ctype' => '',
        'elapsed' => 0.0, 'final' => $url, 'error' => '',
    ];
    if (function_exists('curl_init')) {
        $ch = curl_init($url);
        curl_setopt_array($ch, [
            CURLOPT_RETURNTRANSFER => true,
            CURLOPT_FOLLOWLOCATION => true,
            CURLOPT_MAXREDIRS      => 5,
            CURLOPT_TIMEOUT        => KSEO_TIMEOUT,
            CURLOPT_CONNECTTIMEOUT => 10,
            CURLOPT_USERAGENT      => KSEO_USER_AGENT,
            CURLOPT_HTTPHEADER     => ['Accept-Language: ja,en;q=0.8'],
            CURLOPT_ENCODING       => '',
        ]);
        $body = curl_exec($ch);
        $result['elapsed'] = microtime(true) - $started;
        if ($body === false) {
            $result['error'] = curl_error($ch);
            curl_close($ch);
            return $result;
        }
        $result['status'] = (int)curl_getinfo($ch, CURLINFO_HTTP_CODE);
        $result['ctype']  = (string)curl_getinfo($ch, CURLINFO_CONTENT_TYPE);
        $result['final']  = (string)curl_getinfo($ch, CURLINFO_EFFECTIVE_URL);
        $result['body']   = substr((string)$body, 0, KSEO_MAX_HTML);
        curl_close($ch);
        return $result;
    }

    $ctx = stream_context_create(['http' => [
        'timeout' => KSEO_TIMEOUT,
        'user_agent' => KSEO_USER_AGENT,
        'follow_location' => 1,
        'max_redirects' => 5,
        'ignore_errors' => true,
    ]]);
    $body = @file_get_contents($url, false, $ctx, 0, KSEO_MAX_HTML);
    $result['elapsed'] = microtime(true) - $started;
    if ($body === false) {
        $result['error'] = '取得できませんでした';
        return $result;
    }
    $result['body'] = $body;
    foreach ($http_response_header ?? [] as $line) {
        if (preg_match('#^HTTP/\S+\s+(\d{3})#', $line, $m)) {
            $result['status'] = (int)$m[1];
        }
        if (stripos($line, 'content-type:') === 0) {
            $result['ctype'] = trim(substr($line, 13));
        }
    }
    return $result;
}

// ---------------------------------------------------------------------------
// HTML解析（正規表現。ext-dom が無い共有サーバーでも動かすため）
// ---------------------------------------------------------------------------

/** タグの属性を name => value の配列にする。 */
function kseo_attrs(string $tag): array
{
    $attrs = [];
    if (preg_match_all('/([a-zA-Z_:][-a-zA-Z0-9_:.]*)\s*=\s*("([^"]*)"|\'([^\']*)\'|([^\s"\'>]+))/', $tag, $m, PREG_SET_ORDER)) {
        foreach ($m as $one) {
            $value = $one[3] ?? '';
            if ($value === '' && isset($one[4]) && $one[4] !== '') { $value = $one[4]; }
            if ($value === '' && isset($one[5]) && $one[5] !== '') { $value = $one[5]; }
            $attrs[strtolower($one[1])] = html_entity_decode($value, ENT_QUOTES, 'UTF-8');
        }
    }
    // 値なし属性（async / defer）も拾う
    if (preg_match_all('/\s(async|defer)(?=[\s\/>])/i', $tag, $m2)) {
        foreach ($m2[1] as $flag) {
            $attrs[strtolower($flag)] = $attrs[strtolower($flag)] ?? '';
        }
    }
    return $attrs;
}

/**
 * 1ページを解析して、判定に必要な事実だけを取り出す。
 * Python版 crawler.parse_page と同じ項目を返す。
 */
function kseo_parse_page(string $url, string $html, int $status, float $elapsed): array
{
    $page = [
        'url' => $url, 'status' => $status, 'elapsed' => round($elapsed, 3),
        'html_bytes' => strlen($html), 'title' => '', 'description' => '',
        'canonical' => '', 'canonical_conflict' => false, 'robots_meta' => '',
        'noindex' => false, 'nofollow_page' => false, 'lang' => '', 'charset' => '',
        'viewport' => '', 'h1_count' => 0, 'h1_texts' => [], 'heading_jumps' => [],
        'jsonld_types' => [], 'jsonld_invalid' => 0, 'og' => [],
        'text_length' => 0, 'text_sample' => '', 'images_total' => 0,
        'images_without_alt' => 0, 'link_count' => 0, 'internal_link_count' => 0,
        'links' => [], 'blocking_scripts' => 0, 'mixed_content' => 0,
        'mojibake' => '', 'fullwidth_alnum_in_title' => '', 'error' => '',
    ];

    if (preg_match('#<title[^>]*>(.*?)</title>#is', $html, $m)) {
        $page['title'] = kseo_norm(html_entity_decode($m[1], ENT_QUOTES, 'UTF-8'));
    }
    if (preg_match_all('/[\x{FF21}-\x{FF3A}\x{FF41}-\x{FF5A}\x{FF10}-\x{FF19}]+/u', $page['title'], $fw)) {
        $page['fullwidth_alnum_in_title'] = implode(', ', array_slice($fw[0], 0, 3));
    }

    if (preg_match_all('/<meta\b[^>]*>/i', $html, $metas)) {
        foreach ($metas[0] as $tag) {
            $a = kseo_attrs($tag);
            $name = strtolower($a['name'] ?? '');
            $prop = strtolower($a['property'] ?? '');
            $equiv = strtolower($a['http-equiv'] ?? '');
            $content = trim($a['content'] ?? '');
            if ($name === 'description')      { $page['description'] = $content; }
            elseif ($name === 'robots')       { $page['robots_meta'] = $content; }
            elseif ($name === 'viewport')     { $page['viewport'] = $content; }
            elseif (str_starts_with($prop, 'og:')) { $page['og'][$prop] = $content; }

            // charset は3通りの書き方がある。name= しか見ないと
            // http-equiv 版を見落として「宣言なし」と誤検知する。
            if (!empty($a['charset'])) {
                $page['charset'] = trim($a['charset']);
            } elseif (($equiv === 'content-type' || $name === 'content-type')
                && stripos($content, 'charset=') !== false) {
                $page['charset'] = trim(explode('charset=', strtolower($content), 2)[1], " \t\"'");
            }
        }
    }

    $robots = strtolower($page['robots_meta']);
    $page['noindex'] = str_contains($robots, 'noindex');
    $page['nofollow_page'] = str_contains($robots, 'nofollow');

    if (preg_match_all('/<link\b[^>]*>/i', $html, $links)) {
        foreach ($links[0] as $tag) {
            $a = kseo_attrs($tag);
            $rel = strtolower(trim($a['rel'] ?? ''));
            if ($rel !== '' && in_array('canonical', preg_split('/\s+/', $rel) ?: [], true)
                && !empty($a['href'])) {
                $page['canonical'] = kseo_absolute_url($url, $a['href']);
                $page['canonical_conflict'] =
                    kseo_normalize_url($page['canonical']) !== kseo_normalize_url($url);
                break;
            }
        }
    }

    if (preg_match('/<html\b[^>]*>/i', $html, $m)) {
        $a = kseo_attrs($m[0]);
        $page['lang'] = trim($a['lang'] ?? '');
    }

    $levels = [];
    if (preg_match_all('#<h([1-6])\b[^>]*>(.*?)</h\1>#is', $html, $hs, PREG_SET_ORDER)) {
        foreach ($hs as $h) {
            $level = (int)$h[1];
            $levels[] = $level;
            if ($level === 1) {
                $page['h1_count']++;
                $text = kseo_norm(html_entity_decode(strip_tags($h[2]), ENT_QUOTES, 'UTF-8'));
                if ($text !== '') { $page['h1_texts'][] = $text; }
            }
        }
    }
    for ($i = 1; $i < count($levels); $i++) {
        if ($levels[$i] - $levels[$i - 1] >= 2) {
            $page['heading_jumps'][] = 'H' . $levels[$i - 1] . ' -> H' . $levels[$i];
        }
    }

    if (preg_match_all('#<script\b[^>]*type\s*=\s*["\']?application/ld\+json["\']?[^>]*>(.*?)</script>#is', $html, $lds)) {
        foreach ($lds[1] as $raw) {
            $data = json_decode(trim($raw), true);
            if (!is_array($data)) {
                $page['jsonld_invalid']++;
                continue;
            }
            $entries = isset($data[0]) ? $data : [$data];
            foreach ($entries as $entry) {
                if (!is_array($entry)) { continue; }
                foreach ((array)($entry['@type'] ?? []) as $t) {
                    if ($t !== '' && is_string($t)) { $page['jsonld_types'][] = $t; }
                }
                foreach ((array)($entry['@graph'] ?? []) as $nested) {
                    if (is_array($nested) && !empty($nested['@type']) && is_string($nested['@type'])) {
                        $page['jsonld_types'][] = $nested['@type'];
                    }
                }
            }
        }
    }
    $page['jsonld_types'] = array_values(array_unique($page['jsonld_types']));

    $body = preg_replace('#<(script|style|noscript|template)\b[^>]*>.*?</\1>#is', ' ', $html) ?? $html;
    $text = kseo_norm(html_entity_decode(strip_tags($body), ENT_QUOTES, 'UTF-8'));
    $page['text_length'] = (int)preg_match_all('/\S/u', $text);
    $page['text_sample'] = mb_substr($text, 0, 4000, 'UTF-8');
    if (preg_match('/[\x{00C3}\x{00C2}][\x{0080}-\x{00BF}]|\x{FFFD}{2,}/u', $page['text_sample'], $mj)) {
        $page['mojibake'] = mb_substr($mj[0], 0, 20, 'UTF-8');
    }

    if (preg_match_all('/<img\b[^>]*>/i', $html, $imgs)) {
        $page['images_total'] = count($imgs[0]);
        foreach ($imgs[0] as $tag) {
            if (!array_key_exists('alt', kseo_attrs($tag))) {
                $page['images_without_alt']++;
            }
        }
    }

    $host = strtolower((string)(parse_url($url, PHP_URL_HOST) ?: ''));
    if (preg_match_all('/<a\b[^>]*href\s*=\s*("[^"]*"|\'[^\']*\'|[^\s>]+)[^>]*>/i', $html, $as)) {
        foreach ($as[0] as $tag) {
            $a = kseo_attrs($tag);
            $href = trim($a['href'] ?? '');
            if ($href === '' || preg_match('/^(mailto:|tel:|javascript:|#)/i', $href)) {
                continue;
            }
            $abs = kseo_absolute_url($url, $href);
            if ($abs === '' || !preg_match('#^https?://#i', $abs)) {
                continue;
            }
            $page['link_count']++;
            if (strtolower((string)(parse_url($abs, PHP_URL_HOST) ?: '')) === $host) {
                $page['internal_link_count']++;
                $page['links'][] = kseo_normalize_url($abs);
            }
        }
    }
    $page['links'] = array_values(array_unique($page['links']));

    if (preg_match('#<head\b[^>]*>(.*?)</head>#is', $html, $hm)) {
        if (preg_match_all('/<script\b[^>]*\bsrc\s*=[^>]*>/i', $hm[1], $ss)) {
            foreach ($ss[0] as $tag) {
                $a = kseo_attrs($tag);
                if (!array_key_exists('async', $a) && !array_key_exists('defer', $a)) {
                    $page['blocking_scripts']++;
                }
            }
        }
    }
    if (strtolower((string)(parse_url($url, PHP_URL_SCHEME) ?: '')) === 'https') {
        $page['mixed_content'] = (int)preg_match_all('/(?:src|href)="http:\/\//i', $html);
    }

    return $page;
}

// ---------------------------------------------------------------------------
// 判定
// ---------------------------------------------------------------------------

function kseo_finding(string $rule, string $category, string $severity, string $url,
                      string $message, string $evidence, string $action): array
{
    return compact('rule', 'category', 'severity', 'url', 'message', 'evidence', 'action');
}

/** 1ページ分の指摘。Python版 audit_rules.check_page と同じ規則ID・同じ順序。 */
function kseo_check_page(array $p): array
{
    $f = [];
    $url = $p['url'];

    // --- crawl
    if ($p['noindex']) {
        $f[] = kseo_finding('crawl.noindex', 'crawl', 'critical', $url,
            'noindexが指定されています。このページはGoogleの検索結果に一切出ません。',
            'robots: ' . ($p['robots_meta'] !== '' ? $p['robots_meta'] : 'noindex'),
            '検索に出したいページなら、noindexを外してください。');
    }
    if ($p['nofollow_page']) {
        $f[] = kseo_finding('crawl.nofollow', 'crawl', 'warning', $url,
            'ページ全体にnofollowが指定され、リンク先へ評価が渡りません。',
            'robots: ' . $p['robots_meta'],
            '意図がなければnofollowを外してください。');
    }
    if ($p['status'] >= 400) {
        $f[] = kseo_finding('crawl.error_status', 'crawl', 'critical', $url,
            'HTTP ' . $p['status'] . ' を返しています。', 'status: ' . $p['status'],
            'リンク元を直すか、正しいページへ301リダイレクトしてください。');
    }

    // --- meta
    $title = kseo_norm($p['title']);
    if ($title === '') {
        $f[] = kseo_finding('meta.title.missing', 'meta', 'critical', $url,
            'titleタグがありません。検索結果に出す見出しが無い状態です。', 'title: (空)',
            'そのページ固有の内容を表すtitleを設定してください。');
    } else {
        $len = kseo_visible_length($title);
        if ($len > KSEO_TITLE_MAX) {
            $f[] = kseo_finding('meta.title.too_long', 'meta', 'warning', $url,
                'titleが長く、検索結果で末尾が切れます(全角' . KSEO_TITLE_MAX . '文字が目安)。',
                '全角換算' . $len . '文字: ' . $title,
                '重要な語を前半に寄せ、全角' . KSEO_TITLE_MAX . '文字以内に収めてください。');
        } elseif ($len < KSEO_TITLE_MIN) {
            $f[] = kseo_finding('meta.title.too_short', 'meta', 'warning', $url,
                'titleが短く、何のページか検索結果から判断できません。',
                '全角換算' . $len . '文字: ' . $title,
                'サービス名だけでなく、そのページで解決することを入れてください。');
        }
    }
    $desc = kseo_norm($p['description']);
    if ($desc === '') {
        $f[] = kseo_finding('meta.description.missing', 'meta', 'warning', $url,
            'meta descriptionがありません。検索結果の説明文をGoogleに任せている状態です。',
            'description: (空)',
            'ページ内容を要約した説明文を設定してください。クリック率に直結します。');
    } else {
        $len = kseo_visible_length($desc);
        if ($len > KSEO_DESC_MAX) {
            $f[] = kseo_finding('meta.description.too_long', 'meta', 'info', $url,
                'meta descriptionが長く、末尾が切れます(全角' . KSEO_DESC_MAX . '文字が目安)。',
                '全角換算' . $len . '文字',
                '最も伝えたい一文を先頭に置き、全角' . KSEO_DESC_MAX . '文字以内にしてください。');
        } elseif ($len < KSEO_DESC_MIN) {
            $f[] = kseo_finding('meta.description.too_short', 'meta', 'info', $url,
                'meta descriptionが短く、内容を伝えきれていません。',
                '全角換算' . $len . '文字: ' . $desc,
                '全角' . KSEO_DESC_MIN . '〜' . KSEO_DESC_MAX . '文字で、誰の何を解決するかを書いてください。');
        }
    }
    if ($p['canonical'] === '') {
        $f[] = kseo_finding('meta.canonical.missing', 'meta', 'info', $url,
            'canonical URLが指定されていません。同じ内容が複数URLで見えると評価が割れます。',
            'link[rel=canonical]: なし', '正規URLをcanonicalで明示してください。');
    } elseif ($p['canonical_conflict']) {
        $f[] = kseo_finding('meta.canonical.conflict', 'meta', 'warning', $url,
            'canonicalが別ページを指しています。このページは検索結果に出ません。',
            'canonical: ' . $p['canonical'],
            '意図した統合でなければ、canonicalを自分自身のURLに直してください。');
    }

    // --- heading
    if ($p['h1_count'] === 0) {
        $f[] = kseo_finding('heading.h1.missing', 'heading', 'warning', $url,
            'H1見出しがありません。ページの主題が機械に伝わりません。', 'h1: 0個',
            'ページの主題を表すH1を1つ置いてください。');
    } elseif ($p['h1_count'] > 1) {
        $f[] = kseo_finding('heading.h1.multiple', 'heading', 'info', $url,
            'H1が' . $p['h1_count'] . '個あります。主題が分散します。',
            'h1: ' . $p['h1_count'] . '個 / ' . implode(', ', array_slice($p['h1_texts'], 0, 3)),
            'H1は1つにし、以降はH2以下にしてください。');
    }
    if ($p['heading_jumps']) {
        $f[] = kseo_finding('heading.level_jump', 'heading', 'info', $url,
            '見出しの階層が飛んでいます。読み上げと構造解析の妨げになります。',
            implode(' / ', array_slice($p['heading_jumps'], 0, 3)),
            'H2の下はH3、というように1段ずつ下げてください。');
    }
    if ($p['h1_texts'] && $title !== '' && kseo_norm($p['h1_texts'][0]) === $title) {
        $f[] = kseo_finding('heading.h1_equals_title', 'heading', 'info', $url,
            'H1とtitleが完全に同一です。検索結果と本文で違う語を拾う機会を失っています。',
            '共通: ' . mb_substr($title, 0, 60, 'UTF-8'),
            'titleは検索向け、H1は読み手向けに少し表現を変えてください。');
    }

    // --- structured
    if (!$p['jsonld_types']) {
        $f[] = kseo_finding('structured.missing', 'structured', 'warning', $url,
            '構造化データ(JSON-LD)がありません。リッチリザルトの対象外です。',
            'application/ld+json: 0件',
            'Organization / WebSite / BreadcrumbList から始めて追加してください。');
    } else {
        if ($p['jsonld_invalid'] > 0) {
            $f[] = kseo_finding('structured.invalid', 'structured', 'warning', $url,
                'JSON-LDに構文エラーがあり、Googleに読まれません。',
                '壊れているブロック: ' . $p['jsonld_invalid'] . '個',
                'JSONとして妥当か検証してから設置してください。');
        }
        if (!in_array('BreadcrumbList', $p['jsonld_types'], true)) {
            $types = $p['jsonld_types'];
            sort($types);
            $f[] = kseo_finding('structured.breadcrumb', 'structured', 'info', $url,
                'パンくずの構造化データがありません。検索結果に階層が出ません。',
                '検出した型: ' . ($types ? implode(', ', $types) : 'なし'),
                'BreadcrumbListを追加してください。');
        }
    }

    // --- social
    $missing = [];
    foreach (['og:title', 'og:description', 'og:image'] as $key) {
        if (!isset($p['og'][$key])) { $missing[] = $key; }
    }
    if (count($missing) === 3) {
        $f[] = kseo_finding('social.og.missing', 'social', 'warning', $url,
            'OGPがありません。SNSやチャットに貼っても内容が表示されません。',
            'og:title / og:description / og:image いずれも未設定',
            'og:title・og:description・og:image(1200×630)を設定してください。');
    } elseif ($missing) {
        $f[] = kseo_finding('social.og.partial', 'social', 'info', $url,
            'OGPの一部が欠けています。', '不足: ' . implode(', ', $missing),
            '不足しているOGPを補ってください。');
    }

    // --- content
    if ($p['text_length'] < KSEO_THIN_CONTENT) {
        $f[] = kseo_finding('content.thin', 'content', 'warning', $url,
            '本文が' . KSEO_THIN_CONTENT . '文字未満で、検索意図を満たせません。',
            '本文: ' . $p['text_length'] . '文字',
            '具体例・数字・手順を足して、読み手の疑問が解ける量にしてください。');
    }
    if ($p['images_without_alt'] > 0) {
        $f[] = kseo_finding('content.img_alt', 'content', 'info', $url,
            'alt属性の無い画像が' . $p['images_without_alt'] . '個あります。',
            'alt無し ' . $p['images_without_alt'] . '個 / 画像 ' . $p['images_total'] . '個',
            '内容を説明するaltを入れてください(装飾画像はalt="")。');
    }
    if ($p['link_count'] > KSEO_MAX_LINKS) {
        $f[] = kseo_finding('content.too_many_links', 'content', 'info', $url,
            'リンクが' . $p['link_count'] . '本あり、1本あたりの評価が薄まります。',
            'リンク: ' . $p['link_count'] . '本',
            '主要な導線に絞り、' . KSEO_MAX_LINKS . '本以内を目安にしてください。');
    }
    if ($p['internal_link_count'] === 0) {
        $f[] = kseo_finding('content.no_internal_link', 'content', 'warning', $url,
            '内部リンクが1本もありません。孤立ページはクロールも評価も届きません。',
            '内部リンク: 0本', '関連ページへの導線を本文中に置いてください。');
    }

    // --- mobile
    if ($p['viewport'] === '') {
        $f[] = kseo_finding('mobile.viewport.missing', 'mobile', 'critical', $url,
            'viewport指定がありません。スマートフォンで拡大縮小が必要な表示になります。',
            'meta[name=viewport]: なし',
            '<meta name="viewport" content="width=device-width, initial-scale=1"> を入れてください。');
    } else {
        $v = str_replace(' ', '', $p['viewport']);
        if (str_contains($v, 'user-scalable=no') || str_contains($v, 'maximum-scale=1')) {
            $f[] = kseo_finding('mobile.viewport.no_zoom', 'mobile', 'info', $url,
                '拡大を禁止しています。読みづらさとアクセシビリティの問題になります。',
                'viewport: ' . $p['viewport'],
                'user-scalable=no と maximum-scale の指定を外してください。');
        }
    }

    // --- speed
    if ($p['elapsed'] >= KSEO_VERY_SLOW_SEC) {
        $f[] = kseo_finding('speed.very_slow', 'speed', 'critical', $url,
            '応答が' . number_format($p['elapsed'], 1) . '秒かかっています。離脱の主因になります。',
            '応答時間: ' . number_format($p['elapsed'], 2) . '秒 / HTML ' . number_format($p['html_bytes']) . 'バイト',
            'サーバー応答・画像・キャッシュを順に見直してください。');
    } elseif ($p['elapsed'] >= KSEO_SLOW_SEC) {
        $f[] = kseo_finding('speed.slow', 'speed', 'warning', $url,
            '応答が' . number_format($p['elapsed'], 1) . '秒かかっています。',
            '応答時間: ' . number_format($p['elapsed'], 2) . '秒 / HTML ' . number_format($p['html_bytes']) . 'バイト',
            'キャッシュとgzip/brotli圧縮の有無を確認してください。');
    }
    if ($p['blocking_scripts'] > KSEO_MAX_BLOCKING_SCRIPTS) {
        $f[] = kseo_finding('speed.blocking_scripts', 'speed', 'info', $url,
            '描画を止めるスクリプトが' . $p['blocking_scripts'] . '本あります。',
            'head内の同期script: ' . $p['blocking_scripts'] . '本',
            'async / defer を付けるか、body末尾へ移してください。');
    }

    // --- security
    $scheme = strtolower((string)(parse_url($url, PHP_URL_SCHEME) ?: ''));
    if ($scheme !== 'https') {
        $f[] = kseo_finding('security.no_https', 'security', 'critical', $url,
            'HTTPSではありません。ブラウザに警告が出て、検索でも不利になります。',
            'scheme: ' . $scheme,
            '証明書を導入し、HTTPからHTTPSへ301リダイレクトしてください。');
    } elseif ($p['mixed_content'] > 0) {
        $f[] = kseo_finding('security.mixed_content', 'security', 'warning', $url,
            'HTTPSページ内にhttp://の読み込みが' . $p['mixed_content'] . '件あります。',
            'mixed content: ' . $p['mixed_content'] . '件',
            '参照先をhttps://に直してください。');
    }

    // --- japanese
    $body_ja = kseo_has_japanese($p['text_sample']);
    if ($body_ja && $p['lang'] === '') {
        $f[] = kseo_finding('japanese.lang.missing', 'japanese', 'warning', $url,
            '日本語のページなのに html の lang 属性がありません。', 'html[lang]: なし',
            '<html lang="ja"> を指定してください。');
    } elseif ($body_ja && $p['lang'] !== '' && !str_starts_with(strtolower($p['lang']), 'ja')) {
        $f[] = kseo_finding('japanese.lang.mismatch', 'japanese', 'warning', $url,
            '本文は日本語ですが lang="' . $p['lang'] . '" と宣言されています。',
            'html[lang]: ' . $p['lang'],
            'lang="ja" に直してください。誤った言語で配信対象がずれます。');
    }
    if ($p['mojibake'] !== '') {
        $f[] = kseo_finding('japanese.mojibake', 'japanese', 'critical', $url,
            '文字化けを検出しました。文字コード宣言と実体が食い違っています。',
            '検出箇所: ' . $p['mojibake'],
            'meta charset と実際のエンコーディングをUTF-8で揃えてください。');
    }
    if ($p['fullwidth_alnum_in_title'] !== '') {
        $f[] = kseo_finding('japanese.fullwidth_alnum', 'japanese', 'info', $url,
            'titleに全角の英数字が含まれています。半角で検索する人と一致しにくくなります。',
            '該当: ' . $p['fullwidth_alnum_in_title'],
            '英数字は半角に統一してください。');
    }
    if ($p['charset'] === '') {
        $f[] = kseo_finding('japanese.charset.missing', 'japanese', 'info', $url,
            'meta charset の宣言がありません。環境によって文字化けします。',
            'meta[charset]: なし',
            '<meta charset="utf-8"> を head の先頭付近に置いてください。');
    }

    return $f;
}

/** 複数ページを見て初めて分かること（robots・sitemap・重複）。 */
function kseo_check_site(array $site, array $pages): array
{
    $f = [];
    $home = $site['base_url'];

    if ($site['robots_txt'] === null) {
        $f[] = kseo_finding('crawl.robots.missing', 'crawl', 'info', $home,
            'robots.txt がありません。',
            kseo_absolute_url($home, '/robots.txt') . ': 取得できず',
            'robots.txt を置き、sitemapの場所を記載してください。');
    } else {
        if ($site['robots_blocks_all']) {
            $f[] = kseo_finding('crawl.robots.disallow_all', 'crawl', 'critical', $home,
                'robots.txt が全クローラーを拒否しています。サイト全体が検索に出ません。',
                'Disallow: /', '検索に出したいなら Disallow: / を外してください。');
        }
        if ($site['robots_sitemap'] === '') {
            $f[] = kseo_finding('crawl.robots.no_sitemap', 'crawl', 'info', $home,
                'robots.txt に Sitemap の記載がありません。', 'Sitemap行: なし',
                'Sitemap: https://example.com/sitemap.xml を追記してください。');
        }
    }
    if (!$site['sitemap_found']) {
        $f[] = kseo_finding('crawl.sitemap.missing', 'crawl', 'warning', $home,
            'sitemap.xml が見つかりません。新しいページの発見が遅れます。',
            kseo_absolute_url($home, '/sitemap.xml') . ': 取得できず',
            'sitemap.xml を生成し、Search Consoleに送信してください。');
    }

    foreach ([
        ['title', 'title', 'meta.title.duplicate', 'meta'],
        ['meta description', 'description', 'meta.description.duplicate', 'meta'],
    ] as [$label, $key, $rule, $category]) {
        $seen = [];
        foreach ($pages as $p) {
            $value = kseo_norm((string)$p[$key]);
            if ($value !== '') { $seen[$value][] = $p['url']; }
        }
        foreach ($seen as $value => $urls) {
            if (count($urls) > 1) {
                $f[] = kseo_finding($rule, $category, 'warning', $urls[0],
                    '同じ' . $label . 'が' . count($urls) . 'ページで使われています。どれを出すかGoogleが選べません。',
                    mb_substr((string)$value, 0, 50, 'UTF-8') . ' — ' . implode(', ', array_slice($urls, 0, 3))
                        . (count($urls) > 3 ? ' ほか' : ''),
                    'ページごとに固有の' . $label . 'を書いてください。');
            }
        }
    }

    if ($site['broken_links']) {
        $sample = [];
        foreach (array_slice($site['broken_links'], 0, 3) as $b) {
            $sample[] = $b['url'] . '(' . $b['status'] . ')';
        }
        $f[] = kseo_finding('content.broken_link', 'content', 'warning', $home,
            'リンク切れが' . count($site['broken_links']) . '件あります。',
            implode(', ', $sample), 'リンク先を修正するか、リンクを外してください。');
    }

    return $f;
}

/**
 * カテゴリ別と総合のスコア。
 *
 * 総合はカテゴリの平均にしない。10カテゴリの平均だと1カテゴリが壊滅していても
 * 90点台に見えてしまい、noindexで全ページ検索圏外という最悪の状態を
 * 「おおむね良好」と伝えることになる。減点の総量で出す。
 */
function kseo_score(array $findings, int $page_count = 1): array
{
    $pages = max(1, $page_count);
    $penalty = [];
    foreach (array_keys(kseo_categories()) as $key) { $penalty[$key] = 0; }
    $counts = ['critical' => 0, 'warning' => 0, 'info' => 0];
    $total = 0;
    foreach ($findings as $f) {
        $w = KSEO_SEVERITY_WEIGHT[$f['severity']] ?? 5;
        $penalty[$f['category']] = ($penalty[$f['category']] ?? 0) + $w;
        if (isset($counts[$f['severity']])) { $counts[$f['severity']]++; }
        $total += $w;
    }
    $categories = [];
    foreach (kseo_categories() as $key => $label) {
        $categories[$key] = [
            'label' => $label,
            'score' => max(0, min(100, (int)round(100 - $penalty[$key] / $pages))),
        ];
    }
    return [
        'overall' => max(0, min(100, (int)round(100 - $total / $pages))),
        'categories' => $categories,
        'counts' => $counts,
        'pages' => $pages,
        'total' => count($findings),
    ];
}

// ---------------------------------------------------------------------------
// クロール
// ---------------------------------------------------------------------------

function kseo_run_audit(string $base_url, int $max_pages): array
{
    [$ok, $checked] = kseo_validate_url($base_url);
    if (!$ok) {
        return ['error' => $checked];
    }
    $base_url = $checked;
    $host = strtolower((string)(parse_url($base_url, PHP_URL_HOST) ?: ''));
    $site = [
        'base_url' => $base_url, 'robots_txt' => null, 'robots_blocks_all' => false,
        'robots_sitemap' => '', 'sitemap_found' => false, 'sitemap_urls' => [],
        'broken_links' => [],
    ];

    // robots.txt
    $r = kseo_fetch(kseo_absolute_url($base_url, '/robots.txt'));
    if ($r['status'] > 0 && $r['status'] < 400 && $r['body'] !== '') {
        $site['robots_txt'] = $r['body'];
        $agent_all = false;
        foreach (preg_split('/\R/', $r['body']) ?: [] as $line) {
            $line = trim(explode('#', $line, 2)[0]);
            if ($line === '' || !str_contains($line, ':')) { continue; }
            [$k, $v] = array_map('trim', explode(':', $line, 2));
            $k = strtolower($k);
            if ($k === 'user-agent') {
                $agent_all = ($v === '*');
            } elseif ($k === 'disallow' && $agent_all && $v === '/') {
                $site['robots_blocks_all'] = true;
            } elseif ($k === 'sitemap' && $site['robots_sitemap'] === '') {
                // Sitemap行のURLは "https://..." なので : で切れている。復元する。
                $site['robots_sitemap'] = trim(substr($line, strpos($line, ':') + 1));
            }
        }
    }

    // sitemap.xml
    $candidates = array_filter([$site['robots_sitemap'], kseo_absolute_url($base_url, '/sitemap.xml')]);
    foreach ($candidates as $candidate) {
        $s = kseo_fetch($candidate);
        if ($s['status'] <= 0 || $s['status'] >= 400 || $s['body'] === '') { continue; }
        if (!preg_match_all('#<loc>\s*([^<\s]+)\s*</loc>#i', $s['body'], $locs)) { continue; }
        $site['sitemap_found'] = true;
        foreach ($locs[1] as $loc) {
            $site['sitemap_urls'][] = kseo_normalize_url(html_entity_decode($loc, ENT_QUOTES, 'UTF-8'));
        }
        if ($site['sitemap_urls']) { break; }
    }

    // ページ
    $queue = [kseo_normalize_url($base_url)];
    foreach ($site['sitemap_urls'] as $u) {
        if (strtolower((string)(parse_url($u, PHP_URL_HOST) ?: '')) === $host && !in_array($u, $queue, true)) {
            $queue[] = $u;
        }
    }
    $pages = [];
    $seen = [];
    $max_pages = max(1, min($max_pages, KSEO_MAX_PAGES));
    while ($queue && count($pages) < $max_pages) {
        $url = array_shift($queue);
        if (isset($seen[$url])) { continue; }
        $seen[$url] = true;
        if ($pages) {
            // 相手サーバーへ連続アクセスしないための待ち。短くしないこと。
            usleep((int)(KSEO_CRAWL_DELAY * 1000000));
        }
        $res = kseo_fetch($url);
        if ($res['status'] === 0) {
            $pages[] = ['url' => $url, 'error' => $res['error'] ?: '取得できませんでした'] + kseo_parse_page($url, '', 0, 0.0);
            continue;
        }
        if ($res['ctype'] !== '' && !str_contains(strtolower($res['ctype']), 'html')) {
            continue;
        }
        $page = kseo_parse_page($url, $res['body'], $res['status'], $res['elapsed']);
        $pages[] = $page;
        if (!$site['sitemap_urls']) {
            foreach ($page['links'] as $link) {
                if (!isset($seen[$link]) && !in_array($link, $queue, true)) {
                    $queue[] = $link;
                }
            }
        }
    }

    if (!$pages) {
        return ['error' => '対象サイトのページを1件も取得できませんでした。URLを確認してください。'];
    }

    // リンク切れ。相手に迷惑なので上限を切る。
    $candidates = [];
    foreach ($pages as $p) {
        foreach ($p['links'] as $link) {
            if (!isset($seen[$link]) && !in_array($link, $candidates, true)) {
                $candidates[] = $link;
            }
        }
    }
    foreach (array_slice($candidates, 0, 20) as $link) {
        usleep((int)(KSEO_CRAWL_DELAY * 1000000));
        $res = kseo_fetch($link);
        if ($res['status'] === 0 || $res['status'] >= 400) {
            $site['broken_links'][] = ['url' => $link, 'status' => $res['status']];
        }
    }

    $ok_pages = array_values(array_filter($pages, fn($p) => ($p['error'] ?? '') === ''));
    $findings = [];
    foreach ($ok_pages as $p) {
        $findings = array_merge($findings, kseo_check_page($p));
    }
    $findings = array_merge($findings, kseo_check_site($site, $ok_pages));

    $order = ['critical' => 0, 'warning' => 1, 'info' => 2];
    usort($findings, function ($a, $b) use ($order) {
        $d = ($order[$a['severity']] ?? 3) <=> ($order[$b['severity']] ?? 3);
        if ($d !== 0) { return $d; }
        $d = strcmp($a['category'], $b['category']);
        return $d !== 0 ? $d : strcmp($a['rule'], $b['rule']);
    });

    return [
        'url' => $base_url,
        'created_at' => time(),
        'score' => kseo_score($findings, count($ok_pages)),
        'findings' => $findings,
        'pages' => array_map('kseo_page_summary', $pages),
        'site' => [
            'robots_txt' => $site['robots_txt'] !== null,
            'sitemap_found' => $site['sitemap_found'],
            'broken_links' => $site['broken_links'],
        ],
    ];
}

function kseo_page_summary(array $p): array
{
    return [
        'url' => $p['url'], 'status' => $p['status'], 'elapsed' => $p['elapsed'],
        'title' => $p['title'],
        'title_length' => kseo_visible_length(kseo_norm($p['title'])),
        'description_length' => kseo_visible_length(kseo_norm($p['description'])),
        'h1_count' => $p['h1_count'], 'text_length' => $p['text_length'],
        'link_count' => $p['link_count'], 'internal_link_count' => $p['internal_link_count'],
        'images_total' => $p['images_total'], 'images_without_alt' => $p['images_without_alt'],
        'jsonld_types' => $p['jsonld_types'], 'noindex' => $p['noindex'],
        'canonical' => $p['canonical'], 'lang' => $p['lang'], 'error' => $p['error'] ?? '',
    ];
}

// ---------------------------------------------------------------------------
// 保存（DBは使わない。JSONを置くだけ）
// ---------------------------------------------------------------------------

function kseo_data_dir(): string
{
    $dir = KSEO_DATA_DIR;
    if (!is_dir($dir)) {
        @mkdir($dir, 0700, true);
    }
    return $dir;
}

function kseo_save_audit(array $audit): string
{
    $dir = kseo_data_dir();
    $id = date('Ymd-His') . '-' . substr(bin2hex(random_bytes(4)), 0, 8);
    @file_put_contents($dir . '/audit-' . $id . '.json',
        json_encode($audit, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES));
    $files = glob($dir . '/audit-*.json') ?: [];
    if (count($files) > KSEO_KEEP_AUDITS) {
        sort($files);
        foreach (array_slice($files, 0, count($files) - KSEO_KEEP_AUDITS) as $old) {
            @unlink($old);
        }
    }
    return $id;
}

function kseo_load_audit(string $id): ?array
{
    if (!preg_match('/^\d{8}-\d{6}-[a-f0-9]{8}$/', $id)) {
        return null;
    }
    $path = kseo_data_dir() . '/audit-' . $id . '.json';
    if (!is_file($path)) {
        return null;
    }
    $data = json_decode((string)file_get_contents($path), true);
    return is_array($data) ? $data : null;
}

function kseo_list_audits(): array
{
    $files = glob(kseo_data_dir() . '/audit-*.json') ?: [];
    rsort($files);
    $out = [];
    foreach (array_slice($files, 0, 20) as $path) {
        $data = json_decode((string)file_get_contents($path), true);
        if (!is_array($data)) { continue; }
        $out[] = [
            'id' => substr(basename($path), 6, -5),
            'url' => $data['url'] ?? '',
            'overall' => $data['score']['overall'] ?? 0,
            'created_at' => $data['created_at'] ?? 0,
        ];
    }
    return $out;
}

// ---------------------------------------------------------------------------
// 診断書（Markdown）
// ---------------------------------------------------------------------------

function kseo_verdict(int $score): string
{
    if ($score >= 90) { return '大きな問題は見つかりませんでした。'; }
    if ($score >= 70) { return '検索結果での見え方に直接効く改善点が残っています。'; }
    if ($score >= 50) { return '検索評価を損なう問題が複数あります。上から順に対処してください。'; }
    return '検索結果に出ない、または大きく損をしている状態です。至急対応してください。';
}

function kseo_report_markdown(array $audit): string
{
    $sev = ['critical' => '重大', 'warning' => '警告', 'info' => '改善余地'];
    $score = $audit['score'];
    $out = [];
    $out[] = '# SEO診断書';
    $out[] = '';
    $out[] = '- 対象: ' . $audit['url'];
    $out[] = '- 実施: ' . date('Y-m-d H:i', (int)$audit['created_at']) . ' JST';
    $out[] = '- 監査ページ数: ' . $score['pages'];
    $out[] = '- 総合スコア: **' . $score['overall'] . '点** / 100';
    $out[] = '';
    $out[] = kseo_verdict((int)$score['overall']);
    $out[] = '';
    $out[] = '## 指摘の内訳';
    $out[] = '';
    $out[] = '- 重大: ' . $score['counts']['critical'] . '件';
    $out[] = '- 警告: ' . $score['counts']['warning'] . '件';
    $out[] = '- 改善余地: ' . $score['counts']['info'] . '件';
    $out[] = '';
    $out[] = '## カテゴリ別スコア';
    $out[] = '';
    foreach ($score['categories'] as $c) {
        $out[] = '- ' . $c['label'] . ': ' . $c['score'] . '点';
    }
    $out[] = '';
    if ($audit['findings']) {
        $out[] = '## 指摘の詳細';
        $out[] = '';
        $i = 1;
        foreach ($audit['findings'] as $f) {
            $out[] = '### ' . $i . '. [' . ($sev[$f['severity']] ?? $f['severity']) . '] ' . $f['message'];
            $out[] = '';
            $out[] = '- 対象: ' . $f['url'];
            $out[] = '- 実測: ' . $f['evidence'];
            $out[] = '- 対応: ' . $f['action'];
            $out[] = '- 規則ID: `' . $f['rule'] . '`';
            $out[] = '';
            $i++;
        }
    }
    $out[] = '## ページ別の実測値';
    $out[] = '';
    foreach ($audit['pages'] as $p) {
        if (($p['error'] ?? '') !== '') { continue; }
        $out[] = '### ' . $p['url'];
        $out[] = '';
        $out[] = '- title: 全角' . $p['title_length'] . '文字 / description: 全角' . $p['description_length'] . '文字';
        $out[] = '- 本文: ' . $p['text_length'] . '文字 / H1: ' . $p['h1_count'] . '個 / 内部リンク: ' . $p['internal_link_count'] . '本';
        $out[] = '- 応答時間: ' . $p['elapsed'] . '秒 / HTTP ' . $p['status'];
        $out[] = '- 構造化データ: ' . ($p['jsonld_types'] ? implode(', ', $p['jsonld_types']) : 'なし');
        $out[] = '';
    }
    $out[] = '---';
    $out[] = '';
    $out[] = 'この診断書は Kurage SEO が自動生成しました。';
    $out[] = '判定はすべて実測値にもとづく決定論的なチェックで、推測は含みません。';
    return implode("\n", $out);
}

// ===========================================================================
// ここから下は画面。CLI（自己テスト）からrequireしたときは実行しない。
// ===========================================================================

if (PHP_SAPI === 'cli') {
    return;
}

session_start();
date_default_timezone_set('Asia/Tokyo');

function kseo_h(?string $s): string
{
    return htmlspecialchars((string)$s, ENT_QUOTES, 'UTF-8');
}

// --- 認証（任意）
$locked = KSEO_PASSWORD_HASH !== '';
$authed = !$locked || !empty($_SESSION['kseo_ok']);
if ($locked && isset($_POST['password'])) {
    if (password_verify((string)$_POST['password'], KSEO_PASSWORD_HASH)) {
        session_regenerate_id(true);
        $_SESSION['kseo_ok'] = true;
        header('Location: ?');
        exit;
    }
    $login_error = 'パスワードが違います。';
}
if (isset($_GET['logout'])) {
    session_destroy();
    header('Location: ?');
    exit;
}

if (empty($_SESSION['kseo_csrf'])) {
    $_SESSION['kseo_csrf'] = bin2hex(random_bytes(24));
}
$csrf = (string)$_SESSION['kseo_csrf'];

// --- 診断書のダウンロード
if ($authed && isset($_GET['report'])) {
    $audit = kseo_load_audit((string)$_GET['report']);
    if ($audit) {
        header('Content-Type: text/markdown; charset=utf-8');
        header('Content-Disposition: attachment; filename="kseo-report.md"');
        echo kseo_report_markdown($audit);
        exit;
    }
    http_response_code(404);
}

// --- 診断の実行
$audit = null;
$error = '';
if ($authed && $_SERVER['REQUEST_METHOD'] === 'POST' && isset($_POST['url'])) {
    if (!hash_equals($csrf, (string)($_POST['csrf'] ?? ''))) {
        $error = 'ページを再読み込みしてからお試しください。';
    } else {
        @set_time_limit(0);
        $max = max(1, min((int)($_POST['max_pages'] ?? 5), KSEO_MAX_PAGES));
        $audit = kseo_run_audit((string)$_POST['url'], $max);
        if (isset($audit['error'])) {
            $error = $audit['error'];
            $audit = null;
        } else {
            $audit['id'] = kseo_save_audit($audit);
        }
    }
} elseif ($authed && isset($_GET['id'])) {
    $loaded = kseo_load_audit((string)$_GET['id']);
    if ($loaded) {
        $audit = $loaded;
        $audit['id'] = (string)$_GET['id'];
    }
}

$score_class = function (int $s): string {
    return $s >= 90 ? 'good' : ($s >= 70 ? 'mid' : 'bad');
};
?><!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title><?= kseo_h(KSEO_TITLE) ?></title>
<style>
:root{--bg:#f5f8fb;--panel:#fff;--line:#dde6ee;--text:#15334a;--muted:#667b8b;
--primary:#1d6fa5;--primary-dark:#14567f;--critical:#c0392b;--warning:#c77700;
--info:#4b7a94;--good:#1f8a5f}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);line-height:1.75;
font-family:system-ui,-apple-system,"Hiragino Kaku Gothic ProN","Noto Sans JP",sans-serif}
.top{display:flex;align-items:center;justify-content:space-between;gap:16px;
padding:14px 20px;background:var(--panel);border-bottom:1px solid var(--line);flex-wrap:wrap}
.top a.brand{font-weight:700;font-size:1.15rem;color:var(--text);text-decoration:none}
main{max-width:960px;margin:0 auto;padding:22px 16px 60px}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:14px;
padding:20px;margin-bottom:20px}
h1{font-size:1.5rem;margin:0 0 6px}h2{font-size:1.12rem;margin:0 0 14px}
h3{font-size:1rem;margin:18px 0 8px}
form.audit{display:grid;gap:12px;grid-template-columns:1fr 130px auto;align-items:end}
label{display:grid;gap:6px;font-size:.85rem;color:var(--muted)}
input,select{min-height:44px;padding:8px 12px;border:1px solid var(--line);
border-radius:10px;font:inherit;color:var(--text);background:#fff}
input:focus,select:focus{outline:2px solid var(--primary);outline-offset:1px}
button{min-height:44px;padding:0 18px;border-radius:10px;border:1px solid var(--line);
background:#fff;color:var(--text);font:inherit;cursor:pointer}
button.primary{background:var(--primary);border-color:var(--primary);color:#fff;font-weight:700}
button.primary:hover{background:var(--primary-dark)}
.note{color:var(--muted);font-size:.85rem}.note.error{color:var(--critical)}
.summary{display:flex;gap:14px;align-items:center;flex-wrap:wrap;margin-bottom:16px}
.badge{font-weight:700;font-size:1.6rem;min-width:78px;text-align:center;
padding:8px 10px;border-radius:10px;background:#eef4f9}
.badge.good{color:var(--good);background:#e8f6ef}
.badge.mid{color:var(--warning);background:#fdf3e3}
.badge.bad{color:var(--critical);background:#fbeceb}
.cats{display:grid;gap:8px;grid-template-columns:repeat(auto-fill,minmax(215px,1fr));margin:16px 0 22px}
.cat{border:1px solid var(--line);border-radius:10px;padding:10px 12px}
.cat .l{font-size:.8rem;color:var(--muted)}.cat .v{font-weight:700}
.bar{height:6px;border-radius:3px;background:#e8eef4;margin-top:6px;overflow:hidden}
.bar span{display:block;height:100%;background:var(--primary)}
.f{border:1px solid var(--line);border-left-width:4px;border-radius:10px;padding:12px 14px;margin-bottom:10px}
.f.critical{border-left-color:var(--critical)}.f.warning{border-left-color:var(--warning)}
.f.info{border-left-color:var(--info)}
.sev{font-size:.74rem;font-weight:700;padding:2px 8px;border-radius:999px;background:#eef4f9;color:var(--muted)}
.f.critical .sev{background:#fbeceb;color:var(--critical)}
.f.warning .sev{background:#fdf3e3;color:var(--warning)}
.msg{font-weight:700;margin:6px 0 4px}
.ev,.act{font-size:.87rem;color:var(--muted);word-break:break-word}
.ev b,.act b{color:var(--text);font-weight:500}
.u{font-size:.78rem;color:var(--muted);word-break:break-all}
table{width:100%;border-collapse:collapse;font-size:.87rem}
th,td{text-align:left;padding:7px 8px;border-bottom:1px solid var(--line)}
th{color:var(--muted);font-weight:500}
.hist a{color:var(--primary-dark)}
footer{text-align:center;color:var(--muted);font-size:.83rem;padding:16px}
@media(max-width:720px){form.audit{grid-template-columns:1fr}form.audit button{width:100%}}
</style>
</head>
<body>
<header class="top">
  <a class="brand" href="?"><?= kseo_h(KSEO_TITLE) ?></a>
  <?php if ($locked && $authed): ?><a class="note" href="?logout">ログアウト</a><?php endif; ?>
</header>
<main>

<?php if (!$authed): ?>
  <div class="panel">
    <h2>パスワード</h2>
    <?php if (!empty($login_error)): ?><p class="note error"><?= kseo_h($login_error) ?></p><?php endif; ?>
    <form method="post">
      <label><span>パスワード</span><input type="password" name="password" autofocus required></label>
      <p><button class="primary" type="submit">開く</button></p>
    </form>
  </div>
<?php else: ?>

  <div class="panel">
    <h1>日本語サイトのSEOを、実測で診断する</h1>
    <p class="note">
      タイトルの長さは全角で数え、文字コード宣言や lang 属性まで見ます。
      判定はすべて決定論的なチェックで、AIの推測は入りません。
    </p>
    <form class="audit" method="post">
      <input type="hidden" name="csrf" value="<?= kseo_h($csrf) ?>">
      <label><span>診断するURL</span>
        <input type="text" name="url" placeholder="https://example.co.jp/"
               value="<?= kseo_h($_POST['url'] ?? '') ?>" required></label>
      <label><span>ページ数</span>
        <select name="max_pages">
          <?php foreach ([1, 3, 5, 10, 20] as $n): if ($n > KSEO_MAX_PAGES) { continue; } ?>
            <option value="<?= $n ?>" <?= ((int)($_POST['max_pages'] ?? 5) === $n) ? 'selected' : '' ?>><?= $n ?>ページ</option>
          <?php endforeach; ?>
        </select></label>
      <button class="primary" type="submit">診断する</button>
    </form>
    <?php if ($error !== ''): ?><p class="note error"><?= kseo_h($error) ?></p><?php endif; ?>
    <p class="note">1ページごとに<?= kseo_h((string)KSEO_CRAWL_DELAY) ?>秒あけて取得します。20ページなら30秒ほどかかります。</p>
  </div>

  <?php if ($audit): $s = $audit['score']; ?>
    <div class="panel">
      <h2>診断結果</h2>
      <div class="summary">
        <div class="badge <?= $score_class((int)$s['overall']) ?>"><?= (int)$s['overall'] ?></div>
        <div>
          <div style="font-weight:700"><?= kseo_h(kseo_verdict((int)$s['overall'])) ?></div>
          <div class="note">
            <?= kseo_h($audit['url']) ?> ・ <?= (int)$s['pages'] ?>ページを監査 ・
            重大 <?= (int)$s['counts']['critical'] ?> /
            警告 <?= (int)$s['counts']['warning'] ?> /
            改善余地 <?= (int)$s['counts']['info'] ?>
          </div>
        </div>
      </div>
      <div class="cats">
        <?php foreach ($s['categories'] as $c): ?>
          <div class="cat">
            <div class="l"><?= kseo_h($c['label']) ?></div>
            <div class="v"><?= (int)$c['score'] ?>点</div>
            <div class="bar"><span style="width:<?= max(0, min(100, (int)$c['score'])) ?>%"></span></div>
          </div>
        <?php endforeach; ?>
      </div>

      <h3>指摘</h3>
      <?php if (!$audit['findings']): ?>
        <p class="note">指摘はありません。</p>
      <?php else: $sev = ['critical' => '重大', 'warning' => '警告', 'info' => '改善余地']; ?>
        <?php foreach ($audit['findings'] as $f): ?>
          <div class="f <?= kseo_h($f['severity']) ?>">
            <span class="sev"><?= kseo_h($sev[$f['severity']] ?? $f['severity']) ?></span>
            <div class="msg"><?= kseo_h($f['message']) ?></div>
            <div class="ev"><b>実測:</b> <?= kseo_h($f['evidence']) ?></div>
            <div class="act"><b>対応:</b> <?= kseo_h($f['action']) ?></div>
            <div class="u"><?= kseo_h($f['url']) ?></div>
          </div>
        <?php endforeach; ?>
      <?php endif; ?>

      <h3>ページ別の実測値</h3>
      <div style="overflow-x:auto">
      <table>
        <tr><th>URL</th><th>title</th><th>desc</th><th>本文</th><th>H1</th><th>応答</th></tr>
        <?php foreach ($audit['pages'] as $p): if (($p['error'] ?? '') !== '') { continue; } ?>
          <tr>
            <td class="u"><?= kseo_h($p['url']) ?></td>
            <td>全角<?= (int)$p['title_length'] ?></td>
            <td>全角<?= (int)$p['description_length'] ?></td>
            <td><?= (int)$p['text_length'] ?>字</td>
            <td><?= (int)$p['h1_count'] ?></td>
            <td><?= kseo_h((string)$p['elapsed']) ?>秒</td>
          </tr>
        <?php endforeach; ?>
      </table>
      </div>

      <?php if (!empty($audit['id'])): ?>
        <p style="margin-top:16px">
          <a href="?report=<?= kseo_h($audit['id']) ?>"><button type="button">診断書をダウンロード</button></a>
        </p>
      <?php endif; ?>
    </div>
  <?php endif; ?>

  <?php $history = kseo_list_audits(); if ($history): ?>
    <div class="panel hist">
      <h2>これまでの診断</h2>
      <div style="overflow-x:auto">
      <table>
        <tr><th>日時</th><th>URL</th><th>点</th><th></th></tr>
        <?php foreach ($history as $h): ?>
          <tr>
            <td><?= kseo_h(date('Y-m-d H:i', (int)$h['created_at'])) ?></td>
            <td class="u"><?= kseo_h($h['url']) ?></td>
            <td><?= (int)$h['overall'] ?></td>
            <td><a href="?id=<?= kseo_h($h['id']) ?>">開く</a></td>
          </tr>
        <?php endforeach; ?>
      </table>
      </div>
    </div>
  <?php endif; ?>

<?php endif; ?>
</main>
<footer>Kurage SEO — 株式会社エクスブリッジ</footer>
<?php if (($_SERVER['HTTP_HOST'] ?? '') === 'proto.exbridge.jp'): ?><p style="text-align:center;font-size:13px;margin:14px 0;color:#5d6b7a">これはデモです。<a href="https://kappstore.exbridge.jp/app.php?id=c8ffa66502f7d905&amp;ref=kseo" target="_blank" rel="noopener">この製品をオンプレミスで導入する（商品ページ）</a></p><?php endif; ?>
</body>
</html>
