# カスタム日程調整Bot

discord.py製の日程調整専用Bot。複数のサーバーに入れて使えます（設定・状態はサーバーごとに別々）。投稿先を設定したサーバーでは、以下の流れで毎週自動的に動きます（すべて日本時間）。

- **毎週月曜 9:00**　`@everyone` で今週のアンケートを投稿（金・土・日をそれぞれ別メッセージで投稿し、リアクションで回答。「来れない」は確認用で集計には使いません）
- **毎週水曜 9:00**　「本日が回答期限です」とリマインド投稿
- **毎週木曜 9:00**　リアクションを集計し、一番多い日を開催日として発表（同数なら早い日が優先。最多が最低人数以下なら「今週のカスタムはお休みです」）
- **開催日当日 9:00**　`@everyone` で「本日カスタムです！」と投稿

## 管理者向けの操作（サーバー管理権限 Manage Server が必要）

- **Botにメンション＋「ここに投稿」と送信**　そのチャンネルをこのサーバーの投稿先として記憶します。以降の自動投稿はそこに出ます。**導入したらまずこれを1回送ってください。**
- **Botにメンション＋「来週休み」と送信**　次の月曜の投稿だけをお休みにできます
  - 送った日の次の月曜（月曜に送った場合は翌週の月曜）にアンケートの代わりに「今週のカスタムはお休みです」と自動投稿し、その週のリマインド・集計・開催メンションはスキップ
  - その次の月曜からは通常通りアンケートを投稿します
- `/custom_setup` 開催日の発表文などの設定（指定した項目だけ変更。何も指定しないと現在の設定を表示）
  - 集合時間（例: 22:30）、集合場所（例: 雑談vc）、連絡先チャンネル（遅れる・欠席の連絡先）、最低人数（既定は6。この人数以下だとお休み）
  - 設定した項目だけが発表文に入ります（未設定なら「今週のカスタムは○月○日(曜)に開催します🌟」だけ）
  - 「初期化」でサーバーの設定を初期値に戻します
- `/custom_post_now` アンケートを今すぐ投稿
- `/custom_remind_now` 締切リマインドを今すぐ投稿
- `/custom_announce_now` 集計結果を今すぐ発表
- `/custom_daycheck_now` 本日が開催日であれば開催メンションを今すぐ投稿

## Botを自分のサーバーに入れる

Botの所有者から招待URLをもらい、サーバー管理権限のあるアカウントで開いて追加してください。追加後、投稿したいチャンネルで「ここに投稿」を送れば完了です。必要な権限は `Send Messages` `Mention Everyone` `Add Reactions` `Read Message History` `Read Messages/View Channels` です。

## セットアップ（Bot運営者向け）

### 1. Botアプリケーションを作成

1. [Discord Developer Portal](https://discord.com/developers/applications) にアクセスし「New Application」
2. 左メニュー「Bot」→「Reset Token」でトークンを取得（他人に絶対共有しないこと）
3. 同じ画面で **Privileged Gateway Intents** の `MESSAGE CONTENT INTENT` をON
4. 他の人にも入れてもらう場合は、同じ画面で **公開Bot** をオン（インストールタブの「インストールリンク」が「なし」だと、公開Botをオンにする際にエラーになることがあります。その場合は「Discord提供リンク」にします）
5. 左メニュー「OAuth2」→「URL Generator」で
   - SCOPES: `bot`, `applications.commands`
   - BOT PERMISSIONS: `Send Messages` `Mention Everyone` `Add Reactions` `Read Message History` `Read Messages/View Channels`
   - 生成されたURLが招待URLです

### 2. パッケージのインストール

```bash
pip install -r requirements.txt
```

### 3. 環境変数を設定

`.env.example` を `.env` にコピーし、`DISCORD_TOKEN` を設定してください。

```bash
cp .env.example .env
```

### 4. 起動

```bash
python bot.py
```

起動後、Discord上でスラッシュコマンド（`/`）が使えるようになります（反映まで数分かかることがあります）。

## カスタマイズ

- **開催日の曜日を変える**：`bot.py` の `DAY_META`（`offset` は月曜からの日数）を編集
- **最低人数の既定値を変える**：`bot.py` の `MIN_PARTICIPANTS`（サーバーごとの変更は `/custom_setup`）
- **投稿・締切・発表の時刻を変える**：`bot.py` の `SCHEDULE_HOUR`（全ジョブ共通、JSTの時）を編集。各ジョブの曜日は `JOBS` で変更
- **特定のサーバーに発表文の初期設定を持たせる**：`bot.py` の `PRESET_SETTINGS`（キーはサーバーID）

## 注意事項

- Botを常時起動しておかないと、自動投稿は行われません（Railway等での常時稼働を推奨）。各ジョブは1分ごとに「今日の分が未実行か」を確認する方式なので、9:00にBotが落ちていても、復帰後にその日のうちに実行されます（失敗時は1日最大3回まで再試行）。
- `.env` はトークンが含まれるため、Gitにコミットしないでください（`.gitignore` 済み）。
- Botトークンが流出した場合は、Developer Portalから即座にリセットしてください。
- `data/state.json` にサーバーごとの状態（投稿先、投稿したメッセージID、開催日、設定など）を保存しています。Botを再起動しても状態は保持されます（Railwayでは `/app/data` にVolumeを付けてください）。
- 以前の1サーバー専用形式の `state.json` は、起動時に自動でサーバーごとの形式へ移行されます。
