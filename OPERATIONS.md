# Kurage SEO 運用

## 稼働

- サービス: `kseo.service`（**user unit**。`systemctl --user` で操作する）
- ポート: **18345**（127.0.0.1 のみ bind。外部公開は heteml の `kseo.php` 経由）
- データ: `data/kseo.db`（SQLite・WAL）

```bash
systemctl --user status kseo
systemctl --user restart kseo
journalctl --user -u kseo -n 50
```

## 状態確認

```bash
curl -s http://127.0.0.1:18345/health
# {"ok":true,"service":"kseo","port":18345,"llm_free":true,"llm_paid":false}
```

- `llm_free` が false → Ollama(192.168.0.3) に到達できていないか、モデル名が違う
- `llm_paid` が false → DeepSeek のキーが未設定（無料枠だけなら問題ない）

## よくある誤診断

- **改善案が空で返る** → gemma4 は思考型なので `"think": false` が必須。
  外すと隠れ推論トークンが num_predict を食い潰し、`done_reason: length` で空になる。
  Ollama を再起動しても直らない。**デーモン故障と誤診しないこと。**
- **監査が400で失敗する** → 入力URLの問題（内部宛て・解決不能・取得不可）。
  入力起因は4xxで返す設計なので、502で包まない。外形監視が停止と誤判定するため。
- **監査が遅い** → `KSEO_CRAWL_DELAY`（既定1秒）×ページ数。相手サイトへの
  行儀のための待ちなので、短くする前に本当に必要か考える。

## 誤検知が見つかったら

1. `tests/test_audit_rules.py` に**再現するテストを先に書く**
2. `app/audit_rules.py` か `app/crawler.py` を直す
3. テストを残したままコミットする

誤検知は製品として致命的なので、踏んだものは必ずテストに残す。
第1号は exbridge.jp の charset 誤検知（`http-equiv="Content-Type"` 形式を
見落として「charset宣言なし」と出した）。

## 公開

**公開URL: https://kurage.exbridge.jp/kseo.php**

    ブラウザ → kurage.exbridge.jp/kseo.php (heteml)
            → exbridge.ddns.net:18345 (ルーターNAT)
            → このマシンの kseo.service

配置は `bash scripts/deploy.sh`（FTP。認証は `aixec/.env` から読む）。
送るのは kseo.php / kseo_config.php / assets/kseo.css / assets/kseo.js の4つ。

**バックエンドは `0.0.0.0` にbindする必要がある**（`KSEO_HOST=0.0.0.0`）。
127.0.0.1 のままだと heteml から到達できず、画面は出るが全操作が503になる。
ルーターは18300番台を範囲転送しているので、ポート開放の手作業は要らない
（`ss -ltn` で 0.0.0.0 になっていれば外から届く）。

インターネットに直接口が開くので、**内部トークンが唯一の守り**になる。
`.env` の `KSEO_INTERNAL_TOKEN` と `public/kseo_config.php` の
`KSEO_API_TOKEN` は必ず一致させ、どちらもgitに入れない。

### 公開経路の生死確認

    curl -s "https://kurage.exbridge.jp/kseo.php?api=/health"

`/health` だけは未ログインで通す（利用者データを返さないため）。これが200なら
heteml→ルーター→バックエンドまで生きている。他の経路は未ログインだと401。
バックエンド側のログに heteml のIP(157.7.188.210)が出ることでも確かめられる。
