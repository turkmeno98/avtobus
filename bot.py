import os
import asyncio
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from math import radians, sin, cos, sqrt, atan2

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton, Location
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

API_TOKEN = os.getenv('BOT_TOKEN')
ADMIN_IDS = [1135333763]  # Твой Telegram ID
DRIVER_ID = 1135333753   # ID водителя

# Инициализация
logging.basicConfig(level=logging.INFO)
bot = Bot(token=API_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
SCHEDULE_FILE = Path('schedule.json')

# Состояния FSM
class AdminStates(StatesGroup):
    waiting_weekdays = State()
    waiting_saturday = State()
    waiting_holiday_date = State()
    waiting_notify_chat = State()

# 🗄️ Работа с JSON
def init_schedule():
    if not SCHEDULE_FILE.exists():
        default_schedule = {
            "notify_chat": None,
            "автобус_позиция": {},
            "настройки": {
                "расстояние_км": 13.3,
                "скорость_кмч": 45,
                "время_в_пути_мин": 18
            },
            "базовое_расписание": {
                "будни": {
                    "Жирновск→Медведица": ["06:20", "07:20", "08:00", "09:00", "11:00", "13:00", "15:00", "17:00"],
                    "Медведица→Жирновск": ["06:50", "07:40", "08:30", "09:30", "11:30", "13:30", "15:30", "17:30"]
                },
                "суббота": {
                    "Жирновск→Медведица": ["07:00", "08:00", "09:00", "11:00", "13:00", "15:00"],
                    "Медведица→Жирновск": ["07:30", "08:30", "09:30", "11:30", "15:30"]
                }
            },
            "изменения": {},
            "праздники": ["2026-01-01", "2026-02-23", "2026-03-08", "2026-05-01", "2026-05-09"]
        }
        with open(SCHEDULE_FILE, 'w', encoding='utf-8') as f:
            json.dump(default_schedule, f, ensure_ascii=False, indent=2)

def load_schedule():
    with open(SCHEDULE_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)

def save_schedule(data):
    with open(SCHEDULE_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def get_day_type(date_str=None):
    if not date_str:
        date_str = datetime.now().strftime('%Y-%m-%d')
    
    dt = datetime.strptime(date_str, '%Y-%m-%d')
    weekday = dt.weekday()
    data = load_schedule()
    
    if date_str in data.get('праздники', []):
        return 'выходной'
    if weekday == 6:
        return 'выходной'
    if weekday == 5:
        return 'суббота'
    return 'будни'

def get_schedule(direction, date_str=None):
    day_type = get_day_type(date_str)
    if day_type == 'выходной':
        return []
    
    data = load_schedule()
    base_times = data['базовое_расписание'][day_type][direction]
    
    date_changes = data['изменения'].get(date_str, {})
    if direction in date_changes:
        return date_changes[direction]
    
    return base_times

def haversine(lat1, lon1, lat2, lon2):
    R = 6371
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat/2)**2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon/2)**2
    c = 2 * atan2(sqrt(a), sqrt(1-a))
    return R * c

def get_user_progress_on_route(lat, lon):
    zhirnovsk = (50.976412, 44.777647)
    medveditsa = (51.082652, 44.816874)
    
    total_distance = haversine(*zhirnovsk, *medveditsa)
    dist_start = haversine(lat, lon, *zhirnovsk)
    progress = min(dist_start / total_distance * 100, 100)
    
    return progress, dist_start

def calculate_real_eta(user_lat, user_lon):
    data = load_schedule()
    bus_pos = data.get('автобус_позиция', {})
    
    if bus_pos and 'время' in bus_pos:
        pos_time = datetime.fromisoformat(bus_pos['время'])
        if (datetime.now() - pos_time).seconds < 300:
            dist_to_user = haversine(user_lat, user_lon, bus_pos['lat'], bus_pos['lon'])
            speed_kmh = data['настройки'].get('скорость_кмч', 45)
            minutes = max(1, int(dist_to_user / (speed_kmh / 60)))
            return f"{minutes} мин (GPS)"
    
    return "по графику (~18мин)"

# 🛡️ Проверка админа
def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

# 📱 ГЛАВНОЕ МЕНЮ
@dp.message(F.text == '/start')
async def start_handler(msg: Message):
    text = "🚌 Бот расписания Жирновск ↔ Медведица"
    kb = ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="📋 Расписание")],
        [KeyboardButton(text="📍 Моя геолокация", request_location=True)]
    ], resize_keyboard=True)
    
    if is_admin(msg.from_user.id):
        kb.keyboard.append([KeyboardButton(text="🌐 Админ панель")])
        text += " | 🔧 Админ"
    
    await msg.answer(text, reply_markup=kb)

@dp.message(F.text == "📋 Расписание")
async def show_schedule(msg: Message):
    today = datetime.now().strftime('%Y-%m-%d')
    day_type = get_day_type(today)
    
    if day_type == 'выходной':
        await msg.answer("🛑 Сегодня выходной день. Рейсов нет.")
        return
    
    to_med = get_schedule("Жирновск→Медведица", today)
    back = get_schedule("Медведица→Жирновск", today)
    
    data = load_schedule()
    bus_pos = data.get('автобус_позиция', {})
    gps_status = "📴 GPS автобуса недоступен"
    
    if bus_pos and 'время' in bus_pos:
        pos_time = datetime.fromisoformat(bus_pos['время'])
        time_diff = (datetime.now() - pos_time).seconds / 60
        
        if time_diff < 5:
            progress = bus_pos.get('прогресс', 0)
            dist_from_start = 13.3 * progress / 100
            
            if progress < 50:
                eta_medveditsa = int((13.3 - dist_from_start) / (45/60))
                gps_status = f"📍 Автобус → Медведица ({progress:.0f}%) через {eta_medveditsa} мин"
            else:
                dist_to_zhirovsk = 13.3 - dist_from_start
                eta_zhirovsk = int(dist_to_zhirovsk / (45/60))
                gps_status = f"📍 Автобус → Жирновск ({progress:.0f}%) через {eta_zhirovsk} мин"
        else:
            gps_status = f"📴 GPS устарел ({time_diff:.0f}мин)"
    
    day_name = {'будни': 'Будни', 'суббота': 'Суббота'}[day_type]
    text = f"""📅 {datetime.now().strftime('%d.%m.%Y')} ({day_name})

📍 {gps_status}

🚌 Жирновск → Медведица:
{chr(10).join([f'• {t}' for t in to_med])}

🚌 Медведица → Жирновск:
{chr(10).join([f'• {t}' for t in back])}"""
    
    await msg.answer(text)

@dp.message(F.location)
async def handle_location(msg: Location):
    lat, lon = msg.location.latitude, msg.location.longitude
    progress, dist_start = get_user_progress_on_route(lat, lon)
    
    today = datetime.now().strftime('%Y-%m-%d')
    day_type = get_day_type(today)
    
    if day_type == 'выходной':
        await msg.answer("🛑 Сегодня выходной.")
        return
    
    # Водитель
    if msg.from_user.id == DRIVER_ID:
        data = load_schedule()
        data['автобус_позиция'] = {
            'lat': lat, 'lon': lon,
            'время': datetime.now().isoformat(),
            'прогресс': progress
        }
        save_schedule(data)
        await msg.answer("✅ GPS обновлён! Пассажиры видят вас.")
        return
    
    # Пассажир
    eta = calculate_real_eta(lat, lon)
    
    if dist_start < 6.65:
        times = get_schedule("Жирновск→Медведица", today)
        text = f"""📍 Вы в Жирновске ({progress:.0f}%)
🚌 До Медведицы: {', '.join(times) or 'нет рейсов'}
⏰ Автобус через: {eta}"""
    else:
        times = get_schedule("Медведица→Жирновск", today)
        dist_to_end = 13.3 - dist_start
        text = f"""📍 Вы около Медведицы ({progress:.0f}%)
🚌 До Жирновска: {', '.join(times) or 'нет рейсов'}
⏰ Автобус через: {eta}"""
    
    await msg.answer(text)

@dp.message(F.text == '/driver_mode')
async def driver_mode(msg: Message):
    if msg.from_user.id != DRIVER_ID:
        await msg.answer("❌ Только для водителя.")
        return
    await msg.answer("🚍 GPS отправляйте скрепкой → Геопозиция → 1 час")

# 🌐 АДМИН-ПАНЕЛЬ (✅ РАБОТАЕТ 100%)
@dp.message(F.text == "🌐 Админ панель")
async def admin_panel(msg: Message):
    if not is_admin(msg.from_user.id):
        await msg.answer("❌ Нет доступа!")
        return
    
    print(f"🔍 АДМИН {msg.from_user.id} зашёл в панель")
    
    kb = ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="📅 Настроить расписание")],
        [KeyboardButton(text="❌ Отменить рейс")],
        [KeyboardButton(text="🎉 Праздники")],
        [KeyboardButton(text="📢 Чат уведомлений")],
        [KeyboardButton(text="📊 Статистика"), KeyboardButton(text="🔙 Главное меню")]
    ], resize_keyboard=True)
    
    await msg.answer("🔧 АДМИН-ПАНЕЛЬ", reply_markup=kb)

@dp.message(F.text == "📅 Настроить расписание")
async def admin_schedule_menu(msg: Message):
    if not is_admin(msg.from_user.id): return
    
    data = load_schedule()
    weekdays = ', '.join(data['базовое_расписание']['будни']['Жирновск→Медведица'])
    saturday = ', '.join(data['базовое_расписание']['суббота']['Жирновск→Медведица'])
    
    kb = ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="📋 Будни"), KeyboardButton(text="📋 Суббота")],
        [KeyboardButton(text="🔙 Назад")]
    ], resize_keyboard=True)
    
    await msg.answer(f"""📅 ТЕКУЩЕЕ РАСПИСАНИЕ:
Будни: {weekdays}
Суббота: {saturday}

Выберите что редактировать:""", reply_markup=kb)

@dp.message(F.text == "📋 Будни")
async def edit_weekdays(msg: Message, state: FSMContext):
    if not is_admin(msg.from_user.id): return
    await state.set_state(AdminStates.waiting_weekdays)
    await msg.answer("📝 Введите время будней через запятую (06:20,07:20) или 'отмена':")
    
@dp.message(AdminStates.waiting_weekdays)
async def save_weekdays(msg: Message, state: FSMContext):
    times_input = msg.text.strip().lower()
    data = load_schedule()
    
    if times_input == 'отмена':
        times = []
    else:
        times = [t.strip() for t in times_input.split(',')]
        times = [t for t in times if len(t) == 5 and ':' in t]
    
    data['базовое_расписание']['будни']['Жирновск→Медведица'] = times
    data['базовое_расписание']['будни']['Медведица→Жирновск'] = [f"{t[:3]}30" for t in times]
    save_schedule(data)
    
    await msg.answer(f"✅ Будни: {', '.join(times) or 'отменено'}")
    await state.clear()
    await admin_panel(msg)

@dp.message(F.text == "📋 Суббота")
async def edit_saturday(msg: Message, state: FSMContext):
    if not is_admin(msg.from_user.id): return
    await state.set_state(AdminStates.waiting_saturday)
    await msg.answer("📝 Введите время субботы через запятую или 'отмена':")
    
@dp.message(AdminStates.waiting_saturday)
async def save_saturday(msg: Message, state: FSMContext):
    times_input = msg.text.strip().lower()
    data = load_schedule()
    
    if times_input == 'отмена':
        times = []
    else:
        times = [t.strip() for t in times_input.split(',')]
        times = [t for t in times if len(t) == 5 and ':' in t]
    
    data['базовое_расписание']['суббота']['Жирновск→Медведица'] = times
    data['базовое_расписание']['суббота']['Медведица→Жирновск'] = [f"{t[:3]}30" for t in times]
    save_schedule(data)
    
    await msg.answer(f"✅ Суббота: {', '.join(times) or 'отменено'}")
    await state.clear()
    await admin_panel(msg)

@dp.message(F.text == "❌ Отменить рейс")
async def cancel_reys(msg: Message):
    if not is_admin(msg.from_user.id): return
    
    today = datetime.now().strftime('%Y-%m-%d')
    to_med = get_schedule("Жирновск→Медведица", today)
    back = get_schedule("Медведица→Жирновск", today)
    
    text = f"🛑 Рейсы сегодня ({today}):\n\nЖирновск→Медведица:\n"
    for t in to_med:
        text += f"• {t}\n"
    text += f"\nМедведица→Жирновск:\n"
    for t in back:
        text += f"• {t}\n"
    
    kb = ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="🛑 Отменить Жирновск→Медведица")],
        [KeyboardButton(text="🛑 Отменить Медведица→Жирновск")],
        [KeyboardButton(text="🔙 Назад")]
    ], resize_keyboard=True)
    
    await msg.answer(text, reply_markup=kb)

@dp.message(F.text == "🛑 Отменить Жирновск→Медведица")
async def cancel_to_medveditsa(msg: Message):
    if not is_admin(msg.from_user.id): return
    
    today = datetime.now().strftime('%Y-%m-%d')
    data = load_schedule()
    if today not in data['изменения']:
        data['изменения'][today] = {}
    data['изменения'][today]["Жирновск→Медведица"] = []
    save_schedule(data)
    
    await msg.answer("✅ Все рейсы Жирновск→Медведица отменены!")
    await admin_panel(msg)

@dp.message(F.text == "🛑 Отменить Медведица→Жирновск")
async def cancel_back(msg: Message):
    if not is_admin(msg.from_user.id): return
    
    today = datetime.now().strftime('%Y-%m-%d')
    data = load_schedule()
    if today not in data['изменения']:
        data['изменения'][today] = {}
    data['изменения'][today]["Медведица→Жирновск"] = []
    save_schedule(data)
    
    await msg.answer("✅ Все рейсы Медведица→Жирновск отменены!")
    await admin_panel(msg)

@dp.message(F.text == "🎉 Праздники")
async def holidays_menu(msg: Message):
    if not is_admin(msg.from_user.id): return
    
    data = load_schedule()
    holidays = data.get('праздники', [])
    
    kb = ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="➕ Добавить праздник")],
        [KeyboardButton(text="➖ Удалить праздник")],
        [KeyboardButton(text="🔙 Назад")]
    ], resize_keyboard=True)
    
    text = "🎉 Праздники:\n" + "\n".join([f"• {h}" for h in holidays]) or "Праздников нет"
    await msg.answer(text, reply_markup=kb)

@dp.message(F.text == "➕ Добавить праздник")
async def add_holiday(msg: Message, state: FSMContext):
    if not is_admin(msg.from_user.id): return
    await state.set_state(AdminStates.waiting_holiday_date)
    await msg.answer("📅 Дата (YYYY-MM-DD):")

@dp.message(AdminStates.waiting_holiday_date)
async def save_holiday(msg: Message, state: FSMContext):
    date_str = msg.text.strip()
    try:
        datetime.strptime(date_str, '%Y-%m-%d')
        data = load_schedule()
        if date_str not in data['праздники']:
            data['праздники'].append(date_str)
            save_schedule(data)
            await msg.answer(f"✅ {date_str} добавлен в праздники!")
        else:
            await msg.answer("❌ Уже праздник!")
    except:
        await msg.answer("❌ Формат: YYYY-MM-DD")
    await state.clear()
    await holidays_menu(msg)

@dp.message(F.text == "➖ Удалить праздник")
async def remove_holiday_menu(msg: Message):
    if not is_admin(msg.from_user.id): return
    
    data = load_schedule()
    holidays = data.get('праздники', [])
    
    if not holidays:
        await msg.answer("Нет праздников для удаления")
        return
    
    kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=f"🗑️ {h}")] for h in holidays[:10]] + [[KeyboardButton(text="🔙 Назад")]], resize_keyboard=True)
    await msg.answer("Выберите праздник для удаления:", reply_markup=kb)

@dp.message(F.text.startswith("🗑️ "))
async def delete_holiday(msg: Message):
    if not is_admin(msg.from_user.id): return
    
    date = msg.text[2:].strip()
    data = load_schedule()
    data['праздники'] = [h for h in data['праздники'] if h != date]
    save_schedule(data)
    await msg.answer(f"✅ {date} удалён!")
    await holidays_menu(msg)

@dp.message(F.text == "📢 Чат уведомлений")
async def notify_chat_menu(msg: Message, state: FSMContext):
    if not is_admin(msg.from_user.id): return
    
    data = load_schedule()
    chat_id = data.get('notify_chat')
    text = f"📢 Чат уведомлений: {chat_id or 'НЕ УСТАНОВЛЕН'}\n\nОтправьте ID чата:"
    
    kb = ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="❌ Отключить")],
        [KeyboardButton(text="🔙 Назад")]
    ], resize_keyboard=True)
    
    await state.set_state(AdminStates.waiting_notify_chat)
    await msg.answer(text, reply_markup=kb)

@dp.message(AdminStates.waiting_notify_chat)
async def save_notify_chat(msg: Message, state: FSMContext):
    text = msg.text.strip()
    data = load_schedule()
    
    if text == "❌ Отключить":
        data['notify_chat'] = None
        await msg.answer("✅ Уведомления отключены")
    else:
        data['notify_chat'] = int(text)
        await msg.answer(f"✅ Чат {text} установлен")
    
    save_schedule(data)
    await state.clear()
    await admin_panel(msg)

@dp.message(F.text == "📊 Статистика")
async def show_stats(msg: Message):
    if not is_admin(msg.from_user.id): return
    
    data = load_schedule()
    today = datetime.now().strftime('%Y-%m-%d')
    
    text = f"""📊 СТАТИСТИКА:

📅 Сегодня: {today}
📍 GPS активен: {'✅' if data.get('автобус_позиция') else '❌'}
🎉 Праздников: {len(data.get('праздники', []))}
📢 Уведомления: {data.get('notify_chat', 'откл')}

Расписание будни: {len(data['базовое_расписание']['будни']['Жирновск→Медведица'])} рейсов"""
    
    await msg.answer(text)

# 🔙 НАВИГАЦИЯ
@dp.message(F.text.in_(["🔙 Главное меню", "🔙 Назад"]))
async def back_to_main(msg: Message):
    await start_handler(msg)

async def main():
    init_schedule()
    print("🚀 Бот автобуса запущен!")
    print(f"👨‍💼 Админы: {ADMIN_IDS}")
    print(f"🚗 Водитель: {DRIVER_ID}")
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
