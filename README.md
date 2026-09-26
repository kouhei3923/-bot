# カスタム日程調整Bot

discord.py製の日程調整専用Bot。以下の流れで毎週自動的に動きます（すべて日本時間）。

- **毎週月曜 9:00**　`@everyone` で今週のアンケートを投稿（金・土・日＋「来られない」にリアクション）
- **毎週水曜 9:00**　「本日23:59が回答期限です」とリマインド投稿
- **毎週木曜 9:00**　リアクションを集計し、一番多い日を開催日として発表（最多リアクションが6人以下なら「今週のカスタムはお休みです」）
- **開催日当日 9:00**　`@everyone` で「本日カスタムです！」と投稿
- **Botにメンション＋「今週休み」と送信**　その週を事前にお休みにできます
  - まだアンケート投稿前（月曜9:00より前）に送った場合 → 次の月曜の投稿をスキップし、「今週のカスタムはお休みです」と自動投稿
  - アンケート投稿後に送った場合 → その場でその週をお休み扱いにします（以降のリマインド・集計・開催メンションはスキップ）
- **Botにメンション＋「ここに投稿」と送信**　そのメッセージを送ったチャンネルを投稿先として記憶し、以降すべての自動投稿がそこに切り替わります（`.env`のCHANNEL_IDは初期値として使われるだけで、この操作をすれば上書きされます）。サーバー管理権限（Manage Server）を持つ人のみ実行できます。

管理者は以下のスラッシュコマンドで手動実行もできます。

- `/custom_post_now` アンケートを今すぐ投稿
- `/custom_remind_now` 締切リマインドを今すぐ投稿
- `/custom_announce_now` 集計結果を今すぐ発表
- `/custom_daycheck_now` 本日が開催日であれば開催メンションを今すぐ投稿

## セットアップ

### 1. Botアプリケーションを作成

1. [Discord Developer Portal](https://discord.com/developers/applications) にアクセスし「New Application」
2. 左メニュー「Bot」→「Reset Token」でトークンを取得（他人に絶対共有しないこと）
3. 同じ画面で **Privileged Gateway Intents** の `MESSAGE CONTENT INTENT` をON
4. 左メニュー「OAuth2」→「URL Generator」で
   - SCOPES: `bot`, `applications.commands`
   - BOT PERMISSIONS: `Send Messages` `Mention Everyone` `Add Reactions` `Read Message History` `Read Messages/View Channels`
   - 生成されたURLをブラウザで開き、自分のサーバーに招待

### 2. チャンネルIDを取得

Discordの「設定」→「詳細設定」→「開発者モード」をON にし、投稿したいチャンネルを右クリック→「IDをコピー」

### 3. パッケージのインストール

```bash
pip install -r requirements.txt
```

### 4. 環境変数を設定

`.env.example` を `.env` にコピーし、`DISCORD_TOKEN` を設定してください。`CHANNEL_ID` は未設定のままでも構いません（起動後にBotをメンションして「ここに投稿」と送れば設定できます）。

```bash
cp .env.example .env
```

### 5. 起動

```bash
python bot.py
```

起動後、Discord上でスラッシュコマンド（`/`）が使えるようになります（反映まで数分かかることがあります）。

## カスタマイズ

- **開催日の曜日を変える**：`bot.py` の `DAY_META`（`offset` は月曜からの日数）を編集
- **開催に必要な最低人数を変える**：`bot.py` の `MIN_PARTICIPANTS` を編集（この人数以下だとお休みになります）
- **投稿・締切・発表の時刻を変える**：`bot.py` 内の各 `@tasks.loop(time=...)` を編集

## 注意事項

- Botを常時起動しておかないと、月曜・水曜の自動投稿は行われません（PC常時起動 or VPS/レンタルサーバーでの稼働を推奨）。
- `.env` はトークンが含まれるため、Gitにコミットしないでください（`.gitignore` 済み）。
- Botトークンが流出した場合は、Developer Portalから即座にリセットしてください。
- `data/state.json` に進行中の週の状態（投稿したメッセージID、開催日など）を保存しています。Botを再起動しても状態は保持されます。
