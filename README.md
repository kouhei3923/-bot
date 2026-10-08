# カスタム日程調整Bot

discord.py製の日程調整専用Bot。以下の流れで毎週自動的に動きます（すべて日本時間）。

- **毎週月曜 9:00**　`@everyone` で今週のアンケートを投稿（金・土・日＋「来られない」にリアクション）
- **毎週水曜 9:00**　「本日23:59が回答期限です」とリマインド投稿
- **毎週木曜 9:00**　リアクションを集計し、一番多い日を開催日として発表（最多リアクションが6人以下なら「今週のカスタムはお休みです」）
- **開催日当日 9:00**　`@everyone` で「本日カスタムです！」と投稿
- **Botにメンション＋「来週休み」と送信**　次の月曜の投稿だけをお休みにできます
  - 送った日の次の月曜（月曜に送った場合は翌週の月曜）にアンケートの代わりに「今週のカスタムはお休みです」と自動投稿し、その週のリマインド・集計・開催メンションはスキップ
  - その次の月曜からは通常通りアンケートを投稿します
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
- **投稿・締切・発表の時刻を変える**：`bot.py` の `SCHEDULE_HOUR`（全ジョブ共通、JSTの時）を編集。各ジョブの曜日は `JOBS` で変更
- **投稿先を別サーバーに移す**：移動先のチャンネルで、Botの所有者（Developer Portalのアプリ所有者）が「ここに投稿」と送信。他サーバーの管理者は切り替えできません

## 注意事項

- Botを常時起動しておかないと、自動投稿は行われません（Railway等での常時稼働を推奨）。各ジョブは1分ごとに「今日の分が未実行か」を確認する方式なので、9:00にBotが落ちていても、復帰後にその日のうちに実行されます（失敗時は1日最大3回まで再試行）。
- 他人に勝手にBotを招待されないよう、Developer Portalの「Bot」で **Public Bot をオフ** にしてください。
- `.env` はトークンが含まれるため、Gitにコミットしないでください（`.gitignore` 済み）。
- Botトークンが流出した場合は、Developer Portalから即座にリセットしてください。
- `data/state.json` に進行中の週の状態（投稿したメッセージID、開催日など）を保存しています。Botを再起動しても状態は保持されます。
