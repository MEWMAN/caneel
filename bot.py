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
DEVELOPER_ID = int(os.getenv("DEVELOPER_ID"))
PUBLIC_CHANNEL_ID = os.getenv("PUBLIC_CHANNEL_ID")
PRIVATE_CHANNEL_ID = int(os.getenv("PRIVATE_CHANNEL_ID"))
LINK_EXPIRY_SECONDS = int(os.getenv("LINK_EXPIRY_SECONDS", 60))

if not TOKEN:
    sys.exit(1)

bot = telebot.TeleBot(TOKEN)

AWAITING_BROADCAST_MESSAGE = {} 
LAST_LINK_TIME = {} 
MONITORING_USERS = {} 
USER_LANGUAGE_CACHE = {} 

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
    lang = USER_LANGUAGE_CACHE.get(chat_id)
    
    if lang is None:
        lang = get_user_language(chat_id)
        if lang is None:
            lang = 'ar' 
        USER_LANGUAGE_CACHE[chat_id] = lang
        
    return TEXTS.get(lang, TEXTS['ar']).get(key, TEXTS['ar'][key])


TEXTS = {
    'ar': {
        'welcome_choose_lang': "Hello! 👋\nPlease choose your preferred language:\n\n💡 <b>Note:</b> To change the language later, send the command: <code>/lang</code>",
        'welcome_developer': "Hello Developer! This is your control panel:",
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
        'resources_title': "📊 <b>Server Resources Usage</b> 📊",
        'cpu_usage': "--- 🖥️ Processor (CPU) ---\n<b>Usage:</b> <code>{}%</code>",
        'ram_usage': "--- 🧠 Memory (RAM) ---\n<b>Total:</b> <code>{:.2f} GB</code>\n<b>Available:</b> <code>{:.2f} GB</code>\n<b>Used:</b> <code>{}%</code>",
        'refresh_button': "Refresh 🔄",
        'broadcast_start': "📝 <b>Send the message now</b> that you want to broadcast to all users (text only).",
        'broadcast_success': "✅ <b>Broadcast successful!</b>\n<b>Sent to:</b> {} users.\n<b>Failed for:</b> {} users (due to bot block).",
        'broadcast_message_from_dev': "{}",
        'dev_controls_title': "Your control panel:"
    },
    'en': {
        'welcome_choose_lang': "Hello! 👋\nPlease choose your preferred language:\n\n💡 <b>Note:</b> To change the language later, send the command: <code>/lang</code>",
        'welcome_developer': "Hello Developer! This is your control panel:",
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
        'broadcast_start': "📝 <b>Send the message now</b> that you want to broadcast to all users (text only).",
        'broadcast_success': "✅ <b>Broadcast successful!</b>\n<b>Sent to:</b> {} users.\n<b>Failed for:</b> {} users (due to bot block).",
        'broadcast_message_from_dev': "{}",
        'dev_controls_title': "Your control panel:"
    },
    'ru': {
        'welcome_choose_lang': "Hello! 👋\nPlease choose your preferred language:\n\n💡 <b>Note:</b> To change the language later, send the command: <code>/lang</code>",
        'welcome_developer': "Здравствуйте, разработчик! Это ваша панель управления:",
        'lang_saved': "✅ Язык успешно изменен на русский!",
        'already_member': "✅ <b>Вы уже являетесь участником приватного канала!</b>\nВы не можете получить новую ссылку, пока не покинете канал.",
        'link_active': "⚠️ <b>Внимание: Предыдущая ссылка все еще активна!</b>\nПожалуйста, используйте ссылку, которую мы отправили ранее. Вы сможете запросить новую ссылку после истечения текущей (примерно через минуту).",
        'public_sub_ok': "✅ <b>Добро пожаловать!</b> Мы подтвердили вашу подписку на публичный канал.",
        'get_link_button': "Получить ссылку 🔗",
        'change_lang_button': "🌐 Изменить язык",
        'public_sub_fail': "🚫 <b>Подписка не удалась!</b>\nПожалуйста, сначала подпишитесь на публичный канал:\n{}\nЗатем нажмите /start снова.",
        'technical_error': "⚠️ <b>Извините, произошла техническая ошибка или проблема с правами доступа.</b>\nУбедитесь, что бот является **администратором** в обоих каналах (публичном и приватном) و имеет права на приглашение и исключение, затем повторите попытку.",
        'link_generated_msg': "✅ <b>Ссылка создана!</b>\n\nЭто ваша <b>временная ссылка-приглашение</b> в приватный канал:\n🔗 {}\n\n<b>Примечание:</b> Срок действия ссылки истечет через <b>одну минуту</b>. Пожалуйста, присоединяйтесь немедленно.",
        'link_failed': "⚠️ <b>Извините, не удалось создать ссылку!</b>\nПожалуйста, свяжитесь с администратором бота.",
        'link_expired': "⌛ <b>Извините, ваше время истекло!</b>\nСрок действия отправленной вам ссылки истек.",
        'join_success': "🎉 <b>Успешное присоединение!</b>\nДобро пожаловать в приватный канал. Временная ссылка отключена.",
        'sub_removed': "🚫 <b>Вы были исключены!</b> Вы отменили подписку на публичный канал.\nПожалуйста, подпишитесь снова на {} чтобы получить ссылку و вернуться в приватный канал.",
        'resources_title': "📊 <b>Использование ресурсов сервера</b> 📊",
        'cpu_usage': "--- 🖥️ Процессор (CPU) ---\n<b>Использование:</b> <code>{}%</code>",
        'ram_usage': "--- 🧠 Память (RAM) ---\n<b>Всего:</b> <code>{:.2f} GB</code>\n<b>Доступно:</b> <code>{:.2f} GB</code>\n<b>Использовано:</b> <code>{}%</code>",
        'refresh_button': "Обновить 🔄",
        'broadcast_start': "📝 <b>Отправьте сообщение сейчас</b>، которое вы хотите отправить всем пользователям (только текст).",
        'broadcast_success': "✅ <b>Рассылка прошла успешно!</b>\n<b>Отправлено:</b> {} пользователям.\n<b>Сбой у:</b> {} пользователей (из-за блокировки бота).",
        'broadcast_message_from_dev': "{}",
        'dev_controls_title': "Ваша панель управления:"
    }
}


def main_keyboard(chat_id):
    markup = types.InlineKeyboardMarkup()
    btn_get_link = types.InlineKeyboardButton(get_text(chat_id, 'get_link_button'), callback_data='generate_new_link')
    markup.add(btn_get_link)
    return markup

def language_change_only_keyboard(chat_id):
    return None 

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
    btn_broadcast = types.InlineKeyboardButton(get_text(chat_id, 'broadcast_start').split("</b>")[0].split("<b>")[1], callback_data='broadcast_start')
    btn_resources = types.InlineKeyboardButton(get_text(chat_id, 'resources_title').split("</b>")[0].split("<b>")[1], callback_data='show_resources')
    markup.add(btn_broadcast)
    markup.add(btn_resources) 
    return markup

def edit_link_message_expired(chat_id, message_id):
    try:
        bot.edit_message_text(
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
        if user_data.get('timer'): user_data['timer'].cancel()
        MONITORING_USERS.pop(chat_id, None)
        return

    try:
        member = bot.get_chat_member(PRIVATE_CHANNEL_ID, chat_id)
        if member.status in ['creator', 'administrator', 'member']:
            bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=get_text(chat_id, 'join_success'),
                parse_mode="HTML"
            )
            
            if user_data.get('timer'): user_data['timer'].cancel()
            MONITORING_USERS.pop(chat_id, None)
            return

    except Exception as e:
        print(f"Error checking private membership during monitoring for {chat_id}: {e}")

    new_timer = threading.Timer(5, monitor_user_join, args=[chat_id])
    user_data['timer'] = new_timer 
    new_timer.start()


def generate_invite_link_and_send(chat_id, message_id, user_info):
    try:
        LAST_LINK_TIME[chat_id] = time.time()
        user_label = f"@{user_info.username}" if user_info.username else f"ID: {chat_id}"

        invite_link = bot.create_chat_invite_link(PRIVATE_CHANNEL_ID, member_limit=1, expire_date=int(time.time()) + LINK_EXPIRY_SECONDS, name=user_label)
        link = invite_link.invite_link
        
        bot.edit_message_text(
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
        print(f"Error generating invite link for {chat_id}: {e}")
        bot.edit_message_text(
            chat_id=chat_id, 
            message_id=message_id,
            text=get_text(chat_id, 'link_failed') + " " + get_text(chat_id, 'technical_error'), 
            parse_mode="HTML"
        )


@bot.message_handler(commands=['start'])
def send_welcome(message):
    chat_id = message.chat.id
    current_time = time.time()
    
    if chat_id == DEVELOPER_ID:
        set_user_language(chat_id, 'ar') 
        USER_LANGUAGE_CACHE[chat_id] = 'ar' 
        bot.send_message(chat_id, get_text(chat_id, 'welcome_developer'), reply_markup=developer_keyboard(chat_id))
        return 

    user_lang = get_user_language(chat_id)
    
    if user_lang is None:
        bot.send_message(chat_id, TEXTS['ar']['welcome_choose_lang'], reply_markup=language_selection_keyboard(), parse_mode="HTML")
        return

    USER_LANGUAGE_CACHE[chat_id] = user_lang
    
    try:
        private_member = bot.get_chat_member(PRIVATE_CHANNEL_ID, chat_id)
        if private_member.status in ['creator', 'administrator', 'member']:
            bot.send_message(chat_id, get_text(chat_id, 'already_member'), parse_mode="HTML")
            return

        last_link_time = LAST_LINK_TIME.get(chat_id)
        if last_link_time and (current_time - last_link_time < LINK_EXPIRY_SECONDS + 5): 
            bot.send_message(chat_id, get_text(chat_id, 'link_active'), parse_mode="HTML")
            return

        public_member = bot.get_chat_member(PUBLIC_CHANNEL_ID, chat_id)
        
        if public_member.status in ['creator', 'administrator', 'member']:
            bot.send_message(chat_id, get_text(chat_id, 'public_sub_ok'), reply_markup=main_keyboard(chat_id), parse_mode="HTML")
        else:
            bot.send_message(chat_id, get_text(chat_id, 'public_sub_fail').format(PUBLIC_CHANNEL_ID), parse_mode="HTML")

    except Exception as e:
        print(f"Error in send_welcome for user {chat_id}: {e}")
        bot.send_message(chat_id, get_text(chat_id, 'technical_error'), parse_mode="HTML")

@bot.message_handler(commands=['lang'])
def handle_lang_command(message):
    chat_id = message.chat.id
    if get_user_language(chat_id) is None:
        set_user_language(chat_id, 'ar') 
        USER_LANGUAGE_CACHE[chat_id] = 'ar'
    
    bot.send_message(chat_id, get_text(chat_id, 'welcome_choose_lang'), reply_markup=language_selection_keyboard(), parse_mode="HTML")

@bot.callback_query_handler(func=lambda call: call.data.startswith(('set_lang_', 'generate_new_link', 'broadcast_start', 'show_resources_refresh')) or call.data.startswith('show_resources'))
def handle_callbacks(call):
    chat_id = call.message.chat.id
    message_id = call.message.message_id
    bot.answer_callback_query(call.id) 

    if call.data.startswith('set_lang_'):
        new_lang = call.data.split('_')[2]
        set_user_language(chat_id, new_lang)
        USER_LANGUAGE_CACHE[chat_id] = new_lang 
        
        lang_saved_key = 'lang_saved' 
        
        bot.edit_message_text(
            chat_id=chat_id, 
            message_id=message_id,
            text=get_text(chat_id, lang_saved_key), 
            parse_mode="HTML"
        )
        send_welcome(call.message)
        return

    if chat_id == DEVELOPER_ID:
        if call.data == 'broadcast_start':
            bot.send_message(chat_id, get_text(chat_id, 'broadcast_start'), parse_mode="HTML")
            AWAITING_BROADCAST_MESSAGE[chat_id] = True
            return
        
        elif call.data.startswith('show_resources'):
            resources_info = get_resource_info(chat_id)
            markup = types.InlineKeyboardMarkup()
            btn_refresh = types.InlineKeyboardButton(get_text(chat_id, 'refresh_button'), callback_data='show_resources_refresh')
            markup.add(btn_refresh) 
            
            try:
                bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=resources_info, parse_mode="HTML", reply_markup=markup)
            except Exception:
                 pass
            return

    if call.data == 'generate_new_link':
        try:
            private_member_status = bot.get_chat_member(PRIVATE_CHANNEL_ID, chat_id).status
            if private_member_status in ['creator', 'administrator', 'member']:
                bot.edit_message_text(chat_id=chat_id, message_id=message_id, 
                                      text=get_text(chat_id, 'already_member'), 
                                      parse_mode="HTML")
                return
            
            public_member = bot.get_chat_member(PUBLIC_CHANNEL_ID, chat_id)
            if public_member.status in ['creator', 'administrator', 'member']:
                generate_invite_link_and_send(chat_id, message_id, call.from_user)
            else:
                try:
                    bot.kick_chat_member(PRIVATE_CHANNEL_ID, chat_id, until_date=int(time.time() + 30))
                    bot.unban_chat_member(PRIVATE_CHANNEL_ID, chat_id) 
                except telebot.apihelper.ApiTelegramException as api_e:
                    if 'CHAT_ADMIN_REQUIRED' not in str(api_e) and 'user not found' not in str(api_e) and 'not a member' not in str(api_e):
                        print(f"Error during kick/unban for {chat_id}: {api_e}")

                bot.edit_message_text(chat_id=chat_id, message_id=message_id, 
                                      text=get_text(chat_id, 'sub_removed').format(PUBLIC_CHANNEL_ID),
                                      parse_mode="HTML")

        except Exception as e:
            print(f"Error re-checking subscription on callback for user {chat_id}: {e}")
            bot.edit_message_text(chat_id=chat_id, message_id=message_id, 
                                  text=get_text(chat_id, 'technical_error'),
                                  parse_mode="HTML")


@bot.message_handler(func=lambda message: message.chat.id in AWAITING_BROADCAST_MESSAGE and AWAITING_BROADCAST_MESSAGE[message.chat.id] == True)
def handle_broadcast_message(message):
    chat_id = message.chat.id
    
    if chat_id != DEVELOPER_ID:
        return

    del AWAITING_BROADCAST_MESSAGE[chat_id] 

    success_count = 0
    fail_count = 0
    
    broadcast_message_text = message.text

    all_users = get_all_user_ids()

    for user_id in all_users:
        if user_id != DEVELOPER_ID:
            try:
                bot.send_message(user_id, broadcast_message_text, parse_mode="HTML")
                success_count += 1
            except Exception:
                fail_count += 1

    bot.send_message(chat_id, get_text(chat_id, 'broadcast_success').format(success_count, fail_count), reply_markup=developer_keyboard(chat_id), parse_mode="HTML")


print("البوت يعمل...")
init_db() 
try:
    bot.polling(none_stop=True) 
except Exception as e:
    print(f"حدث خطأ أثناء تشغيل البوت: {e}")
    sys.exit()