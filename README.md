# Kurage SEO (kseo)

日本語サイトのSEOを実測で診断するサービス。

`kgeo`（AI検索＝GEO/AEO対応の監査）と対になる製品で、こちらは**従来のGoogle検索**を見る。

## 何をするか

1. 対象サイトをクロールする（自前クローラー。robots.txt と sitemap.xml を読む）
2. 決定論的なチェックで採点する（**LLMは使わない**）
3. 改善案の文章だけをLLMに書かせる（無料枠と管理者は gemma4、課金された実行は DeepSeek）
4. 顧客に渡せる診断書（Markdown）を出す

## 設計の芯

**判定にLLMを使わない。** 同じページを2回見て違う答えが出る道具は、監査として売り物にならない。
`app/audit_rules.py` は「測って、閾値と比べて、根拠の数字を添える」だけに徹している。
LLMが登場するのは `app/advice_service.py` の改善文だけで、そこにも実測値しか渡さない。

**日本語の閾値で測る。** 英語圏のSEOツールは title 60文字 / description 160文字を前提にするが、
日本語は全角で数えるのでそのまま当てると誤判定する。`visible_length()` は全角を1文字、
半角を0.5文字として数える。文字コード宣言・`lang` 属性・全角英数字の混在も見る。

**外部の有料データを使わない。** 検索ボリュームや被リンクは外部APIを買わないと取れないので、
この製品では扱わない。扱えるのは「自分のサイトを見れば分かること」だけ、と範囲を決めている。

## 判定カテゴリ

| カテゴリ | 例 |
|---|---|
| クロール・インデックス | noindex、robots.txt の全拒否、sitemap 不在、リダイレクト連鎖 |
| タイトル・説明文 | 全角32文字超、重複title、canonical の食い違い |
| 見出し構造 | H1 不在・複数、見出し階層の飛び |
| 構造化データ | JSON-LD 不在、構文エラー、パンくず不足 |
| SNS表示(OGP) | og:title / og:description / og:image |
| コンテンツ | 本文300文字未満、alt 不足、内部リンク0、リンク切れ |
| モバイル対応 | viewport 不在、拡大禁止 |
| 表示速度 | 応答1.5秒/3.0秒超、head内の同期script |
| HTTPS・安全性 | 非HTTPS、mixed content |
| 日本語サイト固有 | `lang` 不在・不一致、文字化け、charset 不在、title の全角英数字 |

## 動かす

```bash
cd /home/kojima/work/kseo
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env      # KSEO_INTERNAL_TOKEN を設定する
.venv/bin/python -m app   # 127.0.0.1:18345
```

テスト:

```bash
python3 -m pytest tests/ -q
```

## 公開

**https://kurage.exbridge.jp/kseo.php**

`bash scripts/deploy.sh` で heteml へ配置する。X認証とCSRFは `public/kseo.php`
が持ち、FastAPI 側は内部トークンだけを見る（認証を2か所に置かない）。
経路と注意点は [OPERATIONS.md](OPERATIONS.md) を参照。

## ポート

**18345**。`ss -ltn` の実測で空きを確認して決めた。
18308 は kgeo のローカル開発と Kargov が使うため避けている。

## 安全性

利用者が任意のURLを投げてくるので、`crawler.validate_public_url()` が
**名前解決後のIP**を見て内部宛て（プライベート/ループバック/リンクローカル/
メタデータ）を拒否する。ここが緩むと社内の Ollama やサービスをこのサーバー
経由で読まれる（SSRF）。テストで固定してある。
