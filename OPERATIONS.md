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

## 公開側

- `public/kseo.php` を heteml の kurage ドメインへ
- `public/kseo_config.php`（gitignore 済み）に `KSEO_API_BASE` と `KSEO_API_TOKEN`
- `static/` を `/kseo_static/` へ
- 内部トークンは `.env` の `KSEO_INTERNAL_TOKEN` と一致させる
