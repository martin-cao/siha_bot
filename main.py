from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters, PicklePersistence
import yaml

# 定义“啊啊啊”消息，重复 100 次“啊”
SCREAM_MESSAGE = "啊" * 100

GLOBAL_JOBS = {}
KNOWN_CHATS = set()

with open("config.yaml", "r", encoding="utf-8") as f:
    config = yaml.safe_load(f)

PROXY_URL = config.get("PROXY_URL", "")
TOKEN = config.get("TOKEN", "")


# 定义定时发送消息的回调函数
async def scream_callback(context):
    chat_id = context.job.data  # 改为 job.data
    await context.bot.send_message(chat_id=chat_id, text=SCREAM_MESSAGE)

# 处理 /help 指令
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print("[DEBUG] Help called")
    if update.effective_chat.type in ["group", "supergroup"]:
        KNOWN_CHATS.add(update.effective_chat.id)
    help_text = "这个机器人会定期喊‘啊啊啊啊...’。\n使用 /on x 开始每隔 x 分钟喊一次，x 可以是浮点数。\n使用 /off 停止喊叫。"
    await update.message.reply_text(help_text)
    await reset_auto_trigger(update, context)

# 处理 /off 指令
async def off_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if update.effective_chat.type in ["group", "supergroup"]:
        KNOWN_CHATS.add(chat_id)
    if chat_id in GLOBAL_JOBS and 'job' in GLOBAL_JOBS[chat_id]:
        try:
            GLOBAL_JOBS[chat_id]['job'].schedule_removal()
        except Exception as e:
            print(f"Error while removing job in off_command: {e}")
        if chat_id in GLOBAL_JOBS:
            GLOBAL_JOBS[chat_id].pop("job_manual", None)
            await update.message.reply_text("喊叫已停止。")
    else:
        await update.message.reply_text("当前没有在喊叫。")
        await reset_auto_trigger(update, context)


# 处理 /on x 指令
async def on_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print("[DEBUG] On called")
    # 检查参数是否正确
    if len(context.args) != 1:
        await update.message.reply_text("用法: /on x，其中 x 是分钟间隔（可以是浮点数）。")
        return

    # 解析 x 并验证
    try:
        x = float(context.args[0])
        if x <= 0:
            await update.message.reply_text("间隔必须大于 0。")
            return
    except ValueError:
        await update.message.reply_text("请提供一个正数作为 x。")
        return

    chat_id = update.effective_chat.id
    KNOWN_CHATS.add(chat_id)
    interval_seconds = x * 60
    job = context.job_queue.run_repeating(
        scream_callback,
        interval=interval_seconds,
        first=0,
        data=chat_id
    )
    GLOBAL_JOBS.setdefault(chat_id, {})['job'] = job
    GLOBAL_JOBS.setdefault(chat_id, {})['job_manual'] = True
    await update.message.reply_text(f"开始每隔 {x} 分钟喊叫，已立即启动。")
    await reset_auto_trigger(update, context)

# 自动触发回调函数：当群组超过60分钟没有消息时调用
async def auto_trigger_callback(context: ContextTypes.DEFAULT_TYPE):
    data = context.job.data
    chat_id = data['chat_id']
    if chat_id not in GLOBAL_JOBS or 'job' not in GLOBAL_JOBS[chat_id]:
        job = context.job_queue.run_repeating(
            scream_callback,
            interval=60 * 60,
            first=1,
            data=chat_id
        )
        GLOBAL_JOBS.setdefault(chat_id, {})['job'] = job
        GLOBAL_JOBS.setdefault(chat_id, {})['job_manual'] = False
        await context.bot.send_message(chat_id=chat_id, text="超过60分钟没有消息，自动开启嚎叫！")
    if chat_id in GLOBAL_JOBS and 'auto_trigger_job' in GLOBAL_JOBS[chat_id]:
        del GLOBAL_JOBS[chat_id]['auto_trigger_job']

# 监控群组消息，重置自动触发定时器
async def group_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type in ["group", "supergroup"]:
        chat_id = update.effective_chat.id
        KNOWN_CHATS.add(update.effective_chat.id)
        # 如果存在自动触发的重复任务（非手动），取消它
        if chat_id in GLOBAL_JOBS and 'job' in GLOBAL_JOBS[chat_id] and not context.chat_data.get("job_manual", False):
            try:
                GLOBAL_JOBS[chat_id]['job'].schedule_removal()
            except Exception as e:
                print(f"Error while removing auto-trigger job on new message: {e}")
            del GLOBAL_JOBS[chat_id]['job']
            await context.bot.send_message(chat_id=chat_id, text="检测到新消息，自动触发嚎叫已终止。")
        # 如果有待执行的自动触发任务，取消它
        if chat_id in GLOBAL_JOBS and 'auto_trigger_job' in GLOBAL_JOBS[chat_id]:
            try:
                GLOBAL_JOBS[chat_id]['auto_trigger_job'].schedule_removal()
            except Exception as e:
                print(f"Error while removing pending auto_trigger_job on new message: {e}")
            del GLOBAL_JOBS[chat_id]['auto_trigger_job']
        # 重新安排自动触发任务（例如：60 分钟后触发；测试时可用 10 秒）
        job = context.job_queue.run_once(auto_trigger_callback, when=60 * 60, data={'chat_id': chat_id})
        GLOBAL_JOBS.setdefault(chat_id, {})['auto_trigger_job'] = job

async def curfew_start_callback(context: ContextTypes.DEFAULT_TYPE):
    # 此回调在每天 0:00 触发
    for chat_id in KNOWN_CHATS:
        if chat_id in GLOBAL_JOBS:
            # 取消所有自动触发相关任务
            if 'job' in GLOBAL_JOBS[chat_id]:
                try:
                    GLOBAL_JOBS[chat_id]['job'].schedule_removal()
                except Exception as e:
                    print(f"Error while removing auto-trigger job at curfew start: {e}")
                GLOBAL_JOBS[chat_id].pop('job', None)
            if 'auto_trigger_job' in GLOBAL_JOBS[chat_id]:
                try:
                    GLOBAL_JOBS[chat_id]['auto_trigger_job'].schedule_removal()
                except Exception as e:
                    print(f"Error while removing pending auto_trigger_job at curfew start: {e}")
                GLOBAL_JOBS[chat_id].pop('auto_trigger_job', None)
            GLOBAL_JOBS.setdefault(chat_id, {})['curfew'] = True
        try:
            await context.bot.send_message(chat_id=chat_id, text="【宵禁开始】现在是 0:00 - 8:00，自动触发已关闭，禁止嚎叫。")
        except Exception as e:
            print(f"Error sending curfew start message to {chat_id}: {e}")

async def curfew_end_callback(context: ContextTypes.DEFAULT_TYPE):
    # 此回调在每天 8:00 触发
    for chat_id in KNOWN_CHATS:
        if chat_id in GLOBAL_JOBS:
            GLOBAL_JOBS[chat_id]['curfew'] = False
        try:
            await context.bot.send_message(chat_id=chat_id, text="【宵禁结束】现在是 8:00 以后，自动触发已恢复。")
        except Exception as e:
            print(f"Error sending curfew end message to {chat_id}: {e}")
        # 恢复自动触发（如果用户未禁用）
        job = context.job_queue.run_once(auto_trigger_callback, when=10, data={'chat_id': chat_id})
        GLOBAL_JOBS.setdefault(chat_id, {})['auto_trigger_job'] = job

async def reset_auto_trigger(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type not in ["group", "supergroup"]:
        return
    chat_id = update.effective_chat.id
    KNOWN_CHATS.add(chat_id)
    # 如果用户已手动禁用自动触发，则不重置
    if chat_id in GLOBAL_JOBS and GLOBAL_JOBS[chat_id].get("auto_enabled", True) is False:
        return
    # 如果处于宵禁时段（0:00 - 8:00），不安排自动触发
    from datetime import datetime
    now = datetime.now()
    if now.hour < 8:
        return
    # 取消 pending 的自动触发任务（如果存在）
    if chat_id in GLOBAL_JOBS and 'auto_trigger_job' in GLOBAL_JOBS[chat_id]:
        try:
            GLOBAL_JOBS[chat_id]['auto_trigger_job'].schedule_removal()
        except Exception as e:
            print(f"Error while removing pending auto_trigger_job on command: {e}")
        del GLOBAL_JOBS[chat_id]['auto_trigger_job']
    # 重新安排自动触发任务（测试时用 10 秒，正式环境可用 3600 秒）
    job = context.job_queue.run_once(auto_trigger_callback, when=10, data={'chat_id': chat_id})
    GLOBAL_JOBS.setdefault(chat_id, {})['auto_trigger_job'] = job

async def enable_auto_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    KNOWN_CHATS.add(chat_id)
    GLOBAL_JOBS.setdefault(chat_id, {})['auto_enabled'] = True
    await update.message.reply_text("自动触发已启用。")
    await reset_auto_trigger(update, context)

async def disable_auto_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    KNOWN_CHATS.add(chat_id)
    GLOBAL_JOBS.setdefault(chat_id, {})['auto_enabled'] = False
    # 取消 pending 的自动触发任务
    if chat_id in GLOBAL_JOBS and 'auto_trigger_job' in GLOBAL_JOBS[chat_id]:
        try:
            GLOBAL_JOBS[chat_id]['auto_trigger_job'].schedule_removal()
        except Exception as e:
            print(f"Error while removing auto_trigger_job in disable_auto_command: {e}")
        GLOBAL_JOBS[chat_id].pop('auto_trigger_job', None)
    await update.message.reply_text("自动触发已关闭。")

# Main func, start the bot
def main():
    from datetime import time
    persistence = PicklePersistence(filepath="bot_data")
    application = (Application.builder() \
                   .token(TOKEN) \
                   .persistence(persistence)
                   # .proxy(PROXY_URL).get_updates_proxy(PROXY_URL) \
                   .build())

    # 添加指令处理器
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("on", on_command))
    application.add_handler(CommandHandler("off", off_command))
    application.add_handler(CommandHandler("enable_auto", enable_auto_command))
    application.add_handler(CommandHandler("disable_auto", disable_auto_command))
    application.add_handler(MessageHandler(
        filters.ALL & (filters.ChatType.GROUP | filters.ChatType.SUPERGROUP),
        group_message_handler
    ))

    # 安排每天的宵禁开始与结束任务（0:00 和 8:00）
    job_queue = application.job_queue
    job_queue.run_daily(curfew_start_callback, time=time(hour=0, minute=0, second=0))
    job_queue.run_daily(curfew_end_callback, time=time(hour=8, minute=0, second=0))

    application.run_polling()

if __name__ == '__main__':
    main()