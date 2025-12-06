import telebot
from telebot import types
import time
import threading
import psutil
import sys
import sqlite3
import telebot.apihelper
import os
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
try:
    DEVELOPER_ID = int(os.getenv("DEVELOPER_ID"))
    PRIVATE_CHANNEL_ID = int(os.getenv("PRIVATE_CHANNEL_ID"))
    LINK_EXPIRY_SECONDS = int(os.getenv("LINK_EXPIRY_SECONDS", 60))
except (TypeError, ValueError):
    print("[ERROR] Invalid .env configuration")
    sys.exit(1)
    
PUBLIC_CHANNEL_ID = os.getenv("PUBLIC_CHANNEL_ID")

MAINTENANCE_MODE = False  
LINK_COOLDOWN_SECONDS = 5 * 60  
LINK_REQUEST_LIMIT = 5  
LINK_REQUEST_WINDOW = 30 * 60  
USER_LINK_REQUESTS = {}  

if not TOKEN or not PUBLIC_CHANNEL_ID:
    print("[ERROR] Missing required .env variables")
    sys.exit(1)

bot = telebot.TeleBot(TOKEN)

def safe_edit_message_text(chat_id, message_id, text, reply_markup=None, parse_mode="HTML"):
    try:
        bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            reply_markup=reply_markup,
            parse_mode=parse_mode
        )
    except telebot.apihelper.ApiTelegramException as e:
        if "message is not modified" in str(e):
            pass
        else:
            print(f"[WARNING] Edit message error: {e}")
    except Exception as e:
        print(f"[WARNING] Edit message exception: {e}")

LAST_LINK_TIME = {}
MONITORING_USERS = {}
USER_LANGUAGE_CACHE = {}
USER_COMMAND_COUNT = {}
USER_BAN_EXPIRY = {}
USER_LAST_CALLBACK_TIME = {}

DATABASE_NAME = 'bot_data.db'

def init_db():
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            language_code TEXT NOT NULL DEFAULT 'ar'
        )
    """)
    conn.commit()
    conn.close()
    print("[INFO] Database initialized")

def get_user_language(user_id):
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT language_code FROM users WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    conn.close()
    return result[0] if result else None

def set_user_language(user_id, lang_code):
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR REPLACE INTO users (user_id, language_code) VALUES (?, ?)",
        (user_id, lang_code)
    )
    conn.commit()
    conn.close()

def get_all_user_ids():
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM users")
    user_ids = [row[0] for row in cursor.fetchall()]
    conn.close()
    return user_ids

def get_text(chat_id, key):
    if chat_id == DEVELOPER_ID:
        lang = 'ar' 
    else:
        lang = USER_LANGUAGE_CACHE.get(chat_id)

    if lang is None:
        lang = get_user_language(chat_id)
        if lang is None:
            lang = 'ar'
        set_user_language(chat_id, lang)
        USER_LANGUAGE_CACHE[chat_id] = lang

    return TEXTS.get(lang, TEXTS['ar']).get(key, TEXTS['ar'][key])

def safe_send_message(chat_id, text, **kwargs):
    try:
        return bot.send_message(chat_id, text, **kwargs)
    except Exception as e:
        print(f"[WARNING] Send message failed: {e}")
        return None

def check_command_spam(chat_id, limit=5, window=60, ban_duration=600):
    if chat_id == DEVELOPER_ID:
        return True

    current_time = time.time()

    ban_expiry = USER_BAN_EXPIRY.get(chat_id, 0)
    if current_time < ban_expiry:
        return False

    data = USER_COMMAND_COUNT.get(chat_id, {'count': 0, 'first_time': current_time})

    if (current_time - data['first_time']) > window:
        data = {'count': 1, 'first_time': current_time}
    else:
        data['count'] += 1

    USER_COMMAND_COUNT[chat_id] = data

    if data['count'] > limit:
        USER_BAN_EXPIRY[chat_id] = current_time + ban_duration
        ban_message = get_text(chat_id, 'spam_banned_msg').format(int(ban_duration / 60))
        safe_send_message(chat_id, ban_message, parse_mode="HTML", protect_content=True)
        return False

    return True

def check_callback_spam(chat_id, delay=2):
    if chat_id == DEVELOPER_ID:
        return True

    current_time = time.time()
    last_time = USER_LAST_CALLBACK_TIME.get(chat_id, 0)

    if (current_time - last_time) < delay:
        warning_message = get_text(chat_id, 'callback_wait_msg').format(delay)
        safe_send_message(chat_id, warning_message, parse_mode='HTML', protect_content=True)
        return False

    USER_LAST_CALLBACK_TIME[chat_id] = current_time
    return True

def check_link_request_spam(chat_id):
    current_time = time.time()

    USER_LINK_REQUESTS[chat_id] = [
        t for t in USER_LINK_REQUESTS.get(chat_id, [])
        if current_time - t < LINK_REQUEST_WINDOW
    ]

    USER_LINK_REQUESTS.setdefault(chat_id, []).append(current_time)

    if len(USER_LINK_REQUESTS[chat_id]) > LINK_REQUEST_LIMIT:
        ban_msg = get_text(chat_id, 'link_request_banned_msg')
        safe_send_message(chat_id, ban_msg, parse_mode="HTML", protect_content=True)
        USER_LINK_REQUESTS.pop(chat_id, None)
        return False

    return True

TEXTS = {
    'ar': {
        'spam_banned_msg': "🚫 <b>تم حظرك!</b>\nلقد أرسلت أوامر كثيرة في وقت قصير. تم منعك من استخدام البوت لمدة {} دقائق.",
        'callback_wait_msg': "⏳ <b>تريث قليلاً!</b>\nالرجاء الانتظار {} ثوانٍ بين كل ضغطة زر وأخرى لتجنب حظر حسابك.",
        'link_cooldown_msg': "⏳ <b>مهلاً!</b>\nيجب أن تنتظر على الأقل <b>{} دقائق</b> قبل طلب رابط جديد.",
        'link_request_banned_msg': "🚫 <b>تم حظرك من طلب الروابط!</b>\nلقد حاولت طلب رابط جديد عدة مرات في فترة قصيرة. لن يتم الرد عليك مجدداً.",
        'maintenance_mode_msg': "⚙️ <b>البوت في وضع الصيانة!</b>\nعذراً، نحن نقوم ببعض التحديثات والإصلاحات الضرورية. يرجى المحاولة لاحقاً.",
        'welcome_choose_lang': "Hello! 👋\nPlease choose your preferred language:\n\n💡 <b>Note:</b> To change the language later, send the command: <code>/lang</code>",
        'welcome_developer': "أهلاً أيها المطور! هذه هي <b>لوحة التحكم</b> الخاصة بك:", 
        'lang_saved': "✅ تم اختيار اللغة العربية بنجاح!",
        'already_member': "✅ <b>أنت بالفعل عضو في القناة الخاصة!</b>\nلا يمكننا إصدار رابط جديد لك إلا إذا غادرت القناة أولاً.",
        'link_active': "⚠️ <b>تنبيه: الرابط السابق لا يزال ساري المفعول!</b>\nيرجى استخدام الرابط الذي أرسلناه لك قبل قليل. يمكنك طلب رابط جديد بعد انتهاء صلاحية الرابط الحالي (بعد حوالي دقيقة واحدة).",
        'public_sub_ok': "✅ <b>أهلاً بك!</b> لقد تأكدنا من اشتراكك في القناة العامة.",
        'get_link_button': "الحصول على الرابط 🔗",
        'change_lang_button': "🌐 تغيير اللغة",
        'public_sub_fail': "🚫 <b>للأسف، أنت لست مشتركاً!</b>\nالرجاء الاشتراك في القناة العامة أولاً:\n{}\nثم عد واضغط /start مجدداً.",
        'technical_error': "⚠️ <b>عذراً، حدث خطأ فني أو مشكلة في الصلاحيات.</b>\nالرجاء التأكد من أن البوت هو **مشرف** في كلتا القناتين (العامة والخاصة) ويمتلك صلاحية الدعوة والطرد، ثم المحاولة لاحقاً.",
        'link_generated_msg': "✅ <b>تم إنشاء الرابط!</b>\n\nهذا هو <b>رابط دخولك المؤقت</b> إلى القناة الخاصة:\n🔗 {}\n\n<b>ملاحظة:</b> الرابط سينتهي مفعوله خلال <b>دقيقة واحدة</b>. نرجو الانضمام فوراً.",
        'link_failed': "⚠️ <b>عذراً، لم نتمكن من إنشاء الرابط!</b>\nيرجى التواصل مع مسؤول البوت.",
        'link_expired': "⌛ <b>عذراً، لقد انتهى وقتك المتاح!</b>\nالرابط الذي أرسلناه لك انتهت صلاحيته الآن.",
        'join_success': "🎉 <b>تم الانضمام بنجاح!</b>\nأهلاً بك في القناة الخاصة. تم إيقاف الرابط المؤقت.",
        'sub_removed': "🚫 <b>تم طردك!</b> لقد أزلت اشتراكك من القناة العامة.\nالرجاء الاشتراك مجدداً في {} للحصول على الرابط والدخول للقناة الخاصة.",
        'resources_title': "📊 <b>استخدام موارد الخادم</b> 📊",
        'cpu_usage': "--- 🖥️ المعالج (CPU) ---\n<b>الاستخدام:</b> <code>{}%</code>",
        'ram_usage': "--- 🧠 الذاكرة (RAM) ---\n<b>الإجمالي:</b> <code>{:.2f} GB</code>\n<b>المتاح:</b> <code>{:.2f} GB</code>\n<b>المستخدم:</b> <code>{}%</code>",
        'refresh_button': "تحديث 🔄",
        'dev_controls_title': "لوحة التحكم الخاصة بك:",
        'maintenance_on_button': "تفعيل الصيانة ✅", 
        'maintenance_off_button': "تعطيل الصيانة ❌", 
        'maintenance_on_msg': "✅ <b>تم تفعيل وضع الصيانة!</b>",
        'maintenance_off_msg': "❌ <b>تم تعطيل وضع الصيانة!</b>",
        'resources_button': "استهلاك الموارد 📊", 
        'back_button': "عودة ⬅️", 
    },
    'en': {
        'spam_banned_msg': "🚫 <b>You are banned!</b>\nYou sent too many commands too quickly. You are banned from using the bot for {} minutes.",
        'callback_wait_msg': "⏳ <b>Slow down!</b>\nPlease wait {} seconds between button clicks to avoid being banned.",
        'link_cooldown_msg': "⏳ <b>Hold on!</b>\nYou must wait at least <b>{} minutes</b> before requesting a new link.",
        'link_request_banned_msg': "🚫 <b>You are banned from requesting links!</b>\nYou tried to request a new link too many times in a short period. You will not receive any further replies.",
        'maintenance_mode_msg': "⚙️ <b>Bot is under maintenance!</b>\nSorry, we are applying necessary updates and fixes. Please try again later.",
        'welcome_choose_lang': "Hello! 👋\nPlease choose your preferred language:\n\n💡 <b>Note:</b> To change the language later, send the command: <code>/lang</code>",
        'welcome_developer': "Hello Developer! This is your <b>Control Panel</b>:", 
        'lang_saved': "✅ Language successfully set to English!",
        'already_member': "✅ <b>You are already a member of the Private Channel!</b>\nYou cannot get a new link unless you leave the channel first.",
        'link_active': "⚠️ <b>Notice: Previous link is still active!</b>\nPlease use the link we sent earlier. You can request a new link after the current one expires (in about a minute).",
        'public_sub_ok': "✅ <b>Welcome!</b> We confirmed your public channel subscription.",
        'get_link_button': "Get Link 🔗",
        'change_lang_button': "🌐 Change Language",
        'public_sub_fail': "🚫 <b>Subscription failed!</b>\nPlease subscribe to the public channel first:\n{}\nThen press /start again.",
        'technical_error': "⚠️ <b>Sorry, a technical error occurred or a permissions issue.</b>\nPlease ensure the bot is an **admin** in both the public and private channels and has invite and kick privileges, then try again later.",
        'link_generated_msg': "✅ <b>Link generated!</b>\n\nThis is your <b>temporary invitation link</b> to the private channel:\n🔗 {}\n\n<b>Note:</b> The link will expire in <b>one minute</b>. Please join immediately.",
        'link_failed': "⚠️ <b>Sorry, we couldn't create the link!</b>\nPlease contact the bot administrator.",
        'link_expired': "⌛ <b>Sorry, your time has run out!</b>\nThe link we sent you has now expired.",
        'join_success': "🎉 <b>Joined successfully!</b>\nWelcome to the private channel. The temporary link has been disabled.",
        'sub_removed': "🚫 <b>You have been kicked!</b> You removed your subscription from the public channel.\nPlease subscribe again to {} to get the link and re-enter the private channel.",
        'resources_title': "📊 <b>Server Resources Usage</b> 📊",
        'cpu_usage': "--- 🖥️ Processor (CPU) ---\n<b>Usage:</b> <code>{}%</code>",
        'ram_usage': "--- 🧠 Memory (RAM) ---\n<b>Total:</b> <code>{:.2f} GB</code>\n<b>Available:</b> <code>{:.2f} GB</code>\n<b>Used:</b> <code>{}%</code>",
        'refresh_button': "Refresh 🔄",
        'dev_controls_title': "Your <b>Control Panel</b>:", 
        'maintenance_on_button': "Maintenance ON ✅",
        'maintenance_off_button': "Maintenance OFF ❌",
        'resources_button': "Resources 📊",
        'back_button': "Back ⬅️",
        'maintenance_on_msg': "✅ <b>Maintenance mode is ON!</b>",
        'maintenance_off_msg': "❌ <b>Maintenance mode is OFF!</b>",
    },
    'ru': {
        'spam_banned_msg': "🚫 <b>Вы заблокированы!</b>\nВы отправили слишком много команд слишком быстро. Вы заблокированы на {} минут.",
        'callback_wait_msg': "⏳ <b>Подождите!</b>\nПожалуйста, подождите {} секунды между нажатиями кнопок, чтобы избежать блокировки.",
        'link_cooldown_msg': "⏳ <b>Подождите!</b>\nВы должны подождать не менее <b>{} минут</b> перед запросом новой ссылки.",
        'link_request_banned_msg': "🚫 <b>Вам запрещено запрашивать ссылки!</b>\nВы попытались запросить новую ссылку слишком много раз за короткий период. Вы не получите дальнейших ответов.",
        'maintenance_mode_msg': "⚙️ <b>Бот находится на обслуживании!</b>\nИзвините, мы проводим необходимые обновления и исправления. Пожалуйста, попробуйте позже.",
        'welcome_choose_lang': "Hello! 👋\nPlease choose your preferred language:\n\n💡 <b>Note:</b> To change the language later, send the command: <code>/lang</code>",
        'welcome_developer': "Здравствуйте, разработчик! Это ваша панель управления:",
        'lang_saved': "✅ Язык успешно изменен на русский!",
        'already_member': "✅ <b>Вы уже являетесь участником приватного канала!</b>\nВы не можете получить новую ссылку, пока не покинете канал.",
        'link_active': "⚠️ <b>Внимание: Предыдущая ссылка все еще активна!</b>\nПожалуйста, используйте ссылку, которую мы отправили ранее. Вы сможете запросить новую ссылку после истечения текущей (примерно через минуту).",
        'public_sub_ok': "✅ <b>Добро пожаловать!</b> Мы подтвердили вашу подписку на публичный канал.",
        'get_link_button': "Получить ссылку 🔗",
        'change_lang_button': "🌐 Изменить язык",
        'public_sub_fail': "🚫 <b>Подписка не удалась!</b>\nПожалуйста, сначала подпишитесь на публичный канал:\n{}\nЗатем нажмите /start снова.",
        'technical_error': "⚠️ <b>Извините, произошла техническая ошибка или проблема с правами доступа.</b>\nУбедитесь, что бот является **администратором** в обоих каналах (публичном и приватном) и имеет права на приглашение и исключение, затем повторите попытку.",
        'link_generated_msg': "✅ <b>Ссылка создана!</b>\n\nЭто ваша <b>временная ссылка-приглашение</b> в приватный канал:\n🔗 {}\n\n<b>Примечание:</b> Срок действия ссылки истечет через <b>одну минуту</b>. Пожалуйста, присоединяйтесь немедленно.",
        'link_failed': "⚠️ <b>Извините, не удалось создать ссылку!</b>\nПожалуйста, свяжитесь с администратором бота.",
        'link_expired': "⌛ <b>Извините, ваше время истекло!</b>\nСрок действия отправленной вам ссылки истек.",
        'join_success': "🎉 <b>Успешное присоединение!</b>\nДобро пожаловать в приватный канал. Временная ссылка отключена.",
        'sub_removed': "🚫 <b>Вы были исключены!</b> Вы отменили подписку на публичный канал.\nПожалуйста, подпишитесь снова на {} чтобы получить ссылку ивернуться в приватный канал.",
        'resources_title': "📊 <b>Использование ресурсов сервера</b> 📊",
        'cpu_usage': "--- 🖥️ Процессор (CPU) ---\n<b>Использование:</b> <code>{}%</code>",
        'ram_usage': "--- 🧠 Память (RAM) ---\n<b>Всего:</b> <code>{:.2f} GB</code>\n<b>Доступно:</b> <code>{:.2f} GB</code>\n<b>Использовано:</b> <code>{}%</code>",
        'refresh_button': "Обновить 🔄",
        'dev_controls_title': "Ваша панель управления:",
        'maintenance_on_button': "Режим обслуживания ВКЛ ✅",
        'maintenance_off_button': "Режим обслуживания ВЫКЛ ❌",
        'resources_button': "Ресурсы 📊",
        'back_button': "Назад ⬅️",
        'maintenance_on_msg': "✅ <b>Режим обслуживания ВКЛ!</b>",
        'maintenance_off_msg': "❌ <b>Режим обслуживания ВЫКЛ!</b>",
    }
}

def main_keyboard(chat_id):
    markup = types.InlineKeyboardMarkup()
    btn_get_link = types.InlineKeyboardButton(get_text(chat_id, 'get_link_button'), callback_data='generate_new_link')
    markup.add(btn_get_link)
    return markup

def empty_keyboard():
    return types.InlineKeyboardMarkup()

def language_selection_keyboard():
    markup = types.InlineKeyboardMarkup(row_width=1)
    btn_ar = types.InlineKeyboardButton("العربية 🇸🇦", callback_data='set_lang_ar')
    btn_en = types.InlineKeyboardButton("English 🇺🇸", callback_data='set_lang_en')
    btn_ru = types.InlineKeyboardButton("Русский 🇷🇺", callback_data='set_lang_ru')
    markup.add(btn_ar, btn_en, btn_ru)
    return markup

def get_resource_info(chat_id):
    psutil.cpu_percent(percpu=False)
    time.sleep(0.1)
    cpu_percent = psutil.cpu_percent(percpu=False)

    memory_usage = psutil.virtual_memory()

    resources_info = (
        get_text(chat_id, 'resources_title') + "\n"
        + get_text(chat_id, 'cpu_usage').format(f"{cpu_percent:.2f}") + "\n"
        + get_text(chat_id, 'ram_usage').format(
            memory_usage.total / (1024**3),
            memory_usage.available / (1024**3),
            memory_usage.percent
        )
    )
    return resources_info

def developer_keyboard(chat_id):
    markup = types.InlineKeyboardMarkup()
    
    if MAINTENANCE_MODE:
        btn_maintenance = types.InlineKeyboardButton(get_text(chat_id, 'maintenance_off_button'), callback_data='maintenance_off') 
    else:
        btn_maintenance = types.InlineKeyboardButton(get_text(chat_id, 'maintenance_on_button'), callback_data='maintenance_on') 

    btn_resources = types.InlineKeyboardButton(get_text(chat_id, 'resources_button'), callback_data='show_resources') 
    
    markup.add(btn_resources) 
    markup.add(btn_maintenance) 
    return markup

def resources_back_keyboard(chat_id): 
    markup = types.InlineKeyboardMarkup()
    btn_refresh = types.InlineKeyboardButton(get_text(chat_id, 'refresh_button'), callback_data='show_resources')
    btn_back = types.InlineKeyboardButton(get_text(chat_id, 'back_button'), callback_data='dev_home')
    markup.add(btn_refresh, btn_back)
    return markup

def edit_link_message_expired(chat_id, message_id):
    try:
        safe_edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=get_text(chat_id, 'link_expired'),
            reply_markup=main_keyboard(chat_id),
            parse_mode="HTML"
        )
    except Exception:
        pass

def monitor_user_join(chat_id):
    user_data = MONITORING_USERS.get(chat_id)
    if not user_data:
        return

    message_id = user_data['message_id']
    start_time = user_data['start_time']
    current_time = time.time()

    if (current_time - start_time) > (LINK_EXPIRY_SECONDS + 5):
        edit_link_message_expired(chat_id, message_id)
        if user_data.get('timer'): 
            user_data['timer'].cancel()
        MONITORING_USERS.pop(chat_id, None)
        return

    try:
        member = bot.get_chat_member(PRIVATE_CHANNEL_ID, chat_id)
        if member.status in ['creator', 'administrator', 'member']:
            safe_edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=get_text(chat_id, 'join_success'),
                parse_mode="HTML"
            )

            if user_data.get('timer'): 
                user_data['timer'].cancel()
            MONITORING_USERS.pop(chat_id, None)
            return

    except Exception as e:
        print(f"[WARNING] Monitor check error: {e}")

    new_timer = threading.Timer(5, monitor_user_join, args=[chat_id])
    user_data['timer'] = new_timer
    new_timer.start()

def generate_invite_link_and_send(chat_id, message_id, user_info):
    try:
        LAST_LINK_TIME[chat_id] = time.time()

        user_label = f"@{user_info.username}" if user_info.username else f"ID: {chat_id}"

        invite_link = bot.create_chat_invite_link(PRIVATE_CHANNEL_ID, member_limit=1, expire_date=int(time.time()) + LINK_EXPIRY_SECONDS, name=user_label)
        link = invite_link.invite_link

        safe_edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=get_text(chat_id, 'link_generated_msg').format(link),
            parse_mode="HTML"
        )

        MONITORING_USERS[chat_id] = {'message_id': message_id, 'start_time': time.time(), 'timer': None}
        timer = threading.Timer(5, monitor_user_join, args=[chat_id])
        MONITORING_USERS[chat_id]['timer'] = timer
        timer.start()

    except Exception as e:
        print(f"[ERROR] Link generation failed: {e}")
        safe_edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=get_text(chat_id, 'link_failed') + " " + get_text(chat_id, 'technical_error'),
            parse_mode="HTML"
        )

@bot.message_handler(func=lambda message: MAINTENANCE_MODE and message.chat.id != DEVELOPER_ID, content_types=['text', 'photo', 'video', 'document', 'audio', 'voice', 'sticker', 'contact', 'location', 'venue', 'game', 'video_note', 'dice', 'poll', 'mask', 'invoice', 'successful_payment', 'new_chat_members', 'left_chat_member', 'new_chat_title', 'new_chat_photo', 'delete_chat_photo', 'group_chat_created', 'supergroup_chat_created', 'channel_chat_created', 'migrate_to_chat_id', 'migrate_from_chat_id', 'pinned_message', 'web_app_data'])
def handle_all_messages(message):
    chat_id = message.chat.id
    
    ban_expiry = USER_BAN_EXPIRY.get(chat_id, 0)
    if time.time() < ban_expiry:
        check_command_spam(chat_id)
        return

    try:
        bot.send_message(chat_id, get_text(chat_id, 'maintenance_mode_msg'), parse_mode="HTML", protect_content=True)
    except Exception:
        pass 
    
    check_command_spam(chat_id)

@bot.message_handler(commands=['start'])
def send_welcome(message):
    chat_id = message.chat.id

    if chat_id in USER_LINK_REQUESTS and chat_id != DEVELOPER_ID:
        if len(USER_LINK_REQUESTS.get(chat_id, [])) > LINK_REQUEST_LIMIT:
            return

    if not check_command_spam(chat_id):
        return

    current_time = time.time()

    if chat_id == DEVELOPER_ID:
        set_user_language(chat_id, 'ar') 
        USER_LANGUAGE_CACHE[chat_id] = 'ar'
        bot.send_message(chat_id, get_text(chat_id, 'welcome_developer'), reply_markup=developer_keyboard(chat_id), parse_mode="HTML", protect_content=True)
        return

    user_lang = get_user_language(chat_id)

    if user_lang is None:
        bot.send_message(chat_id, TEXTS['ar']['welcome_choose_lang'], reply_markup=language_selection_keyboard(), parse_mode="HTML", protect_content=True)
        return

    USER_LANGUAGE_CACHE[chat_id] = user_lang

    try:
        private_member = bot.get_chat_member(PRIVATE_CHANNEL_ID, chat_id)
        if private_member.status in ['creator', 'administrator', 'member']:
            bot.send_message(chat_id, get_text(chat_id, 'already_member'), parse_mode="HTML", protect_content=True)
            return

        last_link_time = LAST_LINK_TIME.get(chat_id)
        if last_link_time and (current_time - last_link_time < LINK_COOLDOWN_SECONDS):
            remaining_time = int(LINK_COOLDOWN_SECONDS - (current_time - last_link_time))
            minutes = int(remaining_time / 60) + 1

            cooldown_msg = get_text(chat_id, 'link_cooldown_msg').format(minutes)

            bot.send_message(chat_id, cooldown_msg, parse_mode="HTML", protect_content=True, reply_markup=empty_keyboard())
            check_link_request_spam(chat_id)
            return

        public_member = bot.get_chat_member(PUBLIC_CHANNEL_ID, chat_id)

        if public_member.status in ['creator', 'administrator', 'member']:
            bot.send_message(chat_id, get_text(chat_id, 'public_sub_ok'), reply_markup=main_keyboard(chat_id), parse_mode="HTML", protect_content=True)
        else:
            bot.send_message(chat_id, get_text(chat_id, 'public_sub_fail').format(PUBLIC_CHANNEL_ID), parse_mode="HTML", protect_content=True)

    except Exception as e:
        print(f"[ERROR] Welcome error: {e}")
        bot.send_message(chat_id, get_text(chat_id, 'technical_error'), parse_mode="HTML", protect_content=True)

@bot.message_handler(commands=['lang'])
def handle_lang_command(message):
    chat_id = message.chat.id

    if chat_id in USER_LINK_REQUESTS and chat_id != DEVELOPER_ID:
        if len(USER_LINK_REQUESTS.get(chat_id, [])) > LINK_REQUEST_LIMIT:
            return

    if not check_command_spam(chat_id):
        return

    if get_user_language(chat_id) is None:
        set_user_language(chat_id, 'ar')
        USER_LANGUAGE_CACHE[chat_id] = 'ar'

    bot.send_message(chat_id, get_text(chat_id, 'welcome_choose_lang'), reply_markup=language_selection_keyboard(), parse_mode="HTML", protect_content=True)

@bot.message_handler(commands=['maintenance_on'])
def maintenance_on_command(message):
    global MAINTENANCE_MODE
    if message.chat.id == DEVELOPER_ID:
        MAINTENANCE_MODE = True
        bot.send_message(message.chat.id, get_text(message.chat.id, 'maintenance_on_msg'), parse_mode="HTML", reply_markup=developer_keyboard(message.chat.id))
        
@bot.message_handler(commands=['maintenance_off'])
def maintenance_off_command(message):
    global MAINTENANCE_MODE
    if message.chat.id == DEVELOPER_ID:
        MAINTENANCE_MODE = False
        bot.send_message(message.chat.id, get_text(message.chat.id, 'maintenance_off_msg'), parse_mode="HTML", reply_markup=developer_keyboard(message.chat.id))

@bot.callback_query_handler(func=lambda call: True)
def callback_inline(call):
    chat_id = call.message.chat.id
    message_id = call.message.message_id
    data = call.data
    
    global MAINTENANCE_MODE
    
    try:
        bot.answer_callback_query(call.id)
    except Exception:
        pass

    if not check_callback_spam(chat_id):
        return

    if data.startswith('set_lang_'):
        lang_code = data.split('_')[2]
        set_user_language(chat_id, lang_code)
        USER_LANGUAGE_CACHE[chat_id] = lang_code
        
        if chat_id == DEVELOPER_ID: 
            safe_edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=get_text(chat_id, 'lang_saved') + "\n" + get_text(chat_id, 'welcome_developer'),
                reply_markup=developer_keyboard(chat_id),
                parse_mode="HTML"
            )
        else: 
            safe_edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=get_text(chat_id, 'lang_saved'),
                parse_mode="HTML"
            )
            send_welcome(call.message)
        return

    if data == 'generate_new_link':
        if chat_id == DEVELOPER_ID:
            return

        if not check_link_request_spam(chat_id):
            return

        try:
            member = bot.get_chat_member(PRIVATE_CHANNEL_ID, chat_id)
            if member.status in ['creator', 'administrator', 'member']:
                bot.send_message(chat_id, get_text(chat_id, 'already_member'), parse_mode="HTML", protect_content=True)
                return
        except Exception:
            pass 

        if chat_id in MONITORING_USERS:
            safe_edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=get_text(chat_id, 'link_active'),
                reply_markup=main_keyboard(chat_id),
                parse_mode="HTML"
            )
            return

        current_time = time.time()
        last_link_time = LAST_LINK_TIME.get(chat_id)
        if last_link_time and (current_time - last_link_time < LINK_COOLDOWN_SECONDS):
            remaining_time = int(LINK_COOLDOWN_SECONDS - (current_time - last_link_time))
            minutes = int(remaining_time / 60) + 1
            
            cooldown_msg = get_text(chat_id, 'link_cooldown_msg').format(minutes)

            safe_edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=cooldown_msg,
                reply_markup=main_keyboard(chat_id), 
                parse_mode="HTML"
            )
            check_link_request_spam(chat_id)
            return

        temp_message_text = get_text(chat_id, 'link_generated_msg').split("\n")[0].replace("✅", "⏳") + "\n" + "Please wait..."
        try:
            safe_edit_message_text(chat_id=chat_id, message_id=message_id, text=temp_message_text, parse_mode="HTML")
        except Exception:
            pass 

        threading.Thread(target=generate_invite_link_and_send, args=[chat_id, message_id, call.from_user]).start()
        return

    if chat_id != DEVELOPER_ID:
        return

    if data == 'dev_home':
        safe_edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=get_text(chat_id, 'welcome_developer'),
            reply_markup=developer_keyboard(chat_id),
            parse_mode="HTML"
        )
        return

    if data == 'maintenance_on':
        MAINTENANCE_MODE = True
        safe_edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=get_text(chat_id, 'maintenance_on_msg'),
            reply_markup=developer_keyboard(chat_id),
            parse_mode="HTML"
        )
        return

    if data == 'maintenance_off':
        MAINTENANCE_MODE = False
        safe_edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=get_text(chat_id, 'maintenance_off_msg'),
            reply_markup=developer_keyboard(chat_id),
            parse_mode="HTML"
        )
        return
    
    if data == 'show_resources':
        resources_info = get_resource_info(chat_id)
        safe_edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=resources_info,
            reply_markup=resources_back_keyboard(chat_id), 
            parse_mode="HTML"
        )
        return

@bot.chat_member_handler()
def handle_chat_member(chat_member_update: types.ChatMemberUpdated):
    try:
        user_id = chat_member_update.from_user.id
        new_status = chat_member_update.new_chat_member.status
        chat_id = chat_member_update.chat.id
        
        if chat_id == PRIVATE_CHANNEL_ID and new_status in ['member', 'administrator', 'creator']:
            if user_id in MONITORING_USERS:
                if MONITORING_USERS[user_id].get('timer'):
                    MONITORING_USERS[user_id]['timer'].cancel()
                
                safe_edit_message_text(
                    chat_id=user_id,
                    message_id=MONITORING_USERS[user_id]['message_id'],
                    text=get_text(user_id, 'join_success'),
                    parse_mode="HTML"
                )
                
                MONITORING_USERS.pop(user_id, None)

        if chat_id == PUBLIC_CHANNEL_ID and new_status in ['left', 'kicked', 'banned']:
            try:
                bot.unban_chat_member(PRIVATE_CHANNEL_ID, user_id)
                bot.send_message(user_id, get_text(user_id, 'sub_removed').format(PUBLIC_CHANNEL_ID), parse_mode="HTML", protect_content=True)
            except Exception as e:
                print(f"[WARNING] Channel leave handling: {e}")
    except Exception as e:
        print(f"[WARNING] Chat member handler: {e}")

def run_bot_forever():
    print("[INFO] Initializing Database...")
    init_db()
    
    connection_attempts = 0
    
    while True:
        try:
            print(f"[INFO] Bot starting... (Attempt {connection_attempts + 1})")
            bot.infinity_polling(timeout=30, long_polling_timeout=20, logger_level=None)
        except KeyboardInterrupt:
            print("\n[INFO] Bot stopped by user")
            break
        except telebot.apihelper.ApiTelegramException as e:
            connection_attempts += 1
            if "Too Many Requests" in str(e):
                print(f"[WARNING] Rate limited. Waiting 60 seconds...")
                time.sleep(60)
            else:
                print(f"[ERROR] Telegram API error: {e}")
                print(f"[INFO] Reconnecting in 10 seconds...")
                time.sleep(10)
        except Exception as e:
            connection_attempts += 1
            error_type = type(e).__name__
            if "Read timed out" in str(e) or "Connection" in str(e):
                print(f"[WARNING] Internet connection lost")
            else:
                print(f"[ERROR] Unexpected error ({error_type}): {e}")
            
            wait_time = min(10 * (2 ** (connection_attempts - 1)), 300)
            print(f"[INFO] Reconnecting in {wait_time} seconds...")
            time.sleep(wait_time)
            
            if connection_attempts % 10 == 0:
                print(f"[INFO] Cleanup cycle - resetting connection attempts")
                connection_attempts = 0

if __name__ == '__main__':
    run_bot_forever()
