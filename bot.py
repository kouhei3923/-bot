import copy
import datetime
import json
import os
import sys
import traceback

import discord
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv

# Windows環境では標準出力がcp932になり、絵文字などを含む文字列のprintでクラッシュするため固定する
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID") or "0")

JST = datetime.timezone(datetime.timedelta(hours=9))

DAY_META = [
    {"offset": 4, "label": "金", "emoji": "1️⃣"},  # 月曜+4日=金曜
    {"offset": 5, "label": "土", "emoji": "2️⃣"},
    {"offset": 6, "label": "日", "emoji": "3️⃣"},
]
CANT_COME_EMOJI = "❌"

# 最多リアクション数がこの人数を超えないと開催せず見送りにする（6以下で見送り、7以上で開催）
MIN_PARTICIPANTS = 6

# 定期ジョブの実行開始時刻（JST）。この時刻を過ぎてから、その日の分が未実行なら実行する
SCHEDULE_HOUR = 9
# 1つのジョブが失敗し続けたときに、1日に再試行する最大回数
MAX_ATTEMPTS = 3

STATE_PATH = os.path.join(os.path.dirname(__file__), "data", "state.json")

DEFAULT_STATE = {
    "channel_id": None,
    "monday": None,
    "poll_message_ids": {},
    "decided_date": None,
    "current_week_off": False,
    "skip_monday": None,
    "last_run": {},
}


def load_state() -> dict:
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            return {**copy.deepcopy(DEFAULT_STATE), **data}
    except (FileNotFoundError, json.JSONDecodeError):
        return copy.deepcopy(DEFAULT_STATE)


def save_state(state: dict):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)


intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)
state = load_state()


def get_channel_id():
    return state.get("channel_id") or CHANNEL_ID or None


def get_channel():
    channel_id = get_channel_id()
    if not channel_id:
        print("[custom-schedule-bot] 投稿先チャンネルが未設定です。Botをメンションして「ここに投稿」と送るか、.envのCHANNEL_IDを設定してください。")
        return None
    channel = bot.get_channel(channel_id)
    if channel is None:
        print(f"[custom-schedule-bot] チャンネル {channel_id} が見つかりません")
    return channel


def configured_guild_id():
    channel_id = get_channel_id()
    channel = bot.get_channel(channel_id) if channel_id else None
    guild = getattr(channel, "guild", None)
    return guild.id if guild else None


def week_monday(today: datetime.date) -> datetime.date:
    return today - datetime.timedelta(days=today.weekday())


async def post_poll(channel):
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

    state["monday"] = monday.isoformat()
    state["poll_message_ids"] = poll_message_ids
    state["decided_date"] = None
    state["current_week_off"] = False
    save_state(state)


async def post_poll_or_offweek():
    channel = get_channel()
    if channel is None:
        return

    this_monday = week_monday(datetime.datetime.now(JST).date()).isoformat()
    skip_monday = state.get("skip_monday")
    if skip_monday and skip_monday <= this_monday:
        state["skip_monday"] = None
    if skip_monday == this_monday:
        state["current_week_off"] = True
        state["decided_date"] = None
        state["poll_message_ids"] = {}
        state["monday"] = this_monday
        save_state(state)
        await channel.send(
            "@everyone 今週のカスタムはお休みです。",
            allowed_mentions=discord.AllowedMentions(everyone=True),
        )
        return

    await post_poll(channel)


async def post_reminder():
    if state["current_week_off"] or not state.get("poll_message_ids"):
        return
    channel = get_channel()
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


async def announce_result():
    if state["current_week_off"] or not state.get("poll_message_ids"):
        return
    channel = get_channel()
    if channel is None:
        return

    monday_str = state.get("monday")
    monday = datetime.date.fromisoformat(monday_str) if monday_str else None

    counts = {}
    for meta in DAY_META:
        msg_id = state["poll_message_ids"].get(meta["emoji"])
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

    if max_count <= MIN_PARTICIPANTS:
        await channel.send(f"投票が{MIN_PARTICIPANTS}人以下だったため、今週のカスタムはお休みです。")
        state["decided_date"] = None
        save_state(state)
        return

    best_meta = next(m for m in DAY_META if counts[m["emoji"]] == max_count)
    day = (monday + datetime.timedelta(days=best_meta["offset"])) if monday else None
    label = f"{day.month}月{day.day}日({best_meta['label']})" if day else best_meta["label"]

    await channel.send(
        f"@everyone \n今週のカスタムは{label}に開催します🌟\n"
        "22:30に雑談vcに集合してください！開催日が決定したあとに都合がつかなくなったり、"
        "遅れる場合は<#1367045763837722664> に必ず報告をお願いします‼️",
        allowed_mentions=discord.AllowedMentions(everyone=True),
    )
    state["decided_date"] = day.isoformat() if day else None
    save_state(state)


async def check_day_mention():
    if state["current_week_off"] or not state["decided_date"]:
        return
    today = datetime.datetime.now(JST).date().isoformat()
    if today != state["decided_date"]:
        return
    channel = get_channel()
    if channel is None:
        return
    await channel.send(
        "@everyone 本日カスタムです！",
        allowed_mentions=discord.AllowedMentions(everyone=True),
    )
    state["decided_date"] = None  # 同日中の二重投稿を防止
    save_state(state)


# (ジョブ名, 実行する曜日(0=月。Noneは毎日), 処理)
JOBS = [
    ("post_poll", 0, post_poll_or_offweek),  # 月曜: アンケート投稿（休み申請があればお休み投稿）
    ("post_reminder", 2, post_reminder),  # 水曜: 本日締切のリマインド
    ("announce_result", 3, announce_result),  # 木曜: 集計して開催日を発表
    ("check_day_mention", None, check_day_mention),  # 毎日: 開催日当日なら告知
]
_attempts = {}


# 1分ごとに「今日の分で未実行のジョブ」を確認して実行する。
# 9:00ちょうどにBotが落ちていても、復帰後にその日のうちに実行される。
@tasks.loop(minutes=1)
async def scheduler_job():
    now = datetime.datetime.now(JST)
    if now.hour < SCHEDULE_HOUR:
        return
    channel_id = get_channel_id()
    if not channel_id or bot.get_channel(channel_id) is None:
        return

    today = now.date().isoformat()
    for name, weekday, func in JOBS:
        if weekday is not None and now.weekday() != weekday:
            continue
        if state["last_run"].get(name) == today:
            continue
        attempt_date, count = _attempts.get(name, (None, 0))
        if attempt_date != today:
            count = 0
        if count >= MAX_ATTEMPTS:
            continue
        _attempts[name] = (today, count + 1)
        try:
            await func()
        except Exception:
            print(f"[custom-schedule-bot] ジョブ {name} が失敗しました（{count + 1}/{MAX_ATTEMPTS}回目）")
            traceback.print_exc()
            continue
        state["last_run"][name] = today
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
    if not scheduler_job.is_running():
        scheduler_job.start()
    synced = await bot.tree.sync()
    print(f"Synced {len(synced)} slash command(s).")


def _has_manage_guild(message: discord.Message) -> bool:
    return bool(message.guild) and message.author.guild_permissions.manage_guild


def next_monday(today: datetime.date) -> datetime.date:
    """今日より後で最初の月曜日（今日が月曜なら7日後）。"""
    return today + datetime.timedelta(days=7 - today.weekday())


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
    if bot.user not in message.mentions:
        return
    if message.guild is None:
        return

    if "ここに投稿" in message.content:
        is_owner = await bot.is_owner(message.author)
        if not (_has_manage_guild(message) or is_owner):
            await message.reply("❌ このコマンドはサーバー管理権限を持つ人のみ実行できます。")
            return
        current_guild_id = configured_guild_id()
        if current_guild_id and message.guild.id != current_guild_id and not is_owner:
            await message.reply("❌ 投稿先は別のサーバーに設定されています。切り替えはBotの所有者のみ可能です。")
            return
        state["channel_id"] = message.channel.id
        save_state(state)
        await message.reply(f"✅ 今後の投稿はこのチャンネル（{message.channel.mention}）に切り替えました。")
        return

    channel_id = get_channel_id()
    if channel_id and message.channel.id != channel_id:
        return
    if "来週休み" not in message.content:
        return
    if not (_has_manage_guild(message) or await bot.is_owner(message.author)):
        await message.reply("❌ このコマンドはサーバー管理権限を持つ人のみ実行できます。")
        return

    target = next_monday(datetime.datetime.now(JST).date())
    state["skip_monday"] = target.isoformat()
    save_state(state)
    await message.reply(
        f"承知しました！{target.month}/{target.day}（月）の週のカスタムはお休みとして扱います。"
        f"{target.month}/{target.day}の投稿はスキップして、その次の月曜から再開します。"
    )


async def _guard(interaction: discord.Interaction) -> bool:
    """投稿先チャンネルがあるサーバーでの操作だけを許可する。deferした後に呼ぶ。"""
    channel = get_channel()
    if channel is None:
        await interaction.followup.send(
            "❌ 投稿先チャンネルが見つかりません。Botをメンションして「ここに投稿」と送ってください。",
            ephemeral=True,
        )
        return False
    if interaction.guild_id != channel.guild.id:
        await interaction.followup.send("❌ このサーバーは投稿先ではないため使えません。", ephemeral=True)
        return False
    return True


@bot.tree.command(name="custom_post_now", description="【管理者用】カスタムアンケートを今すぐ投稿します")
@app_commands.guild_only()
@app_commands.checks.has_permissions(manage_guild=True)
async def custom_post_now(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    if not await _guard(interaction):
        return
    await post_poll(get_channel())
    await interaction.followup.send("✅ 投稿しました。", ephemeral=True)


@bot.tree.command(name="custom_remind_now", description="【管理者用】締切リマインドを今すぐ投稿します")
@app_commands.guild_only()
@app_commands.checks.has_permissions(manage_guild=True)
async def custom_remind_now(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    if not await _guard(interaction):
        return
    await post_reminder()
    await interaction.followup.send("✅ リマインドしました。", ephemeral=True)


@bot.tree.command(name="custom_announce_now", description="【管理者用】集計結果を今すぐ発表します")
@app_commands.guild_only()
@app_commands.checks.has_permissions(manage_guild=True)
async def custom_announce_now(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    if not await _guard(interaction):
        return
    await announce_result()
    await interaction.followup.send("✅ 発表しました。", ephemeral=True)


@bot.tree.command(name="custom_daycheck_now", description="【管理者用】本日が開催日であれば開催メンションを今すぐ投稿します")
@app_commands.guild_only()
@app_commands.checks.has_permissions(manage_guild=True)
async def custom_daycheck_now(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    if not await _guard(interaction):
        return
    await check_day_mention()
    await interaction.followup.send("✅ 確認しました。", ephemeral=True)


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
    if not CHANNEL_ID and not state.get("channel_id"):
        print(
            "[custom-schedule-bot] 投稿先チャンネル未設定です。起動後にBotをメンションして「ここに投稿」"
            "と送るか、.envのCHANNEL_IDを設定してください。"
        )
    bot.run(TOKEN)
