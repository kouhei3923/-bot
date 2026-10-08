import copy
import datetime
import json
import os
import sys
import traceback
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv

# Windows環境では標準出力がcp932になり、絵文字などを含む文字列のprintでクラッシュするため固定する
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")

JST = datetime.timezone(datetime.timedelta(hours=9))

DAY_META = [
    {"offset": 4, "label": "金", "emoji": "1️⃣"},  # 月曜+4日=金曜
    {"offset": 5, "label": "土", "emoji": "2️⃣"},
    {"offset": 6, "label": "日", "emoji": "3️⃣"},
]
CANT_COME_EMOJI = "❌"

# 最多リアクション数がこの人数を超えないと開催せず見送りにする（既定: 6以下で見送り、7以上で開催）
# サーバーごとに /custom_setup で変更できる
MIN_PARTICIPANTS = 6

# 定期ジョブの実行開始時刻（JST）。この時刻を過ぎてから、その日の分が未実行なら実行する
SCHEDULE_HOUR = 9
# 1つのジョブが失敗し続けたときに、1日に再試行する最大回数
MAX_ATTEMPTS = 3

# 発表文の初期設定をサーバー固有に持たせたい場合（キーはサーバーID）
PRESET_SETTINGS = {
    1366728872380469268: {  # 悠 HARU
        "meeting_time": "22:30",
        "meeting_place": "雑談vc",
        "contact_channel_id": 1367045763837722664,
    },
}

STATE_PATH = os.path.join(os.path.dirname(__file__), "data", "state.json")

DEFAULT_GUILD_STATE = {
    "channel_id": None,
    "monday": None,
    "poll_message_ids": {},
    "decided_date": None,
    "current_week_off": False,
    "skip_monday": None,
    "last_run": {},
    "settings": {},
}
# 1サーバー専用だった旧形式の状態キー（起動時にサーバーごとの形式へ移行する）
LEGACY_KEYS = (
    "channel_id",
    "monday",
    "poll_message_ids",
    "decided_date",
    "current_week_off",
    "skip_monday",
    "last_run",
)


def load_state() -> dict:
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"guilds": {}}
    state = {"guilds": data.get("guilds", {})}
    legacy = data.get("_legacy") or {k: data[k] for k in LEGACY_KEYS if k in data}
    if legacy.get("channel_id"):
        state["_legacy"] = legacy
    return state


def save_state(state: dict):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)


intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)
state = load_state()


def guild_state(guild_id) -> dict:
    g = state["guilds"].setdefault(str(guild_id), {})
    for key, value in DEFAULT_GUILD_STATE.items():
        g.setdefault(key, copy.deepcopy(value))
    return g


def effective_settings(guild_id, g: dict) -> dict:
    return {"min_participants": MIN_PARTICIPANTS, **PRESET_SETTINGS.get(int(guild_id), {}), **g["settings"]}


def guild_channel(g: dict):
    channel_id = g.get("channel_id")
    return bot.get_channel(channel_id) if channel_id else None


def week_monday(today: datetime.date) -> datetime.date:
    return today - datetime.timedelta(days=today.weekday())


def next_monday(today: datetime.date) -> datetime.date:
    """今日より後で最初の月曜日（今日が月曜なら7日後）。"""
    return today + datetime.timedelta(days=7 - today.weekday())


def migrate_legacy():
    legacy = state.get("_legacy")
    if not legacy:
        return
    channel = bot.get_channel(legacy.get("channel_id"))
    guild = getattr(channel, "guild", None)
    if guild is None:
        print("[custom-schedule-bot] 旧形式の設定の移行先サーバーが見つかりません。")
        return
    g = guild_state(guild.id)
    if g["channel_id"] is None:
        for key in LEGACY_KEYS:
            if key in legacy:
                g[key] = legacy[key]
    del state["_legacy"]
    save_state(state)
    print(f"[custom-schedule-bot] 旧形式の設定をサーバー {guild.name}({guild.id}) へ移行しました。")


def build_announce_text(guild_id, g: dict, label: str) -> str:
    s = effective_settings(guild_id, g)
    parts = []
    meeting_time, meeting_place = s.get("meeting_time"), s.get("meeting_place")
    if meeting_time and meeting_place:
        parts.append(f"{meeting_time}に{meeting_place}に集合してください！")
    elif meeting_time:
        parts.append(f"{meeting_time}に集合してください！")
    elif meeting_place:
        parts.append(f"{meeting_place}に集合してください！")
    contact_channel_id = s.get("contact_channel_id")
    if contact_channel_id:
        parts.append(
            "開催日が決定したあとに都合がつかなくなったり、"
            f"遅れる場合は<#{contact_channel_id}> に必ず報告をお願いします‼️"
        )
    text = f"@everyone \n今週のカスタムは{label}に開催します🌟"
    if parts:
        text += "\n" + "".join(parts)
    return text


async def post_poll(gid: str, channel):
    g = guild_state(gid)
    today = datetime.datetime.now(JST).date()
    monday = week_monday(today)

    intro_lines = [
        "@everyone",
        "今週のカスタムアンケートです。参加可能な日にリアクション押してください！都合が悪い場合は「来れない」にリアクションをお願いします",
        "",
        "回答期限　毎週水曜日の23:59",
        "",
        "無回答＆開催日決定後のリアクション取り消しや追加は禁止です‼️",
    ]
    await channel.send(
        "\n".join(intro_lines),
        allowed_mentions=discord.AllowedMentions(everyone=True),
    )

    poll_message_ids = {}
    for meta in DAY_META:
        day = monday + datetime.timedelta(days=meta["offset"])
        day_message = await channel.send(f"{day.month}/{day.day}（{meta['label']}）")
        await day_message.add_reaction(meta["emoji"])
        poll_message_ids[meta["emoji"]] = day_message.id

    cant_come_message = await channel.send("来れない")
    await cant_come_message.add_reaction(CANT_COME_EMOJI)

    g["monday"] = monday.isoformat()
    g["poll_message_ids"] = poll_message_ids
    g["decided_date"] = None
    g["current_week_off"] = False
    save_state(state)


async def post_poll_or_offweek(gid: str):
    g = guild_state(gid)
    channel = guild_channel(g)
    if channel is None:
        return

    this_monday = week_monday(datetime.datetime.now(JST).date()).isoformat()
    skip_monday = g.get("skip_monday")
    if skip_monday and skip_monday <= this_monday:
        g["skip_monday"] = None
    if skip_monday == this_monday:
        g["current_week_off"] = True
        g["decided_date"] = None
        g["poll_message_ids"] = {}
        g["monday"] = this_monday
        save_state(state)
        await channel.send(
            "@everyone 今週のカスタムはお休みです。",
            allowed_mentions=discord.AllowedMentions(everyone=True),
        )
        return

    await post_poll(gid, channel)


async def post_reminder(gid: str):
    g = guild_state(gid)
    if g["current_week_off"] or not g.get("poll_message_ids"):
        return
    channel = guild_channel(g)
    if channel is None:
        return
    await channel.send(
        "@everyone 本日カスタムアンケート締切日です、回答まだの方はご対応お願いします🙏🏻",
        allowed_mentions=discord.AllowedMentions(everyone=True),
    )


async def count_votes(message: discord.Message, emoji: str) -> int:
    reaction = discord.utils.get(message.reactions, emoji=emoji)
    if reaction is None:
        return 0
    return sum([1 async for user in reaction.users() if not user.bot])


async def announce_result(gid: str):
    g = guild_state(gid)
    if g["current_week_off"] or not g.get("poll_message_ids"):
        return
    channel = guild_channel(g)
    if channel is None:
        return

    monday_str = g.get("monday")
    monday = datetime.date.fromisoformat(monday_str) if monday_str else None

    counts = {}
    for meta in DAY_META:
        msg_id = g["poll_message_ids"].get(meta["emoji"])
        message = None
        if msg_id:
            try:
                message = await channel.fetch_message(msg_id)
            except discord.NotFound:
                message = None
        if message is None:
            await channel.send(
                "⚠️ アンケートのメッセージが見つからず集計できませんでした。"
                "管理者が開催日を手動で決めてください。"
            )
            return
        counts[meta["emoji"]] = await count_votes(message, meta["emoji"])

    max_count = max(counts.values()) if counts else 0
    min_participants = effective_settings(gid, g)["min_participants"]

    if max_count <= min_participants:
        await channel.send(f"投票が{min_participants}人以下だったため、今週のカスタムはお休みです。")
        g["decided_date"] = None
        save_state(state)
        return

    best_meta = next(m for m in DAY_META if counts[m["emoji"]] == max_count)
    day = (monday + datetime.timedelta(days=best_meta["offset"])) if monday else None
    label = f"{day.month}月{day.day}日({best_meta['label']})" if day else best_meta["label"]

    await channel.send(
        build_announce_text(gid, g, label),
        allowed_mentions=discord.AllowedMentions(everyone=True),
    )
    g["decided_date"] = day.isoformat() if day else None
    save_state(state)


async def check_day_mention(gid: str):
    g = guild_state(gid)
    if g["current_week_off"] or not g["decided_date"]:
        return
    today = datetime.datetime.now(JST).date().isoformat()
    if today != g["decided_date"]:
        return
    channel = guild_channel(g)
    if channel is None:
        return
    await channel.send(
        "@everyone 本日カスタムです！",
        allowed_mentions=discord.AllowedMentions(everyone=True),
    )
    g["decided_date"] = None  # 同日中の二重投稿を防止
    save_state(state)


# (ジョブ名, 実行する曜日(0=月。Noneは毎日), 処理)
JOBS = [
    ("post_poll", 0, post_poll_or_offweek),  # 月曜: アンケート投稿（休み申請があればお休み投稿）
    ("post_reminder", 2, post_reminder),  # 水曜: 本日締切のリマインド
    ("announce_result", 3, announce_result),  # 木曜: 集計して開催日を発表
    ("check_day_mention", None, check_day_mention),  # 毎日: 開催日当日なら告知
]
_attempts = {}


# 1分ごとに、投稿先を設定済みの各サーバーについて「今日の分で未実行のジョブ」を確認して実行する。
# 9:00ちょうどにBotが落ちていても、復帰後にその日のうちに実行される。
@tasks.loop(minutes=1)
async def scheduler_job():
    now = datetime.datetime.now(JST)
    if now.hour < SCHEDULE_HOUR:
        return
    today = now.date().isoformat()

    for gid, g in list(state["guilds"].items()):
        if guild_channel(g) is None:
            continue
        for name, weekday, func in JOBS:
            if weekday is not None and now.weekday() != weekday:
                continue
            if g["last_run"].get(name) == today:
                continue
            attempt_date, count = _attempts.get((gid, name), (None, 0))
            if attempt_date != today:
                count = 0
            if count >= MAX_ATTEMPTS:
                continue
            _attempts[(gid, name)] = (today, count + 1)
            try:
                await func(gid)
            except Exception:
                print(f"[custom-schedule-bot] サーバー{gid}のジョブ {name} が失敗しました（{count + 1}/{MAX_ATTEMPTS}回目）")
                traceback.print_exc()
                continue
            g["last_run"][name] = today
            save_state(state)


@scheduler_job.before_loop
async def before_scheduler():
    await bot.wait_until_ready()


@scheduler_job.error
async def on_scheduler_error(error):
    print("[custom-schedule-bot] スケジューラで想定外のエラー。再起動します。")
    traceback.print_exception(type(error), error, error.__traceback__)
    if not scheduler_job.is_running():
        scheduler_job.restart()


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    print("------")
    migrate_legacy()
    if not scheduler_job.is_running():
        scheduler_job.start()
    synced = await bot.tree.sync()
    print(f"Synced {len(synced)} slash command(s).")


async def _is_admin(message: discord.Message) -> bool:
    if message.guild is not None and message.author.guild_permissions.manage_guild:
        return True
    return await bot.is_owner(message.author)


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
    if bot.user not in message.mentions:
        return
    if message.guild is None:
        return

    if "ここに投稿" in message.content:
        if not await _is_admin(message):
            await message.reply("❌ このコマンドはサーバー管理権限を持つ人のみ実行できます。")
            return
        g = guild_state(message.guild.id)
        g["channel_id"] = message.channel.id
        save_state(state)
        await message.reply(f"✅ 今後の投稿はこのチャンネル（{message.channel.mention}）に切り替えました。")
        return

    if "来週休み" not in message.content:
        return
    g = guild_state(message.guild.id)
    if g["channel_id"] and message.channel.id != g["channel_id"]:
        return
    if not await _is_admin(message):
        await message.reply("❌ このコマンドはサーバー管理権限を持つ人のみ実行できます。")
        return

    target = next_monday(datetime.datetime.now(JST).date())
    g["skip_monday"] = target.isoformat()
    save_state(state)
    await message.reply(
        f"承知しました！{target.month}/{target.day}（月）の週のカスタムはお休みとして扱います。"
        f"{target.month}/{target.day}の投稿はスキップして、その次の月曜から再開します。"
    )


async def _guard(interaction: discord.Interaction):
    """このサーバーの投稿先が設定済みなら、そのサーバーIDを返す。deferした後に呼ぶ。"""
    gid = str(interaction.guild_id)
    if guild_channel(guild_state(gid)) is None:
        await interaction.followup.send(
            "❌ 投稿先チャンネルが設定されていません。投稿したいチャンネルでBotをメンションして「ここに投稿」と送ってください。",
            ephemeral=True,
        )
        return None
    return gid


@bot.tree.command(name="custom_post_now", description="【管理者用】カスタムアンケートを今すぐ投稿します")
@app_commands.guild_only()
@app_commands.checks.has_permissions(manage_guild=True)
async def custom_post_now(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    gid = await _guard(interaction)
    if gid is None:
        return
    await post_poll(gid, guild_channel(guild_state(gid)))
    await interaction.followup.send("✅ 投稿しました。", ephemeral=True)


@bot.tree.command(name="custom_remind_now", description="【管理者用】締切リマインドを今すぐ投稿します")
@app_commands.guild_only()
@app_commands.checks.has_permissions(manage_guild=True)
async def custom_remind_now(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    gid = await _guard(interaction)
    if gid is None:
        return
    await post_reminder(gid)
    await interaction.followup.send("✅ リマインドしました。", ephemeral=True)


@bot.tree.command(name="custom_announce_now", description="【管理者用】集計結果を今すぐ発表します")
@app_commands.guild_only()
@app_commands.checks.has_permissions(manage_guild=True)
async def custom_announce_now(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    gid = await _guard(interaction)
    if gid is None:
        return
    await announce_result(gid)
    await interaction.followup.send("✅ 発表しました。", ephemeral=True)


@bot.tree.command(name="custom_daycheck_now", description="【管理者用】本日が開催日であれば開催メンションを今すぐ投稿します")
@app_commands.guild_only()
@app_commands.checks.has_permissions(manage_guild=True)
async def custom_daycheck_now(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    gid = await _guard(interaction)
    if gid is None:
        return
    await check_day_mention(gid)
    await interaction.followup.send("✅ 確認しました。", ephemeral=True)


def _format_settings(gid: str) -> str:
    g = guild_state(gid)
    s = effective_settings(gid, g)
    channel = guild_channel(g)
    contact = f"<#{s['contact_channel_id']}>" if s.get("contact_channel_id") else "なし"
    return (
        f"投稿先チャンネル: {channel.mention if channel else '未設定（Botをメンションして「ここに投稿」）'}\n"
        f"集合時間: {s.get('meeting_time') or 'なし'}\n"
        f"集合場所: {s.get('meeting_place') or 'なし'}\n"
        f"連絡先チャンネル: {contact}\n"
        f"最低人数: {s['min_participants']}人（この人数以下だとお休み）"
    )


def _clean_text_setting(value: str):
    value = value.strip()
    return None if value in ("", "なし") else value


@bot.tree.command(
    name="custom_setup",
    description="【管理者用】開催日の発表文などの設定を変更します（指定しない項目は変わりません）",
)
@app_commands.guild_only()
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.describe(
    meeting_time="集合時間（例: 22:30）。「なし」で削除",
    meeting_place="集合場所（例: 雑談vc）。「なし」で削除",
    contact_channel="遅れる・欠席するときの連絡先チャンネル",
    clear_contact="連絡先チャンネルの設定を削除する",
    min_participants="開催に必要な最低人数（この人数以下だとお休み）",
    reset="すべての設定を初期値に戻す",
)
@app_commands.rename(
    meeting_time="集合時間",
    meeting_place="集合場所",
    contact_channel="連絡先チャンネル",
    clear_contact="連絡先を削除",
    min_participants="最低人数",
    reset="初期化",
)
async def custom_setup(
    interaction: discord.Interaction,
    meeting_time: Optional[str] = None,
    meeting_place: Optional[str] = None,
    contact_channel: Optional[discord.TextChannel] = None,
    clear_contact: bool = False,
    min_participants: Optional[app_commands.Range[int, 0, 1000]] = None,
    reset: bool = False,
):
    gid = str(interaction.guild_id)
    g = guild_state(gid)

    for value in (meeting_time, meeting_place):
        if value is not None and len(value) > 50:
            await interaction.response.send_message("❌ 文字数が長すぎます（50文字まで）。", ephemeral=True)
            return

    changed = False
    if reset:
        g["settings"] = {}
        changed = True
    if meeting_time is not None:
        g["settings"]["meeting_time"] = _clean_text_setting(meeting_time)
        changed = True
    if meeting_place is not None:
        g["settings"]["meeting_place"] = _clean_text_setting(meeting_place)
        changed = True
    if contact_channel is not None:
        g["settings"]["contact_channel_id"] = contact_channel.id
        changed = True
    elif clear_contact:
        g["settings"]["contact_channel_id"] = None
        changed = True
    if min_participants is not None:
        g["settings"]["min_participants"] = min_participants
        changed = True
    if changed:
        save_state(state)

    title = "✅ 設定を更新しました。" if changed else "現在の設定です。"
    await interaction.response.send_message(f"{title}\n{_format_settings(gid)}", ephemeral=True)


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        message = "❌ このコマンドを実行する権限がありません。"
    else:
        raise error

    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)


if __name__ == "__main__":
    if not TOKEN:
        raise RuntimeError(
            "DISCORD_TOKEN が設定されていません。.env ファイルを作成し、"
            "DISCORD_TOKEN=あなたのトークン を記述してください。"
        )
    bot.run(TOKEN)
