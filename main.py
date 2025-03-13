import yaml
from datetime import datetime, time

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
    PicklePersistence,
)

# 定义“啊啊啊”消息，重复 100 次“啊”
SCREAM_MESSAGE = "啊" * 100

# 从 config.yaml 中读取 PROXY_URL 和 TOKEN
with open("config.yaml", "r", encoding="utf-8") as f:
    config = yaml.safe_load(f)
PROXY_URL = config.get("PROXY_URL", "")
TOKEN = config.get("TOKEN", "")

# ========== 工具函数部分 ==========
def get_chat_info(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> dict:
    """
    获取某个 chat_id 对应的状态字典，如果不存在则初始化。
    这相当于以前的 GLOBAL_JOBS[chat_id] 的作用。
    """
    bot_data = context.bot_data
    if "GLOBAL_JOBS" not in bot_data:
        bot_data["GLOBAL_JOBS"] = {}

    if chat_id not in bot_data["GLOBAL_JOBS"]:
        bot_data["GLOBAL_JOBS"][chat_id] = {
            "job": None,               # 当前在跑的 (自动或手动) scream job
            "job_manual": False,       # 当前 job 是否是手动
            "auto_trigger_job": None,  # “过 x 分钟无消息后” 再次启动自动嚎叫的 “定时检查” 任务
            "auto_enabled": True,      # 是否允许自动嚎叫
            "curfew": False,           # 是否在宵禁中
        }
    return bot_data["GLOBAL_JOBS"][chat_id]


def get_known_chats(context: ContextTypes.DEFAULT_TYPE) -> set:
    """
    获取所有已知群组的集合，如果不存在则初始化。
    这相当于以前的 KNOWN_CHATS
    """
    if "KNOWN_CHATS" not in context.bot_data:
        context.bot_data["KNOWN_CHATS"] = set()
    return context.bot_data["KNOWN_CHATS"]


# ========== 嚎叫回调 ==========
async def scream_callback(context: ContextTypes.DEFAULT_TYPE):
    """
    真正发送“啊啊啊”的回调函数，用于定时调用。
    """
    chat_id = context.job.data
    await context.bot.send_message(chat_id=chat_id, text=SCREAM_MESSAGE)


# ========== 指令处理函数 ==========
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /help 指令
    """
    print("[DEBUG] Help called")
    if update.effective_chat.type in ["group", "supergroup"]:
        get_known_chats(context).add(update.effective_chat.id)

    help_text = (
        "这个机器人会定期喊‘啊啊啊啊...’。\n"
        "使用 /on x 开始每隔 x 分钟喊一次(可浮点数)。\n"
        "使用 /off 停止喊叫。\n"
        "使用 /enable_auto 打开自动触发。\n"
        "使用 /disable_auto 关闭自动触发。"
    )
    await update.message.reply_text(help_text)

    # 试图重置自动触发定时器
    await reset_auto_trigger(update, context)


async def off_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /off 指令
    停止当前群里正在运行的任何 Scream job（不论自动还是手动）。
    """
    chat_id = update.effective_chat.id
    chat_info = get_chat_info(context, chat_id)

    if update.effective_chat.type in ["group", "supergroup"]:
        get_known_chats(context).add(chat_id)

    if chat_info["job"]:
        # 尝试取消
        try:
            chat_info["job"].schedule_removal()
        except Exception as e:
            print(f"Error while removing job in off_command: {e}")

        # 重置
        chat_info["job"] = None
        chat_info["job_manual"] = False
        await update.message.reply_text("喊叫已停止。")
    else:
        await update.message.reply_text("当前没有在喊叫。")

    # 重置自动触发定时器（如果开启了自动）
    await reset_auto_trigger(update, context)


async def on_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /on x 指令
    启动一个手动喊叫任务，每隔 x 分钟喊一次
    """
    print("[DEBUG] On called")
    if len(context.args) != 1:
        await update.message.reply_text("用法: /on x，其中 x 是分钟间隔（可以是浮点数）。")
        return

    try:
        x = float(context.args[0])
        if x <= 0:
            await update.message.reply_text("间隔必须大于 0。")
            return
    except ValueError:
        await update.message.reply_text("请提供一个正数作为 x。")
        return

    chat_id = update.effective_chat.id
    chat_info = get_chat_info(context, chat_id)
    get_known_chats(context).add(chat_id)

    # 先移除当前正在运行的 job（不论自动还是手动），避免重复
    if chat_info["job"]:
        try:
            chat_info["job"].schedule_removal()
        except Exception as e:
            print(f"Error removing existing job in on_command: {e}")
        chat_info["job"] = None
        chat_info["job_manual"] = False

    interval_seconds = x * 60
    job = context.job_queue.run_repeating(
        scream_callback,
        interval=interval_seconds,
        first=0,
        data=chat_id
    )

    chat_info["job"] = job
    chat_info["job_manual"] = True

    await update.message.reply_text(f"开始每隔 {x} 分钟喊叫，已立即启动。")

    # 重置自动触发定时器（如果开启了自动）
    await reset_auto_trigger(update, context)


async def enable_auto_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /enable_auto 指令
    启用本群的自动触发功能，并重置自动触发定时器
    """
    chat_id = update.effective_chat.id
    chat_info = get_chat_info(context, chat_id)
    get_known_chats(context).add(chat_id)

    chat_info["auto_enabled"] = True
    await update.message.reply_text("自动触发已启用。")

    await reset_auto_trigger(update, context)


async def disable_auto_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /disable_auto 指令
    禁用本群的自动触发功能，并取消 pending 的自动触发定时任务
    """
    chat_id = update.effective_chat.id
    chat_info = get_chat_info(context, chat_id)
    get_known_chats(context).add(chat_id)

    chat_info["auto_enabled"] = False

    # 取消 pending 的自动触发任务
    if chat_info["auto_trigger_job"] is not None:
        try:
            chat_info["auto_trigger_job"].schedule_removal()
        except Exception as e:
            print(f"Error removing auto_trigger_job in disable_auto_command: {e}")
        chat_info["auto_trigger_job"] = None

    await update.message.reply_text("自动触发已关闭。")


# ========== 自动触发相关 ==========
async def auto_trigger_callback(context: ContextTypes.DEFAULT_TYPE):
    """
    当群组在一段时间内没有消息时触发。
    如果此时没有手动 job 正在跑，则开启自动嚎叫（每小时重复）
    """
    chat_id = context.job.data["chat_id"]
    chat_info = get_chat_info(context, chat_id)

    # 如果已在宵禁 或 已有任何 job（手动或者自动）在运行，就啥也不做
    if chat_info["curfew"]:
        print(f"auto_trigger_callback skipped because curfew is True for chat {chat_id}")
        return

    if chat_info["job"] is not None:
        # 已经有 job 在跑，可能是手动也可能是自动，都不重复创建
        return

    # 启动自动嚎叫
    job = context.job_queue.run_repeating(
        scream_callback,
        interval=60 * 60,  # 每小时一次
        first=1,
        data=chat_id
    )
    chat_info["job"] = job
    chat_info["job_manual"] = False

    try:
        await context.bot.send_message(chat_id=chat_id, text="超过60分钟没有消息，自动开启嚎叫！")
    except Exception as e:
        print(f"Error sending auto-trigger message to {chat_id}: {e}")

    # 既然自动嚎叫已经开始，不再需要 auto_trigger_job
    chat_info["auto_trigger_job"] = None


async def reset_auto_trigger(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    每次收到命令时，尝试重新安排一个“检测是否 60 分钟无消息后自动触发”的 once 任务。
    但要遵循以下条件：
    - 必须在群聊中
    - 必须已启用 auto_enabled
    - 当前不处于宵禁时段
    """
    if update.effective_chat.type not in ["group", "supergroup"]:
        return

    chat_id = update.effective_chat.id
    chat_info = get_chat_info(context, chat_id)
    get_known_chats(context).add(chat_id)

    # 如果用户已关闭自动触发，则不安排
    if not chat_info["auto_enabled"]:
        return

    # 如果处于宵禁时段（例：凌晨 0:00 - 8:00），不安排自动触发
    now = datetime.now()
    if now.hour < 8:
        return

    # 取消已有的 auto_trigger_job
    if chat_info["auto_trigger_job"] is not None:
        try:
            chat_info["auto_trigger_job"].schedule_removal()
        except Exception as e:
            print(f"Error removing old auto_trigger_job in reset_auto_trigger: {e}")
        chat_info["auto_trigger_job"] = None

    # 重新安排自动触发任务：正式环境可设为 60*60，这里为了测试可设为 60
    job = context.job_queue.run_once(
        auto_trigger_callback,
        when=60 * 60,  # 60 分钟后检查
        data={"chat_id": chat_id}
    )
    chat_info["auto_trigger_job"] = job


# ========== 消息处理 ==========
async def group_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    只要群组有消息，就重置自动触发的计时器。
    如果发现当前在跑的是“自动嚎叫”，则停掉它。
    """
    chat_id = update.effective_chat.id
    chat_info = get_chat_info(context, chat_id)
    get_known_chats(context).add(chat_id)

    # 如果正在运行的是自动 job，则停掉
    # 注意：我们用 chat_info["job_manual"] 来区分是手动还是自动
    if chat_info["job"] and (not chat_info["job_manual"]):
        try:
            chat_info["job"].schedule_removal()
        except Exception as e:
            print(f"Error while removing auto-trigger job on new message: {e}")

        chat_info["job"] = None
        chat_info["job_manual"] = False

        # 通知群里：自动嚎叫已终止
        try:
            await context.bot.send_message(chat_id=chat_id, text="检测到新消息，自动触发嚎叫已终止。")
        except Exception as e:
            print(f"Error sending 'auto ended' message: {e}")

    # 同时，如果有 pending 的 auto_trigger_job，也取消并重新计时
    if chat_info["auto_trigger_job"]:
        try:
            chat_info["auto_trigger_job"].schedule_removal()
        except Exception as e:
            print(f"Error removing pending auto_trigger_job on new message: {e}")
        chat_info["auto_trigger_job"] = None

    # 重新安排自动触发
    await reset_auto_trigger(update, context)


# ========== 宵禁处理 ==========
async def curfew_start_callback(context: ContextTypes.DEFAULT_TYPE):
    """
    每天 0:00 触发，标记所有已知群组进入宵禁，停止所有正在进行的嚎叫。
    """
    bot_data = context.bot_data
    if "KNOWN_CHATS" not in bot_data:
        return

    for chat_id in bot_data["KNOWN_CHATS"]:
        chat_info = get_chat_info(context, chat_id)

        # 标记宵禁
        chat_info["curfew"] = True

        # 关闭自动/手动 job
        if chat_info["job"]:
            try:
                chat_info["job"].schedule_removal()
            except Exception as e:
                print(f"Error removing job at curfew start: {e}")
            chat_info["job"] = None
            chat_info["job_manual"] = False

        # 关闭 pending 的 auto_trigger_job
        if chat_info["auto_trigger_job"]:
            try:
                chat_info["auto_trigger_job"].schedule_removal()
            except Exception as e:
                print(f"Error removing auto_trigger_job at curfew start: {e}")
            chat_info["auto_trigger_job"] = None

        # 发送宵禁开始提示
        try:
            await context.bot.send_message(chat_id=chat_id, text="【宵禁开始】现在是 0:00 - 8:00，自动触发已关闭，禁止嚎叫。")
        except Exception as e:
            print(f"Error sending curfew start message to {chat_id}: {e}")


async def curfew_end_callback(context: ContextTypes.DEFAULT_TYPE):
    """
    每天 8:00 触发，标记所有已知群组宵禁结束，并重新安排自动触发。
    """
    bot_data = context.bot_data
    if "KNOWN_CHATS" not in bot_data:
        return

    for chat_id in bot_data["KNOWN_CHATS"]:
        chat_info = get_chat_info(context, chat_id)

        # 结束宵禁
        chat_info["curfew"] = False

        # 发送宵禁结束提示
        try:
            await context.bot.send_message(chat_id=chat_id, text="【宵禁结束】现在是 8:00 以后，自动触发已恢复。")
        except Exception as e:
            print(f"Error sending curfew end message to {chat_id}: {e}")

        # 如果本群开启了 auto_enabled，则恢复自动触发（先设几秒后触发，避免刚到8:00瞬间又被别的什么逻辑抢占）
        if chat_info["auto_enabled"]:
            job = context.job_queue.run_once(auto_trigger_callback, when=60, data={"chat_id": chat_id})
            chat_info["auto_trigger_job"] = job


# ========== 主函数 ==========
def main():
    persistence = PicklePersistence(filepath="bot_data")
    application = (
        Application.builder()
        .token(TOKEN)
        .persistence(persistence)
        # 如果需要代理可取消注释
        # .proxy_url(PROXY_URL).get_updates_proxy(PROXY_URL)
        .build()
    )

    # 添加指令处理器
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("on", on_command))
    application.add_handler(CommandHandler("off", off_command))
    application.add_handler(CommandHandler("enable_auto", enable_auto_command))
    application.add_handler(CommandHandler("disable_auto", disable_auto_command))

    # 群聊消息处理
    application.add_handler(
        MessageHandler(filters.ALL & (filters.ChatType.GROUP | filters.ChatType.SUPERGROUP), group_message_handler)
    )

    # 安排每天的宵禁开始与结束任务（示例：0:00 - 8:00）
    job_queue = application.job_queue
    job_queue.run_daily(curfew_start_callback, time=time(hour=0, minute=0, second=0))
    job_queue.run_daily(curfew_end_callback, time=time(hour=8, minute=0, second=0))

    # 启动轮询
    application.run_polling()


if __name__ == "__main__":
    main()
