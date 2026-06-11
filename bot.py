import asyncio
import logging
import os
import json
import io
import subprocess
import uuid
import re
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from vosk import Model, KaldiRecognizer
from gigachat import GigaChat
from pyrogram import Client

logging.basicConfig(level=logging.INFO)

# ================== НАСТРОЙКИ ==================
API_TOKEN = "8346093170:AAFcXK4Gu4pQBKNkw034qtPy4mbPmkX38KY"
GIGACHAT_API_KEY = "MDE5ZWFjZTgtMTBlOS03ZTY4LWI4MjQtN2Q5OGZkYmU0MzQ4OmQ2MzAyZDQ5LWRkMmQtNGM1YS1hNzk0LTY3ZDNlZWZkNWIxZA=="

API_ID = 2040
API_HASH = "b18441a1ff607e10a989891a5462e627"
PHONE_NUMBER = "+79811228354"  # ← замени

ADMIN_USER_ID = 739200102  # ← замени
YOOKASSA_PROVIDER_TOKEN = "MDE5ZWFjZTgtMTB"

DAILY_FREE_LIMIT = 1
PRICE_SINGLE = 199
PRICE_50_BOTS = 990
PRICE_UNLIMITED = 2990

# ================== ПУТИ ==================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MODEL_PATH = os.path.join(BASE_DIR, "vosk-model-small-ru-0.22")
DATA_FILE = os.path.join(BASE_DIR, "users.json")

if not os.path.exists(MODEL_PATH):
    raise FileNotFoundError(f"Модель Vosk не найдена: {MODEL_PATH}")

# ================== БАЗА ПОЛЬЗОВАТЕЛЕЙ ==================
def load_users():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_users(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

users_db = load_users()

def get_user_data(user_id: str) -> dict:
    user_id = str(user_id)
    if user_id not in users_db:
        users_db[user_id] = {
            "free_used_today": 0,
            "last_reset": datetime.now().strftime("%Y-%m-%d"),
            "paid_remaining": 0,
            "unlimited_until": None
        }
        save_users(users_db)
    return users_db[user_id]

def reset_daily_if_needed(user_data: dict):
    today = datetime.now().strftime("%Y-%m-%d")
    if user_data.get("last_reset") != today:
        user_data["free_used_today"] = 0
        user_data["last_reset"] = today
        save_users(users_db)

def can_create_bot(user_id: str) -> tuple[bool, str]:
    user_data = get_user_data(user_id)
    reset_daily_if_needed(user_data)
    if user_data.get("unlimited_until"):
        if datetime.strptime(user_data["unlimited_until"], "%Y-%m-%d") >= datetime.now():
            return True, ""
        else:
            user_data["unlimited_until"] = None
    if user_data["free_used_today"] < DAILY_FREE_LIMIT:
        return True, ""
    if user_data.get("paid_remaining", 0) > 0:
        return True, ""
    return False, "Лимит исчерпан. Пополните баланс через 💳 Тарифы"

def register_bot_creation(user_id: str):
    user_data = get_user_data(user_id)
    reset_daily_if_needed(user_data)
    if user_data.get("unlimited_until") and datetime.strptime(user_data["unlimited_until"], "%Y-%m-%d") >= datetime.now():
        return
    elif user_data.get("paid_remaining", 0) > 0:
        user_data["paid_remaining"] -= 1
    else:
        user_data["free_used_today"] += 1
    save_users(users_db)

# ================== ИНИЦИАЛИЗАЦИЯ ==================
model = Model(MODEL_PATH)
bot = Bot(token=API_TOKEN)
dp = Dispatcher()

app = Client(
    name="botforge_session",
    api_id=API_ID,
    api_hash=API_HASH,
    phone_number=PHONE_NUMBER,
    workdir=BASE_DIR
)

last_config = {}

# ================== КОНВЕРТАЦИЯ (Linux ffmpeg) ==================
def convert_ogg_to_wav(ogg_bytes: bytes) -> bytes:
    uid = uuid.uuid4().hex
    ogg_path = os.path.join("/tmp", f"temp_{uid}.ogg")
    wav_path = os.path.join("/tmp", f"temp_{uid}.wav")
    with open(ogg_path, "wb") as f:
        f.write(ogg_bytes)
    try:
        cmd = [
            "ffmpeg", "-i", ogg_path,
            "-acodec", "pcm_s16le", "-ac", "1", "-ar", "16000",
            wav_path
        ]
        subprocess.run(cmd, check=True, capture_output=True)
        with open(wav_path, "rb") as f:
            return f.read()
    finally:
        for p in [ogg_path, wav_path]:
            if os.path.exists(p):
                os.remove(p)

def recognize_wav(wav_data: bytes) -> str:
    rec = KaldiRecognizer(model, 16000)
    text = ""
    for i in range(0, len(wav_data), 4000):
        chunk = wav_data[i:i+4000]
        if not chunk:
            break
        if rec.AcceptWaveform(chunk):
            text += json.loads(rec.Result()).get("text", "") + " "
    text += json.loads(rec.FinalResult()).get("text", "")
    return text.strip()

def generate_bot_config(user_description: str) -> dict:
    system_prompt = (
        "Ты AI-конструктор Telegram-ботов. Из описания бизнеса пользователя создай JSON-конфигурацию бота:\n"
        "{\n"
        '  "name": "краткое название (латиница, без пробелов и спецсимволов)",\n'
        '  "display_name": "человеческое название",\n'
        '  "description": "что делает бот (1 предложение)",\n'
        '  "about": "подробное описание бизнеса",\n'
        '  "commands": [\n'
        '    {"command": "start", "description": "Начало работы"},\n'
        '    {"command": "catalog", "description": "Каталог товаров"},\n'
        '    {"command": "order", "description": "Сделать заказ"},\n'
        '    {"command": "contacts", "description": "Контакты"}\n'
        '  ],\n'
        '  "menu": {\n'
        '    "Каталог": "catalog",\n'
        '    "Заказать": "order",\n'
        '    "О нас": "about",\n'
        '    "Контакты": "contacts"\n'
        '  },\n'
        '  "products": [\n'
        '    {"name": "Название товара 1", "price": "цена в рублях"},\n'
        '    {"name": "Название товара 2", "price": "цена в рублях"}\n'
        '  ],\n'
        '  "payment_methods": "способы оплаты (перевод на карту, ЮKassa, наличные и т.д.)"\n'
        "}\n"
        "Отвечай ТОЛЬКО чистым JSON, без ```json и без пояснений."
    )
    prompt = f"{system_prompt}\n\nОписание бизнеса: {user_description}"
    with GigaChat(credentials=GIGACHAT_API_KEY, scope="GIGACHAT_API_PERS", verify_ssl_certs=False) as giga:
        response = giga.chat(prompt)
        raw = response.choices[0].message.content.strip()
    raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    return json.loads(raw)

async def create_bot_via_botfather(config: dict):
    botfather = await app.get_users("BotFather")
    await app.send_message(botfather.id, "/newbot")
    await asyncio.sleep(1)
    display_name = config.get("display_name", "Мой бот")
    await app.send_message(botfather.id, display_name)
    await asyncio.sleep(1)
    base_username = config.get("name", "mybot")
    if not base_username.endswith("bot"):
        base_username += "_bot"
    base_username = re.sub(r'[^a-zA-Z0-9_]', '', base_username)
    username = base_username + str(uuid.uuid4().hex[:4]) + "bot"
    await app.send_message(botfather.id, username)
    await asyncio.sleep(2)
    token = None
    async for msg in app.get_chat_history(botfather.id, limit=3):
        if msg.text and "HTTP API" in msg.text:
            for line in msg.text.split('\n'):
                line = line.strip()
                if re.match(r'^\d{9,12}:[\w-]{30,}$', line):
                    token = line
                    break
            if token:
                break
    if not token:
        raise Exception("Не удалось извлечь токен")
    return token

async def configure_new_bot(token: str, config: dict):
    async with Bot(token=token) as new_bot:
        desc = config.get("description", "Без описания")
        await new_bot.set_my_description(desc)
        about = config.get("about", desc)
        await new_bot.set_my_short_description(about[:120])
        commands = [
            types.BotCommand(command=cmd["command"], description=cmd["description"])
            for cmd in config.get("commands", [])
        ]
        await new_bot.set_my_commands(commands)

# ================== КЛАВИАТУРЫ ==================
def main_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="🎤 Создать бота", callback_data="create_bot")
    builder.button(text="💳 Тарифы", callback_data="buy_menu")
    builder.button(text="📋 Мои лимиты", callback_data="my_limits")
    builder.button(text="ℹ️ О сервисе", callback_data="about")
    builder.adjust(1)
    return builder.as_markup()

def buy_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text=f"🆕 Старт ({PRICE_SINGLE}₽) — 1 бот", callback_data="pay_single")
    builder.button(text=f"⚡ Профи ({PRICE_50_BOTS}₽) — 50 ботов", callback_data="pay_prof")
    builder.button(text=f"🚀 Бизнес ({PRICE_UNLIMITED}₽) — безлимит", callback_data="pay_unlim")
    builder.button(text="🔙 Назад", callback_data="main_menu")
    builder.adjust(1)
    return builder.as_markup()

def back_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="🔙 Назад в меню", callback_data="main_menu")
    return builder.as_markup()

# ================== ОБРАБОТЧИКИ ==================
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "🤖 **BotForge AI** — создай своего Telegram-бота голосом!\n\n"
        "Расскажите о своём бизнесе, и нейросеть:\n"
        "🔹 Сгенерирует структуру бота\n"
        "🔹 Зарегистрирует его через Telegram\n"
        "🔹 Настроит команды и описание\n\n"
        "Вы получите готовый токен и инструкцию.",
        reply_markup=main_keyboard(),
        parse_mode="Markdown"
    )

@dp.message(Command("buy"))
async def cmd_buy(message: types.Message):
    await show_buy_menu(message)

async def show_buy_menu(message: types.Message):
    if not YOOKASSA_PROVIDER_TOKEN or YOOKASSA_PROVIDER_TOKEN == "вставь_provider_token_от_BotFather":
        return await message.answer("Платёжная система пока не настроена.")
    await message.answer(
        "💳 **Выберите тариф:**\n\n"
        f"🆕 **Старт** — {PRICE_SINGLE}₽ (1 бот)\n"
        f"⚡ **Профи** — {PRICE_50_BOTS}₽ (50 ботов навсегда)\n"
        f"🚀 **Бизнес** — {PRICE_UNLIMITED}₽ (безлимит на 30 дней)\n\n"
        "Оплата картой через ЮKassa. Мгновенное зачисление.",
        reply_markup=buy_keyboard(),
        parse_mode="Markdown"
    )

@dp.message(Command("limits"))
async def cmd_limits(message: types.Message):
    await show_limits(message.from_user.id, message)

async def show_limits(user_id: int, message: types.Message):
    user_data = get_user_data(str(user_id))
    reset_daily_if_needed(user_data)
    free_left = max(0, DAILY_FREE_LIMIT - user_data["free_used_today"])
    unlimited = "✅" if user_data.get("unlimited_until") and datetime.strptime(user_data["unlimited_until"], "%Y-%m-%d") >= datetime.now() else "❌"
    until = user_data.get("unlimited_until", "—")
    await message.answer(
        f"📊 **Ваши лимиты:**\n\n"
        f"🆓 Бесплатных сегодня: {free_left} из {DAILY_FREE_LIMIT}\n"
        f"⚡ Оплаченных ботов: {user_data.get('paid_remaining', 0)}\n"
        f"🚀 Безлимит: {unlimited} (до {until})\n\n"
        f"Пополнить: /buy",
        parse_mode="Markdown"
    )

# Callback-обработчики (идентичны предыдущей версии)
@dp.callback_query(lambda c: c.data == "main_menu")
async def callback_main_menu(callback: types.CallbackQuery):
    await callback.message.edit_text(
        "🤖 **BotForge AI** — создай своего Telegram-бота голосом!",
        reply_markup=main_keyboard(),
        parse_mode="Markdown"
    )
    await callback.answer()

@dp.callback_query(lambda c: c.data == "buy_menu")
async def callback_buy_menu(callback: types.CallbackQuery):
    await show_buy_menu(callback.message)
    await callback.answer()

@dp.callback_query(lambda c: c.data == "about")
async def callback_about(callback: types.CallbackQuery):
    await callback.message.edit_text(
        "ℹ️ **О сервисе BotForge AI**\n\n"
        "Мы создаём Telegram-ботов по голосовому описанию с помощью нейросети GigaChat.\n\n"
        "🔹 Расшифровка голоса (офлайн)\n"
        "🔹 Генерация структуры бота (AI)\n"
        "🔹 Автоматическая регистрация через BotFather\n"
        "🔹 Настройка команд и описания\n\n"
        "Создайте бота для своего бизнеса за 2 минуты!",
        reply_markup=back_keyboard(),
        parse_mode="Markdown"
    )
    await callback.answer()

@dp.callback_query(lambda c: c.data == "my_limits")
async def callback_limits(callback: types.CallbackQuery):
    await show_limits(callback.from_user.id, callback.message)
    await callback.answer()

@dp.callback_query(lambda c: c.data == "create_bot")
async def callback_create(callback: types.CallbackQuery):
    await callback.message.edit_text(
        "🎤 Запишите голосовое сообщение с описанием вашего бизнеса.\n\n"
        "Например: «Я пекарня, хочу принимать заказы на торты с доставкой».",
        reply_markup=back_keyboard()
    )
    await callback.answer()

@dp.callback_query(lambda c: c.data == "pay_single")
async def callback_pay_single(callback: types.CallbackQuery):
    if not YOOKASSA_PROVIDER_TOKEN or YOOKASSA_PROVIDER_TOKEN == "вставь_provider_token_от_BotFather":
        await callback.answer("Платёжная система не настроена", show_alert=True)
        return
    await bot.send_invoice(
        chat_id=callback.message.chat.id,
        title="Тариф Старт",
        description="Разовое создание 1 бота",
        payload="single_1",
        provider_token=YOOKASSA_PROVIDER_TOKEN,
        currency="RUB",
        prices=[types.LabeledPrice(label="Старт", amount=PRICE_SINGLE * 100)],
        need_email=False,
        protect_content=True
    )
    await callback.answer()

@dp.callback_query(lambda c: c.data == "pay_prof")
async def callback_pay_prof(callback: types.CallbackQuery):
    if not YOOKASSA_PROVIDER_TOKEN or YOOKASSA_PROVIDER_TOKEN == "вставь_provider_token_от_BotFather":
        await callback.answer("Платёжная система не настроена", show_alert=True)
        return
    await bot.send_invoice(
        chat_id=callback.message.chat.id,
        title="Тариф Профи",
        description="50 созданий ботов (навсегда)",
        payload="prof_50",
        provider_token=YOOKASSA_PROVIDER_TOKEN,
        currency="RUB",
        prices=[types.LabeledPrice(label="Профи", amount=PRICE_50_BOTS * 100)],
        need_email=False,
        protect_content=True
    )
    await callback.answer()

@dp.callback_query(lambda c: c.data == "pay_unlim")
async def callback_pay_unlim(callback: types.CallbackQuery):
    if not YOOKASSA_PROVIDER_TOKEN or YOOKASSA_PROVIDER_TOKEN == "вставь_provider_token_от_BotFather":
        await callback.answer("Платёжная система не настроена", show_alert=True)
        return
    await bot.send_invoice(
        chat_id=callback.message.chat.id,
        title="Тариф Бизнес",
        description="Безлимит на 30 дней",
        payload="unlim_30",
        provider_token=YOOKASSA_PROVIDER_TOKEN,
        currency="RUB",
        prices=[types.LabeledPrice(label="Бизнес", amount=PRICE_UNLIMITED * 100)],
        need_email=False,
        protect_content=True
    )
    await callback.answer()

@dp.pre_checkout_query()
async def pre_checkout(query: types.PreCheckoutQuery):
    await query.answer(ok=True)

@dp.message(lambda msg: msg.successful_payment is not None)
async def successful_payment(message: types.Message):
    payload = message.successful_payment.invoice_payload
    user_id = str(message.from_user.id)
    user_data = get_user_data(user_id)
    if payload == "single_1":
        user_data["paid_remaining"] = user_data.get("paid_remaining", 0) + 1
        await message.answer("✅ Оплачен тариф «Старт»! Вам доступно 1 создание бота.", reply_markup=main_keyboard())
    elif payload == "prof_50":
        user_data["paid_remaining"] = user_data.get("paid_remaining", 0) + 50
        await message.answer("✅ Оплачен тариф «Профи»! Вам доступно 50 созданий ботов.", reply_markup=main_keyboard())
    elif payload == "unlim_30":
        end_date = datetime.now() + timedelta(days=30)
        user_data["unlimited_until"] = end_date.strftime("%Y-%m-%d")
        await message.answer(f"✅ Оплачен тариф «Бизнес»! Безлимит до {end_date.strftime('%d.%m.%Y')}.", reply_markup=main_keyboard())
    save_users(users_db)

@dp.message(Command("create"))
async def cmd_create(message: types.Message):
    user_id = message.from_user.id
    if user_id not in last_config:
        return await message.answer("Сначала сгенерируйте конфигурацию через голосовое сообщение.")
    ok, reason = can_create_bot(user_id)
    if not ok:
        return await message.answer(f"❌ {reason}\n\nПополните баланс: /buy")
    config = last_config[user_id]
    await message.answer("Регистрирую нового бота через BotFather...")
    try:
        token = await create_bot_via_botfather(config)
        await configure_new_bot(token, config)
        register_bot_creation(user_id)
        await message.answer(
            f"🎉 **Бот успешно создан!**\n\n"
            f"Токен: `{token}`\n\n"
            "**Как начать:**\n"
            "1. Перейдите в нового бота и нажмите /start.\n"
            "2. Настройте приём платежей через @BotFather.\n"
            "3. Отправляйте ссылку клиентам — бот уже работает.\n\n"
            "⚠️ Никому не передавайте токен.",
            parse_mode="Markdown"
        )
    except Exception as e:
        await message.answer(f"Ошибка создания бота: {e}")

@dp.message(lambda msg: msg.voice is not None)
async def handle_voice(message: types.Message):
    await message.answer("Расшифровываю речь (офлайн Vosk)...")
    file_id = message.voice.file_id
    file = await bot.get_file(file_id)
    ogg_data = io.BytesIO()
    await bot.download_file(file.file_path, ogg_data)
    ogg_bytes = ogg_data.getvalue()
    try:
        wav_bytes = await asyncio.to_thread(convert_ogg_to_wav, ogg_bytes)
        text = await asyncio.to_thread(recognize_wav, wav_bytes)
        if not text:
            return await message.answer("Не удалось распознать речь. Повторите громче и чётче.")
        await message.answer(f"Я услышал: «{text}»\n\nГенерирую конфигурацию через GigaChat...")
        if GIGACHAT_API_KEY != "твой_ключ_gigachat":
            config = await asyncio.to_thread(generate_bot_config, text)
            user_id = message.from_user.id
            last_config[user_id] = config
            pretty = json.dumps(config, ensure_ascii=False, indent=2)
            await message.answer(f"Конфигурация:\n\n{pretty}")
            await message.answer("Отправьте /create, чтобы зарегистрировать бота.", reply_markup=main_keyboard())
        else:
            await message.answer("AI-модуль не активен. Вставьте ключ GigaChat в код.")
    except Exception as e:
        await message.answer(f"Ошибка: {e}")

# Админ-команды
@dp.message(Command("admin"))
async def cmd_admin(message: types.Message):
    if message.from_user.id != ADMIN_USER_ID:
        return
    await message.answer("🔧 Админ-панель\n/add50, /addunlim, /stats")

@dp.message(lambda msg: msg.voice is None and not msg.successful_payment)
async def handle_text(message: types.Message):
    await message.answer(
        "Используйте кнопки меню или запишите голосовое сообщение для создания бота.",
        reply_markup=main_keyboard()
    )

async def main():
    await app.start()
    logging.info("Pyrogram factory активирован")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())