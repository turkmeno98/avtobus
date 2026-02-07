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
ADMIN_IDS = [1135333763]  # Твой Telegram ID (@userinfobot)
DRIVER_ID = 1135333753  # ID водителя

# Инициализация
logging.basicConfig(level=logging.INFO)
bot = Bot(token=API_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
SCHEDULE_FILE = Path('schedule.json')

# Состояния FSM
class AdminStates(StatesGroup):
    waiting_date = State()
    waiting_direction = State()
    waiting_times = State()
    waiting_notify_chat = State()
    waiting_weekdays = State()
    waiting_saturday = State()
    waiting_holiday_date = State()

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
    if direction in date_changes and date_changes[direction] is not None:
        return date_changes[direction]
    
    return base_times

def calculate_arrival_time(departure_time_str):
    data = load_schedule()
    settings = data.get('настройки', {'время_в_пути_мин': 18})
    
    try:
        dep_time = datetime.strptime(departure_time_str, '%H:%M')
        arrival_time = dep_time + timedelta(minutes=settings['время_в_пути_мин'])
        return arrival_time.strftime('%H:%M')
    except:
        return f"{departure_time_str} (+18мин)"

def haversine(lat1, lon1, lat2, lon2):
    R = 6371
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat/2)**2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon/2)**2
    c = 2 * atan2(sqrt(a), sqrt(1-a))
    return R * c

def get_user_progress_on_route(lat, lon):
    zhirovsk = (50.976412, 44.777647)
    medveditsa = (51.082652, 44.816874)
    total_distance = haversine(*zhirnovsk_stop, *medveditsa_stop)  # ~12.7км
    dist_start = haversine(lat, lon, *zhirnovsk_stop)
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

# 📱 Пользовательские команды
@dp.message(F.text == '/start')
async def start_handler(msg: Message):
    text = """🚌 Бот расписания Жирновск ↔ Медведица

📋 /расписание - полное расписание
📍 Отправьте геолокацию для точного ETA

👨‍✈️ Водитель: /driver_mode"""
    
    kb = ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="📋 Расписание", request_location=False)],
        [KeyboardButton(text="📍 Моя геолокация", request_location=True)]
    ], resize_keyboard=True)
    
    await msg.answer(text, reply_markup=kb)

@dp.message(F.text.in_(['📋 Расписание', '/расписание']))
async def show_schedule(msg: Message):
    today = datetime.now().strftime('%Y-%m-%d')
    day_type = get_day_type(today)
    
    if day_type == 'выходной':
        await msg.answer("🛑 Сегодня выходной день. Рейсов нет.")
        return
    
    to_med = get_schedule("Жирновск→Медведица", today)
    back = get_schedule("Медведица→Жирновск", today)
    
    # Ближайший рейс по графику
    now = datetime.now().time()
    all_times = []
    for direction, times in [("Жирновск→Медведица", to_med), ("Медведица→Жирновск", back)]:
        for time_str in times:
            time_obj = datetime.strptime(time_str, '%H:%M').time()
            if time_obj > now:
                minutes_left = int((time_obj.hour * 60 + time_obj.minute - 
                                  now.hour * 60 - now.minute))
                all_times.append((time_str, direction, minutes_left))
    all_times.sort(key=lambda x: x[2])
    nearest_schedule = all_times[0] if all_times else None
    
    # 📍 GPS АВТОБУСА - ГЛАВНЫЙ БЛОК
    data = load_schedule()
    bus_pos = data.get('автобус_позиция', {})
    gps_status = ""
    
    if bus_pos and 'время' in bus_pos:
        pos_time = datetime.fromisoformat(bus_pos['время'])
        time_diff = (datetime.now() - pos_time).seconds / 60
        
        if time_diff < 5:  # Свежие данные <5мин
            progress = bus_pos.get('прогресс', 0)
            dist_from_start = 13.3 * progress / 100
            
            if progress < 50:  # Едет к Медведице
                eta_medveditsa = int((13.3 - dist_from_start) / (45/60))
                gps_status = f"""📍 Автобус в пути к Медведице!
🗺️ {progress:.0f}% маршрута ({dist_from_start:.1f}км)
⏰ Прибудет в Медведицу через {eta_medveditsa} мин"""
            else:  # Едет к Жирновску
                dist_to_zhirovsk = 13.3 - dist_from_start
                eta_zhirovsk = int(dist_to_zhirovsk / (45/60))
                gps_status = f"""📍 Автобус в пути к Жирновску!
🗺️ {progress:.0f}% маршрута ({dist_from_start:.1f}км)
⏰ Прибудет в Жирновск через {eta_zhirovsk} мин"""
        else:
            gps_status = f"📴 GPS устарел ({time_diff:.0f}мин назад)"
    else:
        gps_status = "📴 GPS автобуса недоступен"
    
    # Формируем полный текст
    day_name = {'будни': 'будни', 'суббота': 'суббота'}[day_type]
    text = f"""📅 Расписание на {datetime.now().strftime('%d.%m.%Y')} ({day_name})

{gps_status}

🚌 Жирновск → Медведица:
"""
    
    for time_str in to_med:
        arrival = calculate_arrival_time(time_str)
        text += f"• {time_str} → {arrival}\n"
    
    text += f"\n🚌 Медведица → Жирновск:\n"
    for time_str in back:
        arrival = calculate_arrival_time(time_str)
        text += f"• {time_str} → {arrival}\n"
    
    if nearest_schedule:
        next_time, next_dir, minutes = nearest_schedule
        arrival = calculate_arrival_time(next_time)
        text += f"\n🔔 Ближайший по графику:\n{next_time} ({next_dir})"
    else:
        text += f"\n🔔 Сегодня рейсов больше нет"
    
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
    
    # GPS водителя
    if msg.from_user.id == DRIVER_ID:
        data = load_schedule()
        data['автобус_позиция'] = {
            'lat': lat, 'lon': lon,
            'время': datetime.now().isoformat(),
            'прогресс': progress
        }
        save_schedule(data)
        await msg.answer("✅ GPS водителя обновлён! Пассажиры видят вас в реальном времени.")
        return
    
    # Пассажир
    eta = calculate_real_eta(lat, lon)
    
    if dist_start < 6.65:
        direction = "Жирновск→Медведица"
        times = get_schedule(direction, today)
        text = f"""📍 Вы в Жирновске ({progress:.0f}% маршрута)
🚌 До Медведицы: {', '.join(times) if times else 'нет рейсов'}
⏰ Автобус прибудет через: {eta}"""
    else:
        direction = "Медведица→Жирновск"
        times = get_schedule(direction, today)
        dist_to_end = 13.3 - dist_start
        text = f"""📍 Вы около Медведицы ({progress:.0f}% маршрута)
🚌 До Жирновска ({dist_to_end:.1f}км): {', '.join(times) if times else 'нет рейсов'}
⏰ Автобус прибудет через: {eta}"""
    
    await msg.answer(text)

@dp.message(F.text == '/driver_mode')
async def driver_mode(msg: Message):
    if msg.from_user.id != DRIVER_ID:
        await msg.answer("❌ Только для водителя.")
        return
    text = """🚍 РЕЖИМ ВОДИТЕЛЯ ВКЛЮЧЁН!

📍 КАК ОТПРАВИТЬ GPS АВТОМАТИЧЕСКИ:
1. Скрепка → Геопозиция
2. "Транслировать геопозицию" 
3. Выберите "1 час"
✅ GPS обновляется каждые 30 сек!

📱 Все пассажиры видят вас в /расписание"""
    await msg.answer(text)

# 🔧 АДМИН-ПАНЕЛЬ (полная версия)
@dp.message(F.text == '/admin')
async def admin_menu(msg: Message):
    if msg.from_user.id not in ADMIN_IDS:
        await msg.answer("❌ Доступ запрещён.")
        return
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton("📅 Базовое расписание", callback_data="base_schedule")],
        [InlineKeyboardButton("🛑 Изменить дату", callback_data="change_date")],
        [InlineKeyboardButton("🎉 Праздники", callback_data="holidays")],
        [InlineKeyboardButton("❌ Отменить рейс", callback_data="cancel_reys")],
        [InlineKeyboardButton("📢 Чат оповещений", callback_data="set_notify")]
    ])
    await msg.answer("🔧 Админ-панель:", reply_markup=kb)

@dp.callback_query(F.data == "admin_main")
async def admin_main_menu(callback: CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton("📅 Базовое расписание", callback_data="base_schedule")],
        [InlineKeyboardButton("🛑 Изменить дату", callback_data="change_date")],
        [InlineKeyboardButton("🎉 Праздники", callback_data="holidays")],
        [InlineKeyboardButton("❌ Отменить рейс", callback_data="cancel_reys")]
    ])
    await callback.message.edit_text("🔧 Админ-панель:", reply_markup=kb)
    await callback.answer()

@dp.callback_query(F.data == "base_schedule")
async def base_schedule_menu(callback: CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton("📋 Будни", callback_data="edit_weekdays")],
        [InlineKeyboardButton("📋 Суббота", callback_data="edit_saturday")],
        [InlineKeyboardButton("🔙 Главное меню", callback_data="admin_main")]
    ])
    await callback.message.edit_text("📅 Базовое расписание:", reply_markup=kb)
    await callback.answer()

@dp.callback_query(F.data == "edit_weekdays")
async def edit_weekdays(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AdminStates.waiting_weekdays)
    data = load_schedule()
    current = ', '.join(data['базовое_расписание']['будни']['Жирновск→Медведица'])
    text = f"📋 Будни (Жирновск→Медведица):\nТекущее: {current}\n\nНовое через запятую или 'отмена':"
    await callback.message.edit_text(text)
    await callback.answer()

@dp.message(AdminStates.waiting_weekdays)
async def save_weekdays(msg: Message, state: FSMContext):
    times_input = msg.text.strip().lower()
    data = load_schedule()
    
    if times_input == 'отмена':
        times = []
    else:
        times = [t.strip() for t in times_input.split(',')]
        times = [t for t in times if len(t) == 5 and t.count(':') == 1]
    
    data['базовое_расписание']['будни']['Жирновск→Медведица'] = times
    save_schedule(data)
    
    await msg.answer(f"✅ Будни: {', '.join(times) or 'отменено'}")
    await state.clear()

@dp.callback_query(F.data == "cancel_reys")
async def cancel_reys_menu(callback: CallbackQuery):
    today = datetime.now().strftime('%Y-%m-%d')
    to_med = get_schedule("Жирновск→Медведица", today)
    back = get_schedule("Медведица→Жирновск", today)
    
    kb = []
    for time in to_med:
        kb.append([InlineKeyboardButton(f"{time} →Медведица", callback_data=f"cancel_to_{time}")])
    for time in back:
        kb.append([InlineKeyboardButton(f"{time} ←Жирновск", callback_data=f"cancel_back_{time}")])
    kb.append([InlineKeyboardButton("🔙 Главное меню", callback_data="admin_main")])
    
    text = f"❌ Отменить рейс сегодня:\nВыберите время:"
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    await callback.answer()

@dp.callback_query(F.data.startswith("cancel_"))
async def process_cancel(callback: CallbackQuery):
    time_str = callback.data.split('_')[-1]
    direction = "Жирновск→Медведица" if "to_" in callback.data else "Медведица→Жирновск"
    today = datetime.now().strftime('%Y-%m-%d')
    
    data = load_schedule()
    if today not in data['изменения']:
        data['изменения'][today] = {}
    
    current_times = data['изменения'][today].get(direction, get_schedule(direction, today))
    new_times = [t for t in current_times if t != time_str]
    
    data['изменения'][today][direction] = new_times
    save_schedule(data)
    
    if data.get('notify_chat'):
        await bot.send_message(
            data['notify_chat'],
            f"🚨 ОТМЕНЁН рейс!\n{direction} в {time_str}\n📅 Сегодня"
        )
    
    await callback.answer("✅ Рейс отменён!", show_alert=True)

@dp.callback_query(F.data == "holidays")
async def holidays_menu(callback: CallbackQuery):
    data = load_schedule()
    holidays = data.get('праздники', [])
    
    kb = [
        [InlineKeyboardButton("➕ Добавить", callback_data="add_holiday")],
        [InlineKeyboardButton("➖ Удалить", callback_data="remove_holiday")]
    ]
    for date in holidays[:8]:
        kb.append([InlineKeyboardButton(f"🗑️ {date}", callback_data=f"del_holiday_{date}")])
    kb.append([InlineKeyboardButton("🔙 Главное меню", callback_data="admin_main")])
    
    text = f"🎉 Праздники:\n" + '\n'.join([f"• {h}" for h in holidays]) or "нет праздников"
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    await callback.answer()

@dp.callback_query(F.data.startswith("del_holiday_"))
async def delete_holiday(callback: CallbackQuery):
    date = callback.data.replace("del_holiday_", "")
    data = load_schedule()
    data['праздники'] = [h for h in data['праздники'] if h != date]
    save_schedule(data)
    await callback.answer(f"✅ {date} больше не праздник!", show_alert=True)

@dp.callback_query(F.data == "add_holiday")
async def add_holiday(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AdminStates.waiting_holiday_date)
    await callback.message.edit_text("📅 Дата праздника (YYYY-MM-DD):")
    await callback.answer()

@dp.message(AdminStates.waiting_holiday_date)
async def save_holiday(msg: Message, state: FSMContext):
    date_str = msg.text.strip()
    try:
        datetime.strptime(date_str, '%Y-%m-%d')
        data = load_schedule()
        if date_str not in data['праздники']:
            data['праздники'].append(date_str)
            save_schedule(data)
            await msg.answer(f"✅ {date_str} - выходной!")
        else:
            await msg.answer("❌ Уже праздник!")
    except:
        await msg.answer("❌ Формат: YYYY-MM-DD")
    await state.clear()

async def main():
    init_schedule()
    print("🚀 Бот автобуса Жирновск ↔ Медведица запущен!")
    print("📱 /start, /расписание, /admin")
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
