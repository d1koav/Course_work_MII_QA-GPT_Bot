import openai
import logging
import aioredis
import requests
import wikipediaapi
from telegram import Update
from pyaspeller import YandexSpeller, Word
from speechkit import ShortAudioRecognition, Session
from api import OAUTH_TOKEN, FOLDER_ID, TELEGRAM_API_TOKEN, REDIS_URL, CHAT_GPT
from telegram.ext import filters, MessageHandler, ApplicationBuilder, CommandHandler, ContextTypes, ConversationHandler

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

openai.api_key = CHAT_GPT
oauth_session = Session.from_yandex_passport_oauth_token(OAUTH_TOKEN, FOLDER_ID)
messages = []


def suggest_correction(text):
    speller = YandexSpeller()
    if len(text.split(' ')) > 1:
        return False, speller.spelled(text)
    else:
        w = Word(text)
        print(w.correct)
        flag = w.correct
        if flag:
            return flag, text
        else:
            return flag, speller.spelled(text)


async def get_wikipedia_summary(query, lang='ru'):
    redis = await get_redis()
    summary = await redis.get(f"wiki:{query}")
    if summary and summary is not None:
        return summary.decode()
    flag = False
    summary = ""
    wiki = wikipediaapi.Wikipedia(lang)
    page = wiki.page(query)
    if not page.exists():
        f, c = suggest_correction(query)
        if not f:
            page = wiki.page(c)
            if len(page.summary) == 0 or summary is None:
                return f"Статья '{query}' не найдена в Википедии."
            elif len(page.summary) < 1000:
                summary = f"Статья '{query}' не найдена в Википедии. Возможно, вы имели в виду: '{c}'" \
                          f".\nВот статья по запросу '{c}':" + '\n' + page.summary + '\n' + \
                          str(page.fullurl)
                flag = True
            else:
                flag = True
                summary = f"Статья '{query}' не найдена в Википедии. Возможно, вы имели в виду: '{c}'." \
                          f"\nВот статья по запросу '{c}':" + '\n' + page.summary[0:page.summary.find('.', 1000) + 1] \
                          + '\n' + str(page.fullurl)
        else:
            return f"Статья '{query}' не найдена в Википедии."
    if not flag:
        if len(page.summary) < 1000:
            summary = page.summary[0:1000] + '\n' + str(page.fullurl)
        else:
            summary = page.summary[0:page.summary.find('.', 1000) + 1] + '\n' + str(page.fullurl)
    await redis.setex(f"wiki:{query}", 86400, summary)
    await redis.close()
    return summary


def transcribe_audio_file_yandex(file):
    recognizer = ShortAudioRecognition(oauth_session)
    text = recognizer.recognize(file)
    return text


async def get_redis():
    return await aioredis.from_url(REDIS_URL)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_message(chat_id=update.effective_chat.id,
                                   text="Привет! Выберете бота с помощью команд.\n/wiki - Wikipedia QA\n/gpt ChatGpt")


async def exit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global messages
    await context.bot.send_message(chat_id=update.effective_chat.id,
                                   text="Привет! Выберете бота с помощью команд.\n/wiki - Wikipedia QA\n/gpt ChatGpt")
    messages = []
    return ConversationHandler.END


async def gpt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_message(chat_id=update.effective_chat.id,
                                   text="Привет! Я ChatGPT 3.5turbo задай свой вопрос. "
                                        "Чтобы завершить диалог, отправьте /exit")
    return 1


async def gpt_talk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global messages
    msg = update.message.text
    messages.append({"role": "user", "content": msg})
    completion = openai.ChatCompletion.create(
        model="gpt-3.5-turbo",
        messages=messages
    )
    chat_response = completion.choices[0].message.content
    messages.append({"role": "assistant", "content": chat_response})
    await context.bot.send_message(chat_id=update.effective_chat.id, text=chat_response)
    return 1


async def wiki(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_message(chat_id=update.effective_chat.id,
                                   text="Привет! Я Wikipedia QA бот. Введите свой вопрос или ключевое слово, "
                                        "и я предоставлю краткое содержание из Википедии. Вы также можете отправить "
                                        "голосовое сообщение с вашим запросом(не более 15 секунд).")
    return 1


async def wiki_talk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = await get_wikipedia_summary(update.message.text)
    await context.bot.send_message(chat_id=update.effective_chat.id, text=txt)
    return 1


async def voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message.voice
    file_id = msg.file_id
    duration = msg.duration
    if duration >= 15:
        await context.bot.send_message(chat_id=update.effective_chat.id,
                                       text="Ваше сообщение дольше 15 секунд. Отправьте сообщение короче 15 секунд.")
    else:
        file = await context.bot.get_file(file_id)
        file_content = requests.get(file.file_path).content
        txt = transcribe_audio_file_yandex(file_content)
        summary = await get_wikipedia_summary(txt)
        await context.bot.send_message(chat_id=update.effective_chat.id, text=summary)
    return 1


if __name__ == '__main__':
    application = ApplicationBuilder().token(TELEGRAM_API_TOKEN).build()
    start_handler = CommandHandler('start', start)
    gpt_handler = ConversationHandler(
        entry_points=[CommandHandler("gpt", gpt)],
        states={
            1: [MessageHandler(filters.TEXT & ~filters.COMMAND, gpt_talk)]
        },
        fallbacks=[CommandHandler('exit', exit)]
    )
    wiki_handler = ConversationHandler(
        entry_points=[CommandHandler("wiki", wiki)],
        states={
            1: [MessageHandler(filters.TEXT & ~filters.COMMAND, wiki_talk),
                MessageHandler(filters.VOICE & (~filters.COMMAND), voice)]
        },
        fallbacks=[CommandHandler('exit', exit)]
    )
    application.add_handler(gpt_handler)
    application.add_handler(start_handler)
    application.add_handler(wiki_handler)
    application.run_polling()
