<?php
// Kurage SEO の公開入口。X認証を通してから 127.0.0.1:18345 の内部APIへ中継する。
//
// 認証はここだけが持ち、FastAPI側は内部トークンしか見ない。認証を2か所に
// 置くと片方だけ直して穴が開くため(kgeoと同じ構え)。
// ローカルIPのURLを利用者に渡さないための入口でもある。
require_once __DIR__ . '/config.php';
require_once __DIR__ . '/auth_common.php';
require_once __DIR__ . '/kseo_config.php';

$THIS_FILE = 'kseo.php';

if (isset($_GET['login'])) {
    header('Location: ' . url2ai_auth_login_url('/' . $THIS_FILE));
    exit;
}
if (isset($_GET['logout'])) {
    header('Location: ' . url2ai_auth_logout_url('/' . $THIS_FILE));
    exit;
}

$auth = url2ai_auth_bootstrap();
$logged_in = !empty($auth['logged_in']);
$session_user = $logged_in ? trim((string)$auth['session_user']) : '';
$is_admin = $logged_in && !empty($auth['is_admin']);

// 管理者は利用者を選んで代理操作できる(利用者が詰まったときの手当て用)。
// バックエンド側でも管理者かどうかを再検証する。
$act_as = '';
if ($is_admin && isset($_GET['as'])) {
    $candidate = trim((string)$_GET['as']);
    if ($candidate !== '' && preg_match('/^[A-Za-z0-9_]{1,200}$/', $candidate)) {
        $act_as = $candidate;
    }
}

if (empty($_SESSION['kseo_csrf'])) {
    $_SESSION['kseo_csrf'] = bin2hex(random_bytes(24));
}
$csrf = (string)$_SESSION['kseo_csrf'];

function kseo_error($status, $detail) {
    http_response_code((int)$status);
    header('Content-Type: application/json; charset=utf-8');
    header('Cache-Control: no-store, max-age=0');
    echo json_encode(array('detail' => $detail), JSON_UNESCAPED_UNICODE);
    exit;
}

// 通す経路を明示的に列挙する。バックエンドに増えた経路が自動で公開されると、
// 管理用の入口まで外から叩けるようになるため、既定は拒否。
// IDは db.new_id() が作る16桁の16進。
function kseo_route_allowed($path, $method) {
    if (in_array($path, array('/health', '/api/usage'), true)) {
        return $method === 'GET';
    }
    if ($path === '/api/sites') {
        return in_array($method, array('GET', 'POST'), true);
    }
    if (preg_match('#^/api/sites/[a-f0-9]{16}$#', $path)) {
        return $method === 'DELETE';
    }
    if (preg_match('#^/api/sites/[a-f0-9]{16}/audits$#', $path)) {
        return in_array($method, array('GET', 'POST'), true);
    }
    if (preg_match('#^/api/audits/[a-f0-9]{16}$#', $path)) {
        return $method === 'GET';
    }
    if (preg_match('#^/api/audits/[a-f0-9]{16}/advice$#', $path)) {
        return $method === 'POST';
    }
    if (preg_match('#^/api/audits/[a-f0-9]{16}/report\.md$#', $path)) {
        return $method === 'GET';
    }
    return false;
}

function kseo_headers($user) {
    $headers = array(
        'X-KSeo-Token: ' . KSEO_API_TOKEN,
        'X-KSeo-User: ' . $user,
        'Content-Type: application/json',
    );
    if (!empty($GLOBALS['act_as'])) {
        $headers[] = 'X-KSeo-Act-As: ' . $GLOBALS['act_as'];
    }
    return $headers;
}

function kseo_proxy($method, $path, $user) {
    $ch = curl_init(rtrim(KSEO_API_BASE, '/') . $path);
    $body = null;
    if (in_array($method, array('POST', 'PUT', 'PATCH'), true)) {
        $body = file_get_contents('php://input');
        if ($body === false) { $body = ''; }
        // 空ボディでもJSONとして妥当な形で渡す。FastAPI側のバリデータが
        // 空文字を422にするため。
        if (trim($body) === '') { $body = '{}'; }
    }
    // 監査は相手サイトを複数ページ取得するので、待ち時間はゆとりを持たせる。
    // 改善案の生成はGemmaで1分前後かかる。
    curl_setopt_array($ch, array(
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_CUSTOMREQUEST  => $method,
        CURLOPT_HTTPHEADER     => kseo_headers($user),
        CURLOPT_TIMEOUT        => 600,
        CURLOPT_CONNECTTIMEOUT => 10,
    ));
    if ($body !== null) {
        curl_setopt($ch, CURLOPT_POSTFIELDS, $body);
    }
    $response = curl_exec($ch);
    $status = (int)curl_getinfo($ch, CURLINFO_HTTP_CODE);
    $ctype = (string)curl_getinfo($ch, CURLINFO_CONTENT_TYPE);
    $error = curl_error($ch);
    curl_close($ch);

    if ($response === false || $status === 0) {
        // 到達できないのはこちらの障害。入力不備と混ぜない。
        kseo_error(503, '診断サービスに接続できませんでした（' . $error . '）');
    }
    http_response_code($status);
    header('Content-Type: ' . ($ctype !== '' ? $ctype : 'application/json; charset=utf-8'));
    header('Cache-Control: no-store, max-age=0');
    if (strpos($ctype, 'text/markdown') !== false) {
        header('Content-Disposition: attachment; filename="kseo-report.md"');
    }
    echo $response;
    exit;
}

// --- API中継 ---------------------------------------------------------------
if (isset($_GET['api'])) {
    $path = (string)$_GET['api'];
    if ($path === '' || $path[0] !== '/') {
        $path = '/' . $path;
    }
    // /health は利用者データを返さないので未ログインでも通す。外形監視から
    // 「公開URL経由でバックエンドまで生きているか」を1回で確かめるための穴。
    // それ以外は必ずログインを要求する。
    if ($path !== '/health' && !$logged_in) {
        kseo_error(401, 'ログインが必要です。');
    }
    // パス以外は通さない(クエリ経由の別経路呼び出しを防ぐ)。
    if (strpos($path, '..') !== false || strpos($path, '?') !== false) {
        kseo_error(400, '不正なリクエストです。');
    }
    $method = strtoupper($_SERVER['REQUEST_METHOD']);
    if (!kseo_route_allowed($path, $method)) {
        kseo_error(404, '存在しない操作です。');
    }
    // 状態を変える操作にはCSRFトークンを要求する。
    if (in_array($method, array('POST', 'PUT', 'PATCH', 'DELETE'), true)) {
        $sent = isset($_SERVER['HTTP_X_CSRF_TOKEN']) ? (string)$_SERVER['HTTP_X_CSRF_TOKEN'] : '';
        if ($sent === '' || !hash_equals($csrf, $sent)) {
            kseo_error(403, 'ページを再読み込みしてからお試しください。');
        }
    }
    $GLOBALS['act_as'] = $act_as;
    kseo_proxy($method, $path, $session_user);
}

// --- 画面 -------------------------------------------------------------------
header('Content-Type: text/html; charset=utf-8');
$owner_label = htmlspecialchars($act_as !== '' ? $act_as . '（代理）' : $session_user, ENT_QUOTES, 'UTF-8');
?><!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Kurage SEO｜日本語サイトのSEO診断</title>
<meta name="description" content="日本語サイトのSEOを実測で診断します。タイトルの長さは全角で数え、文字コードやlang属性まで確認。判定はすべて決定論的で、AIの推測は入りません。">
<meta property="og:title" content="Kurage SEO｜日本語サイトのSEO診断">
<meta property="og:description" content="日本語サイトのSEOを実測で診断。判定は決定論的、改善案だけAIが書きます。">
<meta property="og:type" content="website">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Zen+Maru+Gothic:wght@500;700&family=Noto+Sans+JP:wght@400;500;700&display=swap" rel="stylesheet">
<link rel="stylesheet" href="/assets/kseo.css">
</head>
<body>
<header class="topbar">
  <a class="brand" href="/kseo.php" aria-label="Kurage SEO トップへ">
    <img src="/images/kurage_mascot_simple_v2.png" alt="" width="36" height="36" onerror="this.style.display='none'">
    <span class="brand-name">Kurage SEO</span>
  </a>
  <div class="usage" id="usage"></div>
</header>

<main>
<?php if (!$logged_in): ?>
  <section class="hero">
    <h1>日本語サイトのSEOを、実測で診断する。</h1>
    <p>
      タイトルの長さは全角で数え、文字コードや <code>lang</code> 属性まで見ます。
      判定はすべて決定論的なチェックで、AIの推測は入りません。
    </p>
  </section>
  <section class="panel">
    <h2>はじめる</h2>
    <p>Xアカウントでログインすると、サイトを登録して診断できます。</p>
    <p>
      <a href="?login"><button class="primary">Xでログイン</button></a>
    </p>
    <p class="note">
      ログインすると <a href="https://x.com/xb_bittensor" target="_blank" rel="noopener">@xb_bittensor</a>
      が表示されます。フォローいただけると更新情報が届きます。
      サービスに関するご提案をDMでお送りする場合があります。
    </p>
  </section>
<?php else: ?>
  <section class="hero">
    <h1>日本語サイトのSEOを、実測で診断する。</h1>
    <p>判定はすべて決定論的なチェックです。改善案の文章だけAIが書きます。</p>
  </section>

  <section class="panel">
    <h2>サイトを登録して診断する</h2>
    <form id="siteForm" class="site-form">
      <label><span>サイト名</span>
        <input id="siteName" type="text" maxlength="80" placeholder="株式会社エクスブリッジ" required></label>
      <label><span>URL</span>
        <input id="siteUrl" type="text" maxlength="500" placeholder="https://example.co.jp/" required></label>
      <button type="submit" class="primary">登録する</button>
    </form>
    <p class="note" id="formNote"></p>
  </section>

  <section class="panel">
    <h2>登録したサイト</h2>
    <div id="siteList" class="site-list"><p class="muted">読み込み中…</p></div>
  </section>

  <section class="panel" id="resultPanel" hidden>
    <h2>診断結果</h2>
    <div id="result"></div>
  </section>
<?php endif; ?>
</main>

<footer>
  <p>
    Kurage SEO — <a href="https://exbridge.jp/">株式会社エクスブリッジ</a>
    <?php if ($logged_in): ?> · <?= $owner_label ?> · <a href="?logout">ログアウト</a><?php endif; ?>
  </p>
</footer>

<?php if ($logged_in): ?>
<script>
  // 中継先は同じPHP。fetchのパスを ?api= に寄せることで、公開URLに
  // 内部ポートを一切出さない。
  window.KSEO = {
    base: "/kseo.php?api=",
    csrf: <?= json_encode($csrf) ?>
  };
</script>
<script src="/assets/kseo.js"></script>
<?php endif; ?>
<script async src="https://www.googletagmanager.com/gtag/js?id=G-BP0650KDFR"></script>
<script>window.dataLayer=window.dataLayer||[];function gtag(){dataLayer.push(arguments)}gtag('js',new Date());gtag('config','G-BP0650KDFR');</script>
<script>(function(){var s=document.createElement('script');s.src='https://kurage.exbridge.jp/simpletrack.php?url='+encodeURIComponent(location.href)+'&ref='+encodeURIComponent(document.referrer);document.head.appendChild(s)})();</script>
<?php if (in_array($_SERVER['HTTP_HOST'] ?? '', array('kurage.exbridge.jp', 'proto.exbridge.jp'), true)) { echo '<script src="https://kurage.exbridge.jp/partner-bar.js" defer></script>'; } // 共通ヘッダーと再販パートナー募集（kurage_web/partner-bar.js・当社の公開先だけ） ?>
</body>
</html>
