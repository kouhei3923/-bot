import datetime
import json
import os
import sys

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

STATE_PATH = os.path.join(os.path.dirname(__file__), "data", "state.json")

DEFAULT_STATE = {
    "channel_id": None,
    "monday": None,
    "poll_message_ids": {},
    "decided_date": None,
    "current_week_off": False,
    "off_week_requested": False,
}


def load_state() -> dict:
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            return {**DEFAULT_STATE, **data}
    except (FileNotFoundError, json.JSONDecodeError):
        return dict(DEFAULT_STATE)


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


async def post_poll(channel):
    today = datetime.datetime.now(JST).date()
    monday = today - datetime.timedelta(days=today.weekday())

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

    if state["off_week_requested"]:
        state["off_week_requested"] = False
        state["current_week_off"] = True
        state["decided_date"] = None
        state["poll_message_ids"] = {}
        state["monday"] = datetime.datetime.now(JST).date().isoformat()
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
        reaction = discord.utils.get(message.reactions, emoji=meta["emoji"]) if message else None
        counts[meta["emoji"]] = max(reaction.count - 1, 0) if reaction else 0

    max_count = max(counts.values()) if counts else 0

    if max_count <= MIN_PARTICIPANTS:
        await channel.send("投票が6人以下だったため、今週のカスタムはお休みです。")
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


# 毎週月曜 9:00 に投票アンケートを投稿（事前に休み申請があればお休み投稿に切り替え）
@tasks.loop(time=datetime.time(hour=9, minute=0, tzinfo=JST))
async def post_poll_job():
    if datetime.datetime.now(JST).weekday() != 0:  # 0=月
        return
    await post_poll_or_offweek()


# 毎週水曜 9:00 に本日締切のリマインド
@tasks.loop(time=datetime.time(hour=9, minute=0, tzinfo=JST))
async def post_reminder_job():
    if datetime.datetime.now(JST).weekday() != 2:  # 2=水
        return
    await post_reminder()


# 毎週木曜 9:00 に集計して開催日を発表
@tasks.loop(time=datetime.time(hour=9, minute=0, tzinfo=JST))
async def announce_result_job():
    if datetime.datetime.now(JST).weekday() != 3:  # 3=木
        return
    await announce_result()


# 毎日9:00に、決定した開催日が今日であればメンション投稿
@tasks.loop(time=datetime.time(hour=9, minute=0, tzinfo=JST))
async def check_day_mention_job():
    await check_day_mention()


@post_poll_job.before_loop
@post_reminder_job.before_loop
@announce_result_job.before_loop
@check_day_mention_job.before_loop
async def before_loops():
    await bot.wait_until_ready()


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    print("------")
    if not post_poll_job.is_running():
        post_poll_job.start()
    if not post_reminder_job.is_running():
        post_reminder_job.start()
    if not announce_result_job.is_running():
        announce_result_job.start()
    if not check_day_mention_job.is_running():
        check_day_mention_job.start()
    synced = await bot.tree.sync()
    print(f"Synced {len(synced)} slash command(s).")


def _has_manage_guild(message: discord.Message) -> bool:
    return bool(message.guild) and message.author.guild_permissions.manage_guild


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
    if bot.user not in message.mentions:
        return

    if "ここに投稿" in message.content:
        if not _has_manage_guild(message):
            await message.reply("❌ このコマンドはサーバー管理権限を持つ人のみ実行できます。")
            return
        state["channel_id"] = message.channel.id
        save_state(state)
        await message.reply(f"✅ 今後の投稿はこのチャンネル（{message.channel.mention}）に切り替えました。")
        return

    channel_id = get_channel_id()
    if channel_id and message.channel.id != channel_id:
        return
    if "今週休み" not in message.content:
        return

    if state.get("poll_message_ids") and not state["current_week_off"]:
        state["current_week_off"] = True
        state["decided_date"] = None
        save_state(state)
        await message.reply("承知しました！今週のカスタムはお休みとして扱います。")
    else:
        state["off_week_requested"] = True
        save_state(state)
        await message.reply("承知しました！来週のカスタムはお休みとして扱います（次の月曜の投稿はスキップします）。")


@bot.tree.command(name="custom_post_now", description="【管理者用】カスタムアンケートを今すぐ投稿します")
@app_commands.checks.has_permissions(manage_guild=True)
async def custom_post_now(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    channel = get_channel()
    if channel is None:
        await interaction.followup.send("❌ チャンネルが見つかりません。", ephemeral=True)
        return
    await post_poll(channel)
    await interaction.followup.send("✅ 投稿しました。", ephemeral=True)


@bot.tree.command(name="custom_remind_now", description="【管理者用】締切リマインドを今すぐ投稿します")
@app_commands.checks.has_permissions(manage_guild=True)
async def custom_remind_now(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    await post_reminder()
    await interaction.followup.send("✅ リマインドしました。", ephemeral=True)


@bot.tree.command(name="custom_announce_now", description="【管理者用】集計結果を今すぐ発表します")
@app_commands.checks.has_permissions(manage_guild=True)
async def custom_announce_now(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    await announce_result()
    await interaction.followup.send("✅ 発表しました。", ephemeral=True)


@bot.tree.command(name="custom_daycheck_now", description="【管理者用】本日が開催日であれば開催メンションを今すぐ投稿します")
@app_commands.checks.has_permissions(manage_guild=True)
async def custom_daycheck_now(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
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
