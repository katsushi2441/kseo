# Kurage SEO 導入手順書

この手順書は、Kurage SEO を**自社サーバーで動かす**ための実務手順です。
回数制限もページ数制限もなく、何度でも診断できます。

---

## 0. 必要なもの

| | |
|---|---|
| サーバー | Linux（Ubuntu 22.04 で動作確認）。VPSでも社内サーバーでも可 |
| Python | 3.10 以上 |
| メモリ | 512MB あれば動きます（診断はネットワーク待ちが主で、CPUはほとんど使いません） |
| 外向き通信 | 診断対象サイトへHTTPSで出られること |
| 言語モデル | **任意**。改善案の文章生成に使います。無くても診断は動きます |

**言語モデルは無くても構いません。** 判定（点数と指摘）は言語モデルを使わない
設計なので、モデル無しでも診断・診断書の出力まで完結します。

---

## 1. 設置

```bash
unzip kseo.zip -d /opt/kseo
cd /opt/kseo
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## 2. 設定

```bash
cp .env.example .env
```

`.env` を開いて、最低この2つを決めます。

```ini
KSEO_HOST=127.0.0.1          # 公開する場合は 0.0.0.0
KSEO_PORT=18345              # 空いているポートに変えて構いません
KSEO_INTERNAL_TOKEN=         # ★必ず設定する（後述）
```

**`KSEO_INTERNAL_TOKEN` を空のままにしないでください。** 空だと認証なしで
動くため、社内からも社外からも誰でもAPIを叩けます。長いランダム文字列を入れます。

```bash
python3 -c "import secrets; print(secrets.token_hex(24))"
```

## 3. 起動

```bash
.venv/bin/python -m app
```

別の端末から確認します。

```bash
curl -s http://127.0.0.1:18345/health
# {"ok":true,"service":"kseo","port":18345,"llm_free":false,"llm_paid":false}
```

`llm_*` が false なのは言語モデル未設定という意味で、故障ではありません。

## 4. 常駐させる（systemd）

同梱の `systemd/kseo.service` をパスに合わせて書き換え、**user unit** として置きます。

```bash
mkdir -p ~/.config/systemd/user
cp systemd/kseo.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now kseo
systemctl --user status kseo
```

**user unit にしておくと `sudo` 無しで再起動できます。** system unit にすると
再起動のたびに管理者権限が要り、運用が止まります。

サーバー再起動後も自動で立ち上げるには、1回だけ次を実行します。

```bash
sudo loginctl enable-linger $USER
```

## 5. 使う

ブラウザで `http://<サーバー>:18345/` を開きます。
サイトを登録して「診断する」を押せば結果が出ます。

**外部に公開する場合は、必ず認証付きのリバースプロキシの後ろに置いてください。**
同梱の `public/kseo.php` は、その一例（Xログインで認証してから中継するPHP）です。

---

## 6. 改善案の生成を有効にする（任意）

### 自社サーバーの Ollama を使う（無料）

```ini
KSEO_OLLAMA_BASE_URL=http://<Ollamaのホスト>:11434
KSEO_OLLAMA_MODEL=gemma4:12b-it-qat
```

**思考型モデル（gemma系など）は `think: false` を送る必要があります。**
本製品は既に送っているので設定は不要ですが、自作の処理を足すときは注意してください。
付け忘れると隠れた推論トークンが出力枠を食い潰し、**応答が空で返ります**。
これをサーバーの故障と誤診しないでください。

### DeepSeek を使う（従量課金）

```ini
KSEO_DEEPSEEK_API_KEY=sk-...
KSEO_DEEPSEEK_MODEL=deepseek-v4-flash
```

管理者アカウント（`KSEO_ADMIN_USERS`）は常にOllama側を使います。
無料枠の実行で課金APIを呼ぶと原価がそのまま赤字になるためです。

---

## 7. 制限を自分で決める

```ini
KSEO_FREE_AUDITS_PER_MONTH=3     # 一般ユーザーの月間回数
KSEO_PAID_PAGES_PER_AUDIT=50     # 管理者の1回あたりページ数
KSEO_MAX_SITES_PER_USER=20
KSEO_ADMIN_USERS=あなたのユーザー名
```

**`KSEO_ADMIN_USERS` に自分を入れると、回数もページ数も無制限になります。**
1人で使うなら、これだけで実質無制限です。

---

## 8. 相手のサイトに迷惑をかけないために

```ini
KSEO_CRAWL_DELAY=1.0    # 1ページごとの待ち時間（秒）
KSEO_CRAWL_TIMEOUT=20
```

**この値を0に近づけないでください。** 短くすると診断は速くなりますが、
相手のサーバーに連続アクセスすることになります。他社サイトを診断する
業務で使う場合は、1.0秒以上を維持してください。

リンク切れの確認は1回20件までに制限してあります。

---

## 9. 運用でつまずきやすいところ

**「画面は出るのに全部エラーになる」**
リバースプロキシを使っている場合、バックエンドが `127.0.0.1` にbindしていると
別ホストからは届きません。`KSEO_HOST=0.0.0.0` にするか、同一ホストに置いてください。

**「診断が400で失敗する」**
入力URLの問題です（内部アドレスに解決される、名前が引けない、取得できない）。
本製品は入力起因の失敗を4xxで返します。502で包むと外形監視が「サービス停止」と
誤判定するためです。エラーメッセージにそのまま理由が出ます。

**「内部のサーバーを診断したい」**
できません。利用者が任意のURLを投げられる作りなので、名前解決後のIPが
プライベートアドレスなら拒否します（SSRF対策）。この制限は外せる設計にして
いません。社内サイトを診断したい場合は、そのサイトを一時的に外部から
到達可能にするか、`app/crawler.py` の `validate_public_url()` をご自身の
責任で調整してください。**その場合、任意のURLを外部から投げられる状態で
公開してはいけません。**

**「診断が遅い」**
`KSEO_CRAWL_DELAY` × ページ数です。相手サイトへの行儀のための待ちなので、
短くする前に本当に必要か検討してください。

---

## 10. 判定を自社向けに変える

`app/audit_rules.py` の先頭に閾値がまとまっています。

```python
TITLE_MAX = 32              # 全角。日本語の実務値
DESC_MAX = 120
THIN_CONTENT_CHARS = 300
SLOW_RESPONSE_SEC = 1.5
```

判定を1つ足したいときは、`check_*` 関数を書いて `PAGE_CHECKS` に加えるだけです。
**変更したら必ず `python3 -m pytest tests/ -q` を通してください。**
誤検知は製品として致命的なので、踏んだ誤検知はテストに残す運用にしてあります。

---

## 11. 更新

ソースコードは GitHub で公開しています。

https://github.com/katsushi2441/kseo

`git pull` して `pip install -r requirements.txt` を実行し、
`systemctl --user restart kseo` で反映されます。

---

## サポート

株式会社エクスブリッジ
https://exbridge.jp/

X: [@xb_bittensor](https://x.com/xb_bittensor)
