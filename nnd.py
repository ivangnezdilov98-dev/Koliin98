import asyncio
import json
import os
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Any

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command, CommandStart
from aiogram.utils.keyboard import InlineKeyboardBuilder
from dotenv import load_dotenv
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage

# Загружаем переменные окружения
load_dotenv()

# ==================== КОНФИГУРАЦИЯ ====================
class Config:
    # Администраторы
    ADMIN_IDS = [1824049351, 5568154436]
    ADMIN_USERNAME = "@koliin98"
    
    # ID каналов для заявок
    PAYMENT_CHANNEL_ID = -1001862240317
    ORDER_CHANNEL_ID = -1002893927706
    
    # Настройки баланса
    BALANCE_DISCOUNT_PERCENT = 5
    MIN_DEPOSIT_AMOUNT = 100
    MAX_DEPOSIT_AMOUNT = 50000
    
    # Реквизиты для оплаты
    PAYMENT_DETAILS = {
        "sber": {
            "name": "СБП (Озон Банк)",
            "number": "+79225739192",
            "owner": "Иван Г."
        },
        "yoomoney": {
            "name": "ЮMoney",
            "number": "4100116710817606",
            "owner": "Иван Г."
        }
    }
    
    # Файлы данных
    DATA_FILE = "products_data.json"
    USERS_FILE = "users_data.json"

config = Config()

# Инициализация бота
bot = Bot(token=os.getenv('BOT_TOKEN'))

# Создаем storage и dispatcher
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# ==================== СОСТОЯНИЯ FSM ====================

class AddProductStates(StatesGroup):
    waiting_for_category = State()
    waiting_for_name = State()
    waiting_for_price = State()
    waiting_for_description = State()

class DepositStates(StatesGroup):
    waiting_for_amount = State()

class PaymentStates(StatesGroup):
    waiting_for_screenshot = State()

# ==================== БАЗА ДАННЫХ ====================

class Database:
    def __init__(self):
        self.carts: Dict[int, Dict] = {}
        self.products: List[Dict] = []
        self.categories: List[Dict] = []
        self.users: Dict[int, Dict] = {}
        self.transactions: List[Dict] = []
        self.pending_orders: Dict[str, Dict] = {}  # Ожидающие подтверждения заказы
        self.pending_deposits: Dict[str, Dict] = {}  # Ожидающие подтверждения пополнения
        self.settings: Dict[str, Any] = {
            "balance_discount": config.BALANCE_DISCOUNT_PERCENT,
            "min_deposit": config.MIN_DEPOSIT_AMOUNT,
            "max_deposit": config.MAX_DEPOSIT_AMOUNT
        }
        self.load_data()
    
    def load_data(self):
        """Загружаем данные из файлов"""
        try:
            # Загружаем товары и категории
            if os.path.exists(config.DATA_FILE):
                with open(config.DATA_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.products = data.get('products', [])
                    self.categories = data.get('categories', [])
            else:
                self.categories = [
                    {"id": 1, "name": "💻 Цифровые услуги"},
                    {"id": 2, "name": "🎨 Дизайн"},
                    {"id": 3, "name": "📝 Контент"}
                ]
                self.save_products_data()
            
            # Загружаем пользователей
            if os.path.exists(config.USERS_FILE):
                with open(config.USERS_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    users_data = data.get('users', {})
                    self.users = {int(k): v for k, v in users_data.items()}
                    self.transactions = data.get('transactions', [])
                    self.settings = data.get('settings', self.settings)
                    self.pending_orders = data.get('pending_orders', {})
                    self.pending_deposits = data.get('pending_deposits', {})
        except Exception as e:
            print(f"Ошибка загрузки данных: {e}")
            self.products = []
            self.categories = []
            self.users = {}
            self.transactions = []
            self.pending_orders = {}
            self.pending_deposits = {}
    
    def save_products_data(self):
        """Сохраняем товары и категории"""
        try:
            data = {
                "products": self.products,
                "categories": self.categories
            }
            with open(config.DATA_FILE, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"Ошибка сохранения товаров: {e}")
    
    def save_users_data(self):
        """Сохраняем пользователей"""
        try:
            data = {
                "users": self.users,
                "transactions": self.transactions,
                "settings": self.settings,
                "pending_orders": self.pending_orders,
                "pending_deposits": self.pending_deposits
            }
            with open(config.USERS_FILE, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"Ошибка сохранения пользователей: {e}")
    
    # Работа с пользователями
    def get_user(self, user_id: int) -> Dict:
        if user_id not in self.users:
            self.users[user_id] = {
                "balance": 0.0,
                "total_spent": 0.0,
                "total_orders": 0,
                "registration_date": datetime.now().isoformat(),
                "last_activity": datetime.now().isoformat()
            }
            self.save_users_data()
        return self.users[user_id]
    
    def get_user_balance(self, user_id: int) -> float:
        user = self.get_user(user_id)
        return user.get("balance", 0.0)
    
    def add_balance(self, user_id: int, amount: float, description: str = "") -> bool:
        try:
            user = self.get_user(user_id)
            user["balance"] = user.get("balance", 0.0) + amount
            
            transaction = {
                "id": len(self.transactions) + 1,
                "user_id": user_id,
                "type": "deposit",
                "amount": amount,
                "description": description,
                "date": datetime.now().isoformat()
            }
            self.transactions.append(transaction)
            
            self.save_users_data()
            return True
        except Exception as e:
            print(f"Ошибка добавления баланса: {e}")
            return False
    
    def deduct_balance(self, user_id: int, amount: float, description: str = "") -> Tuple[bool, str]:
        try:
            user = self.get_user(user_id)
            current_balance = user.get("balance", 0.0)
            
            if current_balance < amount:
                return False, "Недостаточно средств на балансе"
            
            user["balance"] = current_balance - amount
            user["total_spent"] = user.get("total_spent", 0.0) + amount
            user["total_orders"] = user.get("total_orders", 0) + 1
            
            transaction = {
                "id": len(self.transactions) + 1,
                "user_id": user_id,
                "type": "purchase",
                "amount": -amount,
                "description": description,
                "date": datetime.now().isoformat()
            }
            self.transactions.append(transaction)
            
            self.save_users_data()
            return True, "Оплата прошла успешно"
        except Exception as e:
            print(f"Ошибка списания баланса: {e}")
            return False, "Ошибка при списании средств"
    
    # Работа с ожидающими подтверждениями
    def add_pending_deposit(self, deposit_id: str, deposit_data: Dict):
        """Добавить ожидающее пополнение"""
        self.pending_deposits[deposit_id] = deposit_data
        self.save_users_data()
    
    def get_pending_deposit(self, deposit_id: str) -> Optional[Dict]:
        """Получить ожидающее пополнение"""
        return self.pending_deposits.get(deposit_id)
    
    def remove_pending_deposit(self, deposit_id: str):
        """Удалить ожидающее пополнение"""
        if deposit_id in self.pending_deposits:
            del self.pending_deposits[deposit_id]
            self.save_users_data()
    
    def add_pending_order(self, order_id: str, order_data: Dict):
        """Добавить ожидающий заказ"""
        self.pending_orders[order_id] = order_data
        self.save_users_data()
    
    def get_pending_order(self, order_id: str) -> Optional[Dict]:
        """Получить ожидающий заказ"""
        return self.pending_orders.get(order_id)
    
    def remove_pending_order(self, order_id: str):
        """Удалить ожидающий заказ"""
        if order_id in self.pending_orders:
            del self.pending_orders[order_id]
            self.save_users_data()
    
    # Работа с категориями и товарами
    def get_categories(self) -> List[Dict]:
        return self.categories
    
    def get_category(self, category_id: int) -> Optional[Dict]:
        for category in self.categories:
            if category["id"] == category_id:
                return category
        return None
    
    def add_category(self, name: str) -> int:
        new_id = max([cat["id"] for cat in self.categories], default=0) + 1
        self.categories.append({"id": new_id, "name": name})
        self.save_products_data()
        return new_id
    
    def get_products_by_category(self, category_id: int) -> List[Dict]:
        return [p for p in self.products if p["category_id"] == category_id]
    
    def get_product(self, product_id: int) -> Optional[Dict]:
        for product in self.products:
            if product["id"] == product_id:
                return product
        return None
    
    def add_product(self, category_id: int, name: str, price: float, description: str = "", quantity: int = 9999) -> int:
        new_id = max([prod["id"] for prod in self.products], default=0) + 1
        product = {
            "id": new_id,
            "category_id": category_id,
            "name": name,
            "price": price,
            "description": description,
            "quantity": quantity
        }
        self.products.append(product)
        self.save_products_data()
        return new_id
    
    def delete_product(self, product_id: int) -> bool:
        initial_len = len(self.products)
        self.products = [prod for prod in self.products if prod["id"] != product_id]
        self.save_products_data()
        return len(self.products) < initial_len
    
    # Работа с корзиной
    def get_cart(self, user_id: int) -> Dict:
        if user_id not in self.carts:
            self.carts[user_id] = {"items": {}, "total": 0.0}
        return self.carts[user_id]
    
    def add_to_cart(self, user_id: int, product_id: int) -> Tuple[bool, str]:
        cart = self.get_cart(user_id)
        product = self.get_product(product_id)
        
        if not product:
            return False, "Товар не найден"
        
        if product["quantity"] <= 0:
            return False, "Товар закончился"
        
        if product_id in cart["items"]:
            if cart["items"][product_id]["quantity"] >= product["quantity"]:
                return False, f"Доступно только {product['quantity']} шт."
            cart["items"][product_id]["quantity"] += 1
        else:
            cart["items"][product_id] = {
                "product": product,
                "quantity": 1
            }
        
        cart["total"] = sum(item["product"]["price"] * item["quantity"] 
                           for item in cart["items"].values())
        return True, "Товар добавлен в корзину"
    
    def remove_from_cart(self, user_id: int, product_id: int) -> bool:
        cart = self.get_cart(user_id)
        if product_id in cart["items"]:
            if cart["items"][product_id]["quantity"] > 1:
                cart["items"][product_id]["quantity"] -= 1
            else:
                del cart["items"][product_id]
            
            if cart["items"]:
                cart["total"] = sum(item["product"]["price"] * item["quantity"] 
                                   for item in cart["items"].values())
            else:
                cart["total"] = 0.0
            return True
        return False
    
    def clear_cart(self, user_id: int):
        if user_id in self.carts:
            self.carts[user_id] = {"items": {}, "total": 0.0}

db = Database()

# ==================== УТИЛИТЫ ====================

async def send_to_payment_channel(deposit_data: Dict, screenshot_file_id: str = None) -> Optional[int]:
    """
    Отправить заявку на пополнение в канал с кнопками подтверждения
    """
    try:
        # Формируем текст сообщения
        user_info = deposit_data.get('username', 'без username')
        user_id = deposit_data.get('user_id')
        amount = deposit_data.get('amount', 0)
        method = deposit_data.get('method', 'Неизвестно')
        transaction_id = deposit_data.get('transaction_id', 'N/A')
        
        message_text = f"""
🔄 ЗАЯВКА НА ПОПОЛНЕНИЕ БАЛАНСА

👤 Пользователь: @{user_info}
🆔 ID: {user_id}
💰 Сумма: {amount:.2f}₽
💳 Способ: {method}
📅 Дата: {datetime.now().strftime('%d.%m.%Y %H:%M')}
🆔 ID транзакции: {transaction_id}
"""
        
        if screenshot_file_id:
            message_text += "\n📸 Прикреплен скриншот оплаты"
        
        # Сохраняем данные в ожидающие
        db.add_pending_deposit(transaction_id, {
            'user_id': user_id,
            'username': user_info,
            'amount': amount,
            'method': method,
            'transaction_id': transaction_id
        })
        
        # Создаем клавиатуру с кнопками подтверждения
        builder = InlineKeyboardBuilder()
        builder.row(
            InlineKeyboardButton(
                text='✅ Подтвердить пополнение',
                callback_data=f'confirm_deposit_{transaction_id}'
            )
        )
        builder.row(
            InlineKeyboardButton(
                text='❌ Отклонить',
                callback_data=f'reject_deposit_{transaction_id}'
            )
        )
        
        # Отправляем сообщение в канал
        if screenshot_file_id:
            message = await bot.send_photo(
                chat_id=config.PAYMENT_CHANNEL_ID,
                photo=screenshot_file_id,
                caption=message_text,
                reply_markup=builder.as_markup()
            )
        else:
            message = await bot.send_message(
                chat_id=config.PAYMENT_CHANNEL_ID,
                text=message_text,
                reply_markup=builder.as_markup()
            )
        
        print(f"✅ Заявка на пополнение отправлена в канал. Message ID: {message.message_id}")
        return message.message_id
        
    except Exception as e:
        print(f"❌ Ошибка отправки в канал оплаты: {e}")
        return None

async def send_to_order_channel(order_data: Dict, screenshot_file_id: str = None) -> Optional[int]:
    """
    Отправить заявку на покупку в канал заказов с кнопками подтверждения
    """
    try:
        # Формируем основную информацию
        user_info = order_data.get('username', 'без username')
        user_id = order_data.get('user_id')
        order_id = order_data.get('order_id', 'N/A')
        total_amount = order_data.get('total', 0)
        payment_method = order_data.get('payment_method', 'Не указан')
        
        message_text = f"""
🛒 НОВЫЙ ЗАКАЗ

👤 Покупатель: @{user_info}
🆔 ID: {user_id}
💰 Сумма: {total_amount:.2f}₽
💳 Способ оплаты: {payment_method}
📅 Дата: {datetime.now().strftime('%d.%m.%Y %H:%M')}
🆔 ID заказа: {order_id}
"""
        
        # Добавляем информацию о товарах
        items = order_data.get('items', [])
        if items:
            message_text += "\n📋 Состав заказа:\n"
            for item in items:
                item_name = item.get('name', 'Неизвестный товар')
                item_quantity = item.get('quantity', 1)
                item_price = item.get('price', 0)
                item_total = item_quantity * item_price
                
                message_text += f"• {item_name}\n"
                message_text += f"  {item_quantity}шт. × {item_price:.2f}₽ = {item_total:.2f}₽\n"
        
        message_text += f"\n💰 ИТОГО: {total_amount:.2f}₽"
        
        # Добавляем информацию о скидке, если есть
        if order_data.get('discount_percent'):
            discount = order_data.get('discount_percent')
            discount_amount = order_data.get('discount_amount', 0)
            original_total = order_data.get('original_total', total_amount)
            
            message_text += f"\n🎁 Скидка: {discount}% ({discount_amount:.2f}₽)"
            message_text += f"\n💵 Изначальная сумма: {original_total:.2f}₽"
        
        if screenshot_file_id:
            message_text += "\n📸 Прикреплен скриншот оплаты"
        
        # Сохраняем данные заказа
        db.add_pending_order(order_id, {
            'user_id': user_id,
            'username': user_info,
            'order_id': order_id,
            'total': total_amount,
            'payment_method': payment_method,
            'items': items,
            'discount_percent': order_data.get('discount_percent'),
            'discount_amount': order_data.get('discount_amount'),
            'original_total': order_data.get('original_total')
        })
        
        # Создаем клавиатуру с кнопками подтверждения
        builder = InlineKeyboardBuilder()
        builder.row(
            InlineKeyboardButton(
                text='✅ Подтвердить заказ',
                callback_data=f'confirm_order_{order_id}'
            )
        )
        builder.row(
            InlineKeyboardButton(
                text='❌ Отклонить',
                callback_data=f'reject_order_{order_id}'
            )
        )
        
        # Отправляем сообщение в канал
        if screenshot_file_id:
            message = await bot.send_photo(
                chat_id=config.ORDER_CHANNEL_ID,
                photo=screenshot_file_id,
                caption=message_text,
                reply_markup=builder.as_markup()
            )
        else:
            message = await bot.send_message(
                chat_id=config.ORDER_CHANNEL_ID,
                text=message_text,
                reply_markup=builder.as_markup()
            )
        
        print(f"✅ Заказ отправлен в канал. Message ID: {message.message_id}")
        return message.message_id
        
    except Exception as e:
        print(f"❌ Ошибка отправки в канал заказов: {e}")
        return None

# ==================== КЛАВИАТУРЫ ====================

def main_menu_kb(user_id: int = None) -> InlineKeyboardMarkup:
    """Главное меню с учетом прав администратора"""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text='🛒 Посмотреть услуги', callback_data='view_categories'),
        InlineKeyboardButton(text='📦 Моя корзина', callback_data='view_cart'),
    )
    builder.row(
        InlineKeyboardButton(text='👤 Мой профиль', callback_data='my_profile'),
        InlineKeyboardButton(text='💳 Пополнить баланс', callback_data='deposit'),
    )
    
    # Добавляем кнопку админ-панели для администраторов
    if user_id in config.ADMIN_IDS:
        builder.row(
            InlineKeyboardButton(text='👨‍💼 Админ-панель', callback_data='admin_panel'),
        )
    
    return builder.as_markup()

def profile_kb() -> InlineKeyboardMarkup:
    """Меню профиля"""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text='💼 История операций', callback_data='transaction_history'),
        InlineKeyboardButton(text='💳 Пополнить баланс', callback_data='deposit'),
    )
    builder.row(
        InlineKeyboardButton(text='🔙 Главное меню', callback_data='main_menu'),
    )
    return builder.as_markup()

def categories_kb() -> InlineKeyboardMarkup:
    """Категории товаров"""
    builder = InlineKeyboardBuilder()
    categories = db.get_categories()
    
    for category in categories:
        builder.row(
            InlineKeyboardButton(
                text=category["name"], 
                callback_data=f"category_{category['id']}"
            )
        )
    
    builder.row(
        InlineKeyboardButton(text='🔙 Назад', callback_data='main_menu'),
        InlineKeyboardButton(text='📦 Корзина', callback_data='view_cart')
    )
    return builder.as_markup()

def products_kb(category_id: int) -> InlineKeyboardMarkup:
    """Товары в категории"""
    builder = InlineKeyboardBuilder()
    products = db.get_products_by_category(category_id)
    
    for product in products:
        builder.row(
            InlineKeyboardButton(
                text=f"{product['name']} - {product['price']}₽",
                callback_data=f"product_{product['id']}"
            )
        )
    
    builder.row(
        InlineKeyboardButton(text='🔙 Назад к категориям', callback_data='view_categories'),
        InlineKeyboardButton(text='📦 Корзина', callback_data='view_cart')
    )
    return builder.as_markup()

def product_detail_kb(product_id: int, category_id: int) -> InlineKeyboardMarkup:
    """Детали товара"""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text='➕ Добавить в корзину', callback_data=f'add_to_cart_{product_id}'),
        InlineKeyboardButton(text='➖ Убрать из корзины', callback_data=f'remove_from_cart_{product_id}')
    )
    builder.row(
        InlineKeyboardButton(text='🔙 Назад', callback_data=f'category_{category_id}'),
        InlineKeyboardButton(text='📦 Корзина', callback_data='view_cart')
    )
    return builder.as_markup()

def deposit_methods_kb() -> InlineKeyboardMarkup:
    """Способы пополнения"""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text='🏦 СБП (Озон)', callback_data='deposit_sber'),
    )
    builder.row(
        InlineKeyboardButton(text='💰 ЮMoney', callback_data='deposit_yoomoney'),
    )
    builder.row(
        InlineKeyboardButton(text='🔙 Назад', callback_data='my_profile'),
    )
    return builder.as_markup()

def cart_kb(with_balance: bool = False) -> InlineKeyboardMarkup:
    """Корзина"""
    builder = InlineKeyboardBuilder()
    
    if with_balance:
        builder.row(
            InlineKeyboardButton(text='💳 Оплатить балансом', callback_data='checkout_balance'),
        )
    
    builder.row(
        InlineKeyboardButton(text='💳 Другие способы', callback_data='checkout'),
        InlineKeyboardButton(text='🛒 Продолжить покупки', callback_data='view_categories')
    )
    builder.row(
        InlineKeyboardButton(text='🗑️ Очистить корзину', callback_data='clear_cart'),
        InlineKeyboardButton(text='🔙 Главное меню', callback_data='main_menu')
    )
    return builder.as_markup()

def payment_choice_kb() -> InlineKeyboardMarkup:
    """Выбор способа оплаты"""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text='💳 С баланса бота', callback_data='pay_balance'),
        InlineKeyboardButton(text='🏦 СБП (Озон)', callback_data='pay_sber'),
    )
    builder.row(
        InlineKeyboardButton(text='💰 ЮMoney', callback_data='pay_yoomoney'),
    )
    builder.row(
        InlineKeyboardButton(text='🔙 Назад', callback_data='view_cart'),
    )
    return builder.as_markup()

def cancel_kb() -> InlineKeyboardMarkup:
    """Клавиатура отмены"""
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text='❌ Отмена', callback_data='main_menu'))
    return builder.as_markup()

def admin_panel_kb() -> InlineKeyboardMarkup:
    """Клавиатура админ-панели"""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text='📦 Управление товарами', callback_data='admin_products'),
        InlineKeyboardButton(text='📁 Управление категориями', callback_data='admin_categories')
    )
    builder.row(
        InlineKeyboardButton(text='👥 Пользователи', callback_data='admin_users'),
        InlineKeyboardButton(text='📊 Статистика', callback_data='admin_stats')
    )
    builder.row(
        InlineKeyboardButton(text='⏳ Ожидающие заявки', callback_data='admin_pending'),
        InlineKeyboardButton(text='⚙️ Настройки', callback_data='admin_settings')
    )
    builder.row(
        InlineKeyboardButton(text='🔙 Главное меню', callback_data='main_menu')
    )
    return builder.as_markup()

# ==================== ОБРАБОТЧИКИ КОМАНД ====================

@dp.message(CommandStart())
async def handle_start(message: Message):
    """Обработка команды /start"""
    try:
        user_id = message.from_user.id
        
        # Регистрируем пользователя
        db.get_user(user_id)
        
        welcome_text = f"""
👋 Добро пожаловать в магазин виртуальных услуг!

✨ Возможности:
• 🛒 Просмотр и покупка услуг
• 💳 Личный баланс
• 🎁 Скидка {db.settings.get('balance_discount', 10)}% при оплате с баланса
• ✅ Подтверждение заказов администраторами

Используйте кнопки ниже для навигации:
"""
        
        await message.answer(
            text=welcome_text,
            reply_markup=main_menu_kb(user_id)
        )
        
    except Exception as e:
        print(f"Ошибка при обработке /start: {e}")
        await message.answer("❌ Произошла ошибка при запуске")

@dp.message(Command("profile"))
async def handle_profile_command(message: Message):
    """Обработка команды /profile"""
    try:
        user = db.get_user(message.from_user.id)
        balance = user.get("balance", 0.0)
        total_spent = user.get("total_spent", 0.0)
        total_orders = user.get("total_orders", 0)
        
        profile_text = f"""
👤 Ваш профиль

💰 Баланс: {balance:.2f}₽
💳 Всего потрачено: {total_spent:.2f}₽
📦 Заказов: {total_orders}
🎁 Скидка при оплате с баланса: {db.settings.get('balance_discount', 10)}%
"""
        
        await message.answer(
            text=profile_text,
            reply_markup=profile_kb()
        )
        
    except Exception as e:
        print(f"Ошибка при обработке /profile: {e}")
        await message.answer("❌ Ошибка при загрузке профиля")

@dp.message(Command("admin"))
async def handle_admin_command(message: Message):
    """Обработка команды /admin"""
    try:
        user_id = message.from_user.id
        
        if user_id not in config.ADMIN_IDS:
            await message.answer("⛔ У вас нет прав администратора")
            return
        
        admin_text = """
👨‍💼 Админ-панель

Доступные команды:
• /addproduct - Добавить новый товар
• /addcategory <название> - Добавить категорию
• /stats - Показать статистику
• /addbalance <id> <сумма> - Добавить баланс пользователю

Или используйте кнопки ниже:
"""
        
        await message.answer(
            text=admin_text,
            reply_markup=admin_panel_kb()
        )
        
    except Exception as e:
        print(f"Ошибка при обработке /admin: {e}")
        await message.answer("❌ Ошибка при загрузке админ-панели")

# ==================== ОСНОВНЫЕ ОБРАБОТЧИКИ ====================

@dp.callback_query(F.data == 'main_menu')
async def handle_main_menu(callback: CallbackQuery, state: FSMContext):
    """Обработка перехода в главное меню"""
    try:
        await state.clear()
        
        await callback.message.edit_text(
            text="🏠 Главное меню\n\nВыберите действие:",
            reply_markup=main_menu_kb(callback.from_user.id)
        )
        
    except Exception as e:
        print(f"Ошибка при переходе в главное меню: {e}")
        await callback.answer("Произошла ошибка", show_alert=True)
    
    await callback.answer()

@dp.callback_query(F.data == 'my_profile')
async def handle_my_profile(callback: CallbackQuery):
    """Обработка перехода в профиль"""
    try:
        user = db.get_user(callback.from_user.id)
        balance = user.get("balance", 0.0)
        total_spent = user.get("total_spent", 0.0)
        total_orders = user.get("total_orders", 0)
        
        profile_text = f"""
👤 Ваш профиль

💰 Баланс: {balance:.2f}₽
💳 Всего потрачено: {total_spent:.2f}₽
📦 Заказов: {total_orders}
🎁 Скидка при оплате с баланса: {db.settings.get('balance_discount', 10)}%
"""
        
        await callback.message.edit_text(
            text=profile_text,
            reply_markup=profile_kb()
        )
        
    except Exception as e:
        print(f"Ошибка при загрузке профиля: {e}")
        await callback.answer("Ошибка загрузки профиля", show_alert=True)
    
    await callback.answer()

@dp.callback_query(F.data == 'view_categories')
async def handle_view_categories(callback: CallbackQuery):
    """Показать список категорий"""
    try:
        categories = db.get_categories()
        
        if not categories:
            text = "📭 Категории пока отсутствуют"
        else:
            text = "📁 Выберите категорию:"
        
        await callback.message.edit_text(
            text=text,
            reply_markup=categories_kb()
        )
        
    except Exception as e:
        print(f"Ошибка при загрузке категорий: {e}")
        await callback.answer("Ошибка загрузки категорий", show_alert=True)
    
    await callback.answer()

@dp.callback_query(F.data.startswith('category_'))
async def handle_category_products(callback: CallbackQuery):
    """Показать товары в выбранной категории"""
    try:
        # Извлекаем ID категории
        _, category_id_str = callback.data.split('_')
        category_id = int(category_id_str)
        
        # Получаем категорию и товары
        category = db.get_category(category_id)
        products = db.get_products_by_category(category_id)
        
        if not products:
            category_name = category.get('name', 'Неизвестно') if category else 'Неизвестно'
            text = f"📭 В категории '{category_name}' пока нет товаров"
        else:
            category_name = category.get('name', 'Неизвестно') if category else 'Неизвестно'
            text = f"🛒 Товары в категории '{category_name}':"
        
        await callback.message.edit_text(
            text=text,
            reply_markup=products_kb(category_id)
        )
        
    except ValueError:
        await callback.answer("Неверный ID категории", show_alert=True)
    except Exception as e:
        print(f"Ошибка при загрузке товаров категории: {e}")
        await callback.answer("Ошибка загрузки товаров", show_alert=True)
    
    await callback.answer()

@dp.callback_query(F.data.startswith('product_'))
async def handle_product_detail(callback: CallbackQuery):
    """Показать детали товара"""
    try:
        # Извлекаем ID товара
        _, product_id_str = callback.data.split('_')
        product_id = int(product_id_str)
        
        # Получаем информацию о товаре
        product = db.get_product(product_id)
        if not product:
            await callback.answer("Товар не найден", show_alert=True)
            return
        
        # Получаем информацию о категории
        category = db.get_category(product["category_id"])
        
        # Формируем текст
        product_text = f"""
📦 {product['name']}

💰 Цена: {product['price']:.2f}₽
📝 Описание: {product.get('description', 'Нет описания')}
📊 В наличии: {product.get('quantity', 9999)} шт.
📁 Категория: {category.get('name', 'Не указана') if category else 'Не указана'}
"""
        
        await callback.message.edit_text(
            text=product_text,
            reply_markup=product_detail_kb(product_id, product["category_id"])
        )
        
    except ValueError:
        await callback.answer("Неверный ID товара", show_alert=True)
    except Exception as e:
        print(f"Ошибка при загрузке товара: {e}")
        await callback.answer("Ошибка загрузки товара", show_alert=True)
    
    await callback.answer()
@dp.callback_query(F.data.startswith('add_to_cart_'))
async def handle_add_to_cart(callback: CallbackQuery):
    """Добавить товар в корзину"""
    try:
        # Извлекаем ID товара
        parts = callback.data.split('_')
        product_id_str = parts[-1]
        product_id = int(product_id_str)
        
        # Добавляем товар в корзину
        user_id = callback.from_user.id
        success, message = db.add_to_cart(user_id, product_id)
        
        if success:
            await callback.answer(f"✅ {message}")
        else:
            await callback.answer(f"❌ {message}", show_alert=True)
            
    except ValueError:
        await callback.answer("Неверный ID товара", show_alert=True)
    except Exception as e:
        print(f"Ошибка при добавлении в корзину: {e}")
        await callback.answer("Ошибка при добавлении в корзину", show_alert=True)

@dp.callback_query(F.data.startswith('remove_from_cart_'))
async def handle_remove_from_cart(callback: CallbackQuery):
    """Удалить товар из корзины"""
    try:
        # Извлекаем ID товара
        parts = callback.data.split('_')
        product_id_str = parts[-1]
        product_id = int(product_id_str)
        
        # Удаляем товар из корзины
        user_id = callback.from_user.id
        success = db.remove_from_cart(user_id, product_id)
        
        if success:
            await callback.answer("✅ Товар удален из корзины")
        else:
            await callback.answer("Товар не найден в корзине", show_alert=True)
            
    except ValueError:
        await callback.answer("Неверный ID товара", show_alert=True)
    except Exception as e:
        print(f"Ошибка при удалении из корзины: {e}")
        await callback.answer("Ошибка при удалении из корзины", show_alert=True)

@dp.callback_query(F.data == 'view_cart')
async def handle_view_cart(callback: CallbackQuery):
    """Показать содержимое корзины"""
    try:
        user_id = callback.from_user.id
        cart = db.get_cart(user_id)
        user_balance = db.get_user_balance(user_id)
        
        if not cart["items"]:
            # Корзина пуста
            cart_text = "📭 Ваша корзина пуста"
            keyboard = cart_kb()
        else:
            # Формируем список товаров в корзине
            items_text = []
            total_price = 0
            
            for item_id, item_data in cart["items"].items():
                product = item_data["product"]
                quantity = item_data["quantity"]
                item_total = product['price'] * quantity
                total_price += item_total
                
                items_text.append(
                    f"• {product['name']}\n"
                    f"  Количество: {quantity} × {product['price']:.2f}₽ = {item_total:.2f}₽"
                )
            
            # Рассчитываем скидку
            discount_percent = db.settings.get("balance_discount", 10)
            discount_amount = total_price * discount_percent / 100
            price_with_discount = total_price - discount_amount
            
            # Формируем итоговый текст
            cart_text = f"""
📦 Ваша корзина:

{chr(10).join(items_text)}

💰 Итого: {total_price:.2f}₽

🎁 При оплате с баланса:
• Скидка: {discount_percent}% (-{discount_amount:.2f}₽)
• К оплате: {price_with_discount:.2f}₽

💳 Ваш баланс: {user_balance:.2f}₽
"""
            
            # Показываем кнопку оплаты балансом, если достаточно средств
            keyboard = cart_kb(with_balance=(user_balance >= price_with_discount))
        
        await callback.message.edit_text(
            text=cart_text,
            reply_markup=keyboard
        )
        
    except Exception as e:
        print(f"Ошибка при загрузке корзины: {e}")
        await callback.answer("Ошибка загрузки корзины", show_alert=True)
    
    await callback.answer()

@dp.callback_query(F.data == 'clear_cart')
async def handle_clear_cart(callback: CallbackQuery):
    """Очистить корзину"""
    try:
        user_id = callback.from_user.id
        db.clear_cart(user_id)
        
        await callback.message.edit_text(
            text="✅ Корзина успешно очищена",
            reply_markup=main_menu_kb(callback.from_user.id)
        )
        
    except Exception as e:
        print(f"Ошибка при очистке корзины: {e}")
        await callback.answer("Ошибка при очистке корзины", show_alert=True)
    
    await callback.answer()

@dp.callback_query(F.data == 'transaction_history')
async def handle_transaction_history(callback: CallbackQuery):
    """Показать историю транзакций пользователя"""
    try:
        user_id = callback.from_user.id
        transactions = [t for t in db.transactions if t["user_id"] == user_id]
        
        if not transactions:
            history_text = "📭 У вас еще нет транзакций"
        else:
            # Форматируем последние 10 транзакций
            history_items = []
            for trans in transactions[-10:]:  # Последние 10 транзакций
                date = datetime.fromisoformat(trans['date']).strftime('%d.%m.%Y %H:%M')
                amount = trans['amount']
                trans_type = trans['type']
                
                if trans_type == 'deposit':
                    icon = "⬆️"
                    amount_text = f"+{amount:.2f}₽"
                else:
                    icon = "⬇️"
                    amount_text = f"-{abs(amount):.2f}₽"
                
                history_items.append(
                    f"{icon} {date}: {amount_text} - {trans.get('description', 'Транзакция')}"
                )
            
            history_text = f"""
📊 История транзакций:

{chr(10).join(history_items)}
"""
        
        # Создаем клавиатуру
        builder = InlineKeyboardBuilder()
        builder.row(
            InlineKeyboardButton(text='💳 Пополнить баланс', callback_data='deposit'),
            InlineKeyboardButton(text='🔙 Назад', callback_data='my_profile')
        )
        
        await callback.message.edit_text(
            text=history_text,
            reply_markup=builder.as_markup()
        )
        
    except Exception as e:
        print(f"Ошибка при загрузке истории транзакций: {e}")
        await callback.answer("Ошибка загрузки истории", show_alert=True)
    
    await callback.answer()

# ==================== ПОПОЛНЕНИЕ БАЛАНСА ====================

@dp.callback_query(F.data == 'deposit')
async def handle_deposit(callback: CallbackQuery, state: FSMContext):
    """Начать процесс пополнения баланса"""
    try:
        # Сохраняем текущее состояние для возврата
        await state.set_state(DepositStates.waiting_for_amount)
        
        # Получаем текущие настройки лимитов
        min_deposit = db.settings.get('min_deposit', 100)
        max_deposit = db.settings.get('max_deposit', 50000)
        
        deposit_text = f"""
💳 Пополнение баланса

💰 Лимиты:
• Минимальная сумма: {min_deposit:.2f}₽
• Максимальная сумма: {max_deposit:.2f}₽

Введите сумму пополнения (в рублях):
"""
        
        await callback.message.edit_text(
            text=deposit_text,
            reply_markup=cancel_kb()
        )
        
    except Exception as e:
        print(f"Ошибка при инициализации пополнения: {e}")
        await callback.answer("Ошибка начала пополнения", show_alert=True)
        await state.clear()
    
    await callback.answer()

@dp.message(DepositStates.waiting_for_amount)
async def handle_deposit_amount(message: Message, state: FSMContext):
    """Обработать введенную сумму пополнения"""
    try:
        # Очищаем пробелы и заменяем запятые на точки
        amount_text = message.text.strip().replace(',', '.')
        
        try:
            amount = float(amount_text)
        except ValueError:
            await message.answer(
                text="❌ Неверный формат суммы!\n\n"
                     "Пожалуйста, введите число.\n"
                     "Пример: 1000 или 1500.50",
                reply_markup=cancel_kb()
            )
            return
        
        # Проверяем лимиты
        min_deposit = db.settings.get('min_deposit', 100)
        max_deposit = db.settings.get('max_deposit', 50000)
        
        if amount < min_deposit:
            await message.answer(
                text=f"❌ Сумма слишком мала!\n\n"
                     f"Минимальная сумма пополнения: {min_deposit:.2f}₽\n"
                     f"Пожалуйста, введите большую сумму:",
                reply_markup=cancel_kb()
            )
            return
        
        if amount > max_deposit:
            await message.answer(
                text=f"❌ Сумма слишком велика!\n\n"
                     f"Максимальная сумма пополнения: {max_deposit:.2f}₽\n"
                     f"Пожалуйста, введите меньшую сумму:",
                reply_markup=cancel_kb()
            )
            return
        
        # Сохраняем сумму и переходим к выбору метода оплаты
        await state.update_data(amount=amount)
        
        # Очищаем состояние, чтобы не оставаться в FSM
        await state.clear()
        
        await message.answer(
            text=f"✅ Выбрана сумма: {amount:.2f}₽\n\n"
                 "Выберите способ оплаты:",
            reply_markup=deposit_methods_kb()
        )
        
    except Exception as e:
        print(f"Ошибка при обработке суммы пополнения: {e}")
        await message.answer(
            text="❌ Произошла ошибка при обработке суммы",
            reply_markup=main_menu_kb(message.from_user.id)
        )
        await state.clear()

@dp.callback_query(F.data.startswith('deposit_'))
async def handle_deposit_method(callback: CallbackQuery, state: FSMContext):
    """Обработать выбор метода оплаты для пополнения"""
    try:
        # Извлекаем метод оплаты из callback_data
        method = callback.data.replace('deposit_', '')
        
        # Проверяем существование метода
        if method not in config.PAYMENT_DETAILS:
            await callback.answer("Неизвестный способ оплаты", show_alert=True)
            return
        
        # Получаем информацию о методе оплаты
        payment_info = config.PAYMENT_DETAILS[method]
        
        # Создаем уникальный ID транзакции
        transaction_id = f"DEP_{callback.from_user.id}_{int(datetime.now().timestamp())}"
        
        # Устанавливаем состояние ожидания скриншота
        await state.set_state(PaymentStates.waiting_for_screenshot)
        
        # Сохраняем данные платежа
        await state.update_data(
            user_id=callback.from_user.id,
            username=callback.from_user.username or "без username",
            amount=1000,  # В реальном коде нужно получать из предыдущего шага
            payment_method=method,
            payment_name=payment_info['name'],
            transaction_id=transaction_id,
            payment_type='deposit'
        )
        
        # Формируем инструкцию для пользователя
        if method == 'sber':
            payment_text = f"""
🏦 Оплата через {payment_info['name']}

💰 Для пополнения баланса:

📱 Номер телефона:
{payment_info['number']}

👤 Получатель:
{payment_info['owner']}

📝 В комментарии к переводу укажите:
Пополнение #{callback.from_user.id}
"""
        elif method == 'yoomoney':
            payment_text = f"""
💰 Оплата через {payment_info['name']}

💰 Для пополнения баланса:

💳 Номер кошелька:
{payment_info['number']}

👤 Получатель:
{payment_info['owner']}

📝 В комментарии к переводу укажите:
Пополнение #{callback.from_user.id}
"""
        
        payment_text += "\n\n📸 После оплаты отправьте скриншот чека в этот чат"
        
        await callback.message.edit_text(
            text=payment_text
        )
        
    except Exception as e:
        print(f"Ошибка при выборе метода оплаты: {e}")
        await callback.answer("Ошибка выбора метода оплаты", show_alert=True)
        await state.clear()
    
    await callback.answer()

# ==================== ОПЛАТА ЗАКАЗА ====================

@dp.callback_query(F.data == 'checkout')
async def handle_checkout(callback: CallbackQuery):
    """Начать оформление заказа"""
    try:
        user_id = callback.from_user.id
        cart = db.get_cart(user_id)
        
        # Проверяем, что корзина не пуста
        if not cart["items"]:
            await callback.answer("Ваша корзина пуста", show_alert=True)
            return
        
        # Рассчитываем сумму и скидку
        total_amount = cart['total']
        discount_percent = db.settings.get("balance_discount", 10)
        discount_amount = total_amount * discount_percent / 100
        discounted_total = total_amount - discount_amount
        
        checkout_text = f"""
🛒 Оформление заказа

💰 Общая сумма: {total_amount:.2f}₽

🎁 При оплате с баланса:
• Скидка: {discount_percent}%
• Экономия: {discount_amount:.2f}₽
• К оплате: {discounted_total:.2f}₽

💳 Выберите способ оплаты:
"""
        
        await callback.message.edit_text(
            text=checkout_text,
            reply_markup=payment_choice_kb()
        )
        
    except Exception as e:
        print(f"Ошибка при оформлении заказа: {e}")
        await callback.answer("Ошибка оформления заказа", show_alert=True)
    
    await callback.answer()

@dp.callback_query(F.data == 'checkout_balance')
async def handle_checkout_balance(callback: CallbackQuery):
    """Обработать оплату заказа с баланса"""
    try:
        user_id = callback.from_user.id
        cart = db.get_cart(user_id)
        
        # Проверяем, что корзина не пуста
        if not cart["items"]:
            await callback.answer("Ваша корзина пуста", show_alert=True)
            return
        
        # Рассчитываем сумму со скидкой
        total_amount = cart['total']
        discount_percent = db.settings.get("balance_discount", 10)
        discount_amount = total_amount * discount_percent / 100
        final_amount = total_amount - discount_amount
        
        # Проверяем баланс пользователя
        user_balance = db.get_user_balance(user_id)
        
        if user_balance < final_amount:
            await callback.answer(
                f"❌ Недостаточно средств на балансе!\n"
                f"Нужно: {final_amount:.2f}₽\n"
                f"Доступно: {user_balance:.2f}₽",
                show_alert=True
            )
            return
        
        # Формируем подтверждение
        confirm_text = f"""
💳 Подтверждение оплаты

📦 Детали заказа:
• Товаров: {len(cart['items'])} позиций
• Общая сумма: {total_amount:.2f}₽
• Скидка: {discount_percent}% (-{discount_amount:.2f}₽)

💰 К оплате: {final_amount:.2f}₽
💳 Ваш баланс: {user_balance:.2f}₽
💳 Баланс после оплаты: {user_balance - final_amount:.2f}₽

Подтвердить оплату с баланса?
"""
        
        # Создаем клавиатуру подтверждения
        builder = InlineKeyboardBuilder()
        builder.row(
            InlineKeyboardButton(text='✅ Да, оплатить', callback_data='confirm_balance_payment'),
            InlineKeyboardButton(text='❌ Нет, отмена', callback_data='view_cart')
        )
        
        await callback.message.edit_text(
            text=confirm_text,
            reply_markup=builder.as_markup()
        )
        
    except Exception as e:
        print(f"Ошибка при оплате с баланса: {e}")
        await callback.answer("Ошибка при обработке оплаты", show_alert=True)
    
    await callback.answer()

@dp.callback_query(F.data == 'confirm_balance_payment')
async def handle_confirm_balance_payment(callback: CallbackQuery):
    """Подтвердить и обработать оплату с баланса"""
    try:
        user_id = callback.from_user.id
        username = callback.from_user.username or "без username"
        cart = db.get_cart(user_id)
        
        # Рассчитываем финальную сумму
        total_amount = cart['total']
        discount_percent = db.settings.get("balance_discount", 10)
        discount_amount = total_amount * discount_percent / 100
        final_amount = total_amount - discount_amount
        
        # Генерируем ID заказа
        order_id = f"ORD_{user_id}_{int(datetime.now().timestamp())}"
        
        # Формируем список товаров
        items_list = []
        for item_id, item_data in cart["items"].items():
            product = item_data["product"]
            items_list.append({
                'id': product['id'],
                'name': product['name'],
                'price': product['price'],
                'quantity': item_data['quantity']
            })
        
        # Списываем средства с баланса
        success, message = db.deduct_balance(
            user_id=user_id,
            amount=final_amount,
            description=f"Оплата заказа {order_id} (скидка {discount_percent}%)"
        )
        
        if not success:
            await callback.answer(f"❌ {message}", show_alert=True)
            return
        
        # Формируем данные заказа для отправки в канал
        order_data = {
            'user_id': user_id,
            'username': username,
            'order_id': order_id,
            'total': final_amount,
            'original_total': total_amount,
            'discount_percent': discount_percent,
            'discount_amount': discount_amount,
            'payment_method': 'Баланс бота',
            'items': items_list
        }
        
        # Отправляем заказ в канал
        await send_to_order_channel(order_data)
        
        # Очищаем корзину
        db.clear_cart(user_id)
        
        # Формируем сообщение об успехе
        success_text = f"""
✅ Заказ успешно оплачен!

🆔 Номер заказа: {order_id}
💰 Сумма: {final_amount:.2f}₽
🎁 Скидка: {discount_percent}% ({discount_amount:.2f}₽)
📦 Товаров: {len(items_list)} позиций
💳 Способ оплаты: Баланс бота
💳 Остаток баланса: {db.get_user_balance(user_id):.2f}₽

📋 Заказ отправлен на обработку.
Мы свяжемся с вами в ближайшее время.
"""
        
        await callback.message.edit_text(
            text=success_text,
            reply_markup=main_menu_kb(user_id)
        )
        
    except Exception as e:
        print(f"Ошибка при подтверждении оплаты: {e}")
        await callback.answer("Ошибка при подтверждении оплаты", show_alert=True)
    
    await callback.answer()

@dp.callback_query(F.data.startswith('pay_'))
async def handle_external_payment(callback: CallbackQuery, state: FSMContext):
    """Обработать выбор внешнего способа оплаты"""
    try:
        # Извлекаем метод оплаты
        method = callback.data.replace('pay_', '')
        
        # Проверяем существование метода
        if method not in config.PAYMENT_DETAILS:
            await callback.answer("Неизвестный способ оплаты", show_alert=True)
            return
        
        user_id = callback.from_user.id
        username = callback.from_user.username or "без username"
        cart = db.get_cart(user_id)
        
        # Проверяем, что корзина не пуста
        if not cart["items"]:
            await callback.answer("Ваша корзина пуста", show_alert=True)
            return
        
        # Получаем информацию о методе оплаты
        payment_info = config.PAYMENT_DETAILS[method]
        
        # Генерируем ID заказа
        order_id = f"ORD_{user_id}_{int(datetime.now().timestamp())}"
        
        # Формируем список товаров
        items_list = []
        for item_id, item_data in cart["items"].items():
            product = item_data["product"]
            items_list.append({
                'id': product['id'],
                'name': product['name'],
                'price': product['price'],
                'quantity': item_data['quantity']
            })
        
        # Сохраняем данные в состоянии
        await state.set_state(PaymentStates.waiting_for_screenshot)
        await state.update_data(
            user_id=user_id,
            username=username,
            payment_method=method,
            payment_name=payment_info['name'],
            order_id=order_id,
            total_amount=cart['total'],
            items=items_list,
            payment_type='purchase'
        )
        
        # Формируем инструкцию для пользователя
        if method == 'sber':
            payment_text = f"""
🏦 Оплата через {payment_info['name']}

💰 Сумма к оплате: {cart['total']:.2f}₽

📱 Номер телефона:
{payment_info['number']}

👤 Получатель:
{payment_info['owner']}

📝 В комментарии к переводу укажите:
Заказ {order_id}
"""
        elif method == 'yoomoney':
            payment_text = f"""
💰 Оплата через {payment_info['name']}

💰 Сумма к оплате: {cart['total']:.2f}₽

💳 Номер кошелька:
{payment_info['number']}

👤 Получатель:
{payment_info['owner']}

📝 В комментарии к переводу укажите:
Заказ {order_id}
"""
        
        payment_text += "\n\n📸 После оплаты отправьте скриншот чека в этот чат"
        
        await callback.message.edit_text(
            text=payment_text
        )
        
    except Exception as e:
        print(f"Ошибка при выборе внешнего способа оплаты: {e}")
        await callback.answer("Ошибка выбора способа оплаты", show_alert=True)
        await state.clear()
    
    await callback.answer()

# ==================== ОБРАБОТКА СКРИНШОТОВ ====================

@dp.message(PaymentStates.waiting_for_screenshot, F.photo)
async def handle_payment_screenshot(message: Message, state: FSMContext):
    """Обработать полученный скриншот оплаты"""
    try:
        # Получаем file_id самого большого размера фото
        file_id = message.photo[-1].file_id
        
        # Получаем данные платежа из состояния
        data = await state.get_data()
        payment_type = data.get('payment_type')
        
        # Очищаем состояние
        await state.clear()
        
        # Обрабатываем в зависимости от типа платежа
        if payment_type == 'deposit':
            await _process_deposit_screenshot(message, data, file_id)
        elif payment_type == 'purchase':
            await _process_purchase_screenshot(message, data, file_id)
        else:
            await message.answer(
                text="❌ Неизвестный тип платежа",
                reply_markup=main_menu_kb(message.from_user.id)
            )
        
    except Exception as e:
        print(f"Ошибка при обработке скриншота: {e}")
        await message.answer(
            text="❌ Ошибка при обработке скриншота",
            reply_markup=main_menu_kb(message.from_user.id)
        )
        await state.clear()

async def _process_deposit_screenshot(message: Message, data: dict, file_id: str):
    """Обработать скриншот пополнения баланса"""
    try:
        user_id = data.get('user_id')
        username = data.get('username')
        payment_name = data.get('payment_name')
        transaction_id = data.get('transaction_id')
        amount = data.get('amount', 0)
        
        # Формируем данные для отправки в канал
        deposit_data = {
            'user_id': user_id,
            'username': username,
            'amount': amount,
            'method': payment_name,
            'transaction_id': transaction_id
        }
        
        # Отправляем в канал
        await send_to_payment_channel(deposit_data, file_id)
        
        # Уведомляем пользователя
        success_text = f"""
✅ Скриншот получен!

💳 Пополнение баланса
💰 Сумма: {amount:.2f}₽
🏦 Способ: {payment_name}
🆔 ID: {transaction_id}

📋 Заявка отправлена на проверку.
Баланс будет зачислен после подтверждения администратором.
"""
        
        await message.answer(
            text=success_text,
            reply_markup=main_menu_kb(user_id)
        )
        
    except Exception as e:
        print(f"Ошибка при обработке скриншота пополнения: {e}")
        raise

async def _process_purchase_screenshot(message: Message, data: dict, file_id: str):
    """Обработать скриншот оплаты заказа"""
    try:
        user_id = data.get('user_id')
        username = data.get('username')
        payment_name = data.get('payment_name')
        order_id = data.get('order_id')
        total_amount = data.get('total_amount', 0)
        items = data.get('items', [])
        
        # Формируем данные заказа
        order_data = {
            'user_id': user_id,
            'username': username,
            'order_id': order_id,
            'total': total_amount,
            'payment_method': payment_name,
            'items': items
        }
        
        # Отправляем в канал
        await send_to_order_channel(order_data, file_id)
        
        # Очищаем корзину пользователя
        db.clear_cart(user_id)
        
        # Уведомляем пользователя
        success_text = f"""
✅ Заказ оформлен!

🆔 Номер заказа: {order_id}
💰 Сумма: {total_amount:.2f}₽
💳 Способ оплаты: {payment_name}
📦 Товаров: {len(items)} позиций

📋 Заказ отправлен на обработку.
Мы свяжемся с вами в ближайшее время.
"""
        
        await message.answer(
            text=success_text,
            reply_markup=main_menu_kb(user_id)
        )
        
    except Exception as e:
        print(f"Ошибка при обработке скриншота заказа: {e}")
        raise

@dp.message(PaymentStates.waiting_for_screenshot)
async def handle_invalid_screenshot(message: Message, state: FSMContext):
    """Обработать некорректный ввод вместо скриншота"""
    await message.answer(
        text="❌ Пожалуйста, отправьте СКРИНШОТ чека оплаты.\n\n"
             "Чтобы отправить скриншот:\n"
             "1. Нажмите на значок 📎 (скрепка)\n"
             "2. Выберите 'Фото'\n"
             "3. Выберите сделанный скриншот\n"
             "4. Нажмите 'Отправить'"
    )

# ==================== ОБРАБОТЧИКИ ПОДТВЕРЖДЕНИЯ АДМИНИСТРАТОРОМ ====================

@dp.callback_query(F.data.startswith('confirm_deposit_'))
async def handle_confirm_deposit(callback: CallbackQuery):
    """Подтвердить пополнение администратором"""
    try:
        # Проверяем права администратора
        if callback.from_user.id not in config.ADMIN_IDS:
            await callback.answer("⛔ Нет доступа", show_alert=True)
            return
        
        # Извлекаем ID транзакции
        transaction_id = callback.data.replace('confirm_deposit_', '')
        
        # Получаем данные пополнения
        deposit_data = db.get_pending_deposit(transaction_id)
        if not deposit_data:
            await callback.answer("Заявка не найдена", show_alert=True)
            return
        
        user_id = deposit_data.get('user_id')
        amount = deposit_data.get('amount', 0)
        
        # Добавляем баланс пользователю
        db.add_balance(
            user_id=user_id,
            amount=amount,
            description=f"Пополнение подтверждено администратором"
        )
        
        # Удаляем из ожидающих
        db.remove_pending_deposit(transaction_id)
        
        # Обновляем сообщение в канале
        try:
            if callback.message.photo:
                await bot.edit_message_caption(
                    chat_id=callback.message.chat.id,
                    message_id=callback.message.message_id,
                    caption=callback.message.caption + f"\n\n✅ ПОДТВЕРЖДЕНО АДМИНИСТРАТОРОМ: @{callback.from_user.username}",
                    reply_markup=None
                )
            else:
                await bot.edit_message_text(
                    chat_id=callback.message.chat.id,
                    message_id=callback.message.message_id,
                    text=callback.message.text + f"\n\n✅ ПОДТВЕРЖДЕНО АДМИНИСТРАТОРОМ: @{callback.from_user.username}",
                    reply_markup=None
                )
        except Exception as e:
            print(f"Ошибка обновления сообщения: {e}")
        
        # Уведомляем пользователя
        try:
            await bot.send_message(
                chat_id=user_id,
                text=f"✅ Ваше пополнение подтверждено администратором!\n\n"
                     f"💰 Сумма: {amount:.2f}₽\n"
                     f"💳 Текущий баланс: {db.get_user_balance(user_id):.2f}₽"
            )
        except Exception as e:
            print(f"Ошибка уведомления пользователя: {e}")
        
        await callback.answer("✅ Пополнение подтверждено")
        
    except Exception as e:
        print(f"Ошибка при подтверждении пополнения: {e}")
        await callback.answer("❌ Ошибка при подтверждении", show_alert=True)

@dp.callback_query(F.data.startswith('reject_deposit_'))
async def handle_reject_deposit(callback: CallbackQuery):
    """Отклонить пополнение администратором"""
    try:
        # Проверяем права администратора
        if callback.from_user.id not in config.ADMIN_IDS:
            await callback.answer("⛔ Нет доступа", show_alert=True)
            return
        
        # Извлекаем ID транзакции
        transaction_id = callback.data.replace('reject_deposit_', '')
        
        # Получаем данные пополнения
        deposit_data = db.get_pending_deposit(transaction_id)
        if not deposit_data:
            await callback.answer("Заявка не найдена", show_alert=True)
            return
        
        user_id = deposit_data.get('user_id')
        amount = deposit_data.get('amount', 0)
        
        # Удаляем из ожидающих
        db.remove_pending_deposit(transaction_id)
        
        # Обновляем сообщение в канале
        try:
            if callback.message.photo:
                await bot.edit_message_caption(
                    chat_id=callback.message.chat.id,
                    message_id=callback.message.message_id,
                    caption=callback.message.caption + f"\n\n❌ ОТКЛОНЕНО АДМИНИСТРАТОРОМ: @{callback.from_user.username}",
                    reply_markup=None
                )
            else:
                await bot.edit_message_text(
                    chat_id=callback.message.chat.id,
                    message_id=callback.message.message_id,
                    text=callback.message.text + f"\n\n❌ ОТКЛОНЕНО АДМИНИСТРАТОРОМ: @{callback.from_user.username}",
                    reply_markup=None
                )
        except Exception as e:
            print(f"Ошибка обновления сообщения: {e}")
        
        # Уведомляем пользователя
        try:
            await bot.send_message(
                chat_id=user_id,
                text=f"❌ Ваше пополнение отклонено администратором!\n\n"
                     f"💰 Сумма: {amount:.2f}₽\n"
                     f"🆔 ID транзакции: {transaction_id}\n\n"
                     f"💳 Если есть вопросы, обратитесь в поддержку: {config.ADMIN_USERNAME}"
            )
        except Exception as e:
            print(f"Ошибка уведомления пользователя: {e}")
        
        await callback.answer("❌ Пополнение отклонено")
        
    except Exception as e:
        print(f"Ошибка при отклонении пополнения: {e}")
        await callback.answer("❌ Ошибка при отклонении", show_alert=True)

@dp.callback_query(F.data.startswith('confirm_order_'))
async def handle_confirm_order(callback: CallbackQuery):
    """Подтвердить заказ администратором"""
    try:
        # Проверяем права администратора
        if callback.from_user.id not in config.ADMIN_IDS:
            await callback.answer("⛔ Нет доступа", show_alert=True)
            return
        
        # Извлекаем ID заказа
        order_id = callback.data.replace('confirm_order_', '')
        
        # Получаем данные заказа
        order_data = db.get_pending_order(order_id)
        if not order_data:
            await callback.answer("Заказ не найден", show_alert=True)
            return
        
        user_id = order_data.get('user_id')
        total_amount = order_data.get('total', 0)
        username = callback.from_user.username or callback.from_user.first_name
        
        # Удаляем из ожидающих
        db.remove_pending_order(order_id)
        
        # Обновляем сообщение в канале
        try:
            if callback.message.photo:
                # Для сообщений с фото
                new_caption = callback.message.caption + f"\n\n✅ ПОДТВЕРЖДЕНО АДМИНИСТРАТОРОМ: @{username}"
                await bot.edit_message_caption(
                    chat_id=callback.message.chat.id,
                    message_id=callback.message.message_id,
                    caption=new_caption,
                    reply_markup=None
                )
            else:
                # Для текстовых сообщений
                new_text = callback.message.text + f"\n\n✅ ПОДТВЕРЖДЕНО АДМИНИСТРАТОРОМ: @{username}"
                await bot.edit_message_text(
                    chat_id=callback.message.chat.id,
                    message_id=callback.message.message_id,
                    text=new_text,
                    reply_markup=None
                )
        except Exception as e:
            print(f"Ошибка обновления сообщения: {e}")
        
        # Уведомляем пользователя
        try:
            user_message = f"""
✅ Ваш заказ подтвержден администратором!

🆔 Номер заказа: {order_id}
💰 Сумма: {total_amount:.2f}₽

📦 Товары будут отправлены вам в ближайшее время.
"""
            
            await bot.send_message(
                chat_id=user_id,
                text=user_message
            )
            print(f"✅ Заказ {order_id} подтвержден для пользователя {user_id}")
        except Exception as e:
            print(f"Ошибка уведомления пользователя: {e}")
            await callback.answer("Пользователь не получил уведомление", show_alert=True)
        
        await callback.answer("✅ Заказ подтвержден")
        
    except Exception as e:
        print(f"Ошибка при подтверждении заказа: {e}")
        await callback.answer("❌ Ошибка при подтверждении", show_alert=True)

@dp.callback_query(F.data.startswith('reject_order_'))
async def handle_reject_order(callback: CallbackQuery):
    """Отклонить заказ администратором"""
    try:
        # Проверяем права администратора
        if callback.from_user.id not in config.ADMIN_IDS:
            await callback.answer("⛔ Нет доступа", show_alert=True)
            return
        
        # Извлекаем ID заказа
        order_id = callback.data.replace('reject_order_', '')
        
        # Получаем данные заказа
        order_data = db.get_pending_order(order_id)
        if not order_data:
            await callback.answer("Заказ не найден", show_alert=True)
            return
        
        user_id = order_data.get('user_id')
        total_amount = order_data.get('total', 0)
        payment_method = order_data.get('payment_method', 'Неизвестно')
        
        # Если оплата была с баланса - возвращаем средства
        if 'Баланс' in payment_method:
            db.add_balance(
                user_id=user_id,
                amount=total_amount,
                description=f"Возврат средств по отмененному заказу {order_id}"
            )
        
        # Удаляем из ожидающих
        db.remove_pending_order(order_id)
        
        # Обновляем сообщение в канале
        try:
            if callback.message.photo:
                await bot.edit_message_caption(
                    chat_id=callback.message.chat.id,
                    message_id=callback.message.message_id,
                    caption=callback.message.caption + f"\n\n❌ ОТКЛОНЕНО АДМИНИСТРАТОРОМ: @{callback.from_user.username}",
                    reply_markup=None
                )
            else:
                await bot.edit_message_text(
                    chat_id=callback.message.chat.id,
                    message_id=callback.message.message_id,
                    text=callback.message.text + f"\n\n❌ ОТКЛОНЕНО АДМИНИСТРАТОРОМ: @{callback.from_user.username}",
                    reply_markup=None
                )
        except Exception as e:
            print(f"Ошибка обновления сообщения: {e}")
        
        # Уведомляем пользователя
        try:
            message_text = f"❌ Ваш заказ отклонен администратором!\n\n🆔 Номер заказа: {order_id}"
            
            if 'Баланс' in payment_method:
                message_text += f"\n💰 Средства возвращены на баланс"
                message_text += f"\n💳 Текущий баланс: {db.get_user_balance(user_id):.2f}₽"
            
            message_text += f"\n\n💳 Если есть вопросы, обратитесь в поддержку: {config.ADMIN_USERNAME}"
            
            await bot.send_message(chat_id=user_id, text=message_text)
        except Exception as e:
            print(f"Ошибка уведомления пользователя: {e}")
        
        await callback.answer("❌ Заказ отклонен")
        
    except Exception as e:
        print(f"Ошибка при отклонении заказа: {e}")
        await callback.answer("❌ Ошибка при отклонении", show_alert=True)

# ==================== АДМИН-ПАНЕЛЬ ====================

@dp.callback_query(F.data == 'admin_panel')
async def handle_admin_panel(callback: CallbackQuery):
    """Показать админ-панель"""
    try:
        # Проверяем права администратора
        if callback.from_user.id not in config.ADMIN_IDS:
            await callback.answer("⛔ Нет доступа", show_alert=True)
            return
        
        # Статистика для админ-панели
        pending_deposits = len(db.pending_deposits)
        pending_orders = len(db.pending_orders)
        
        admin_text = f"""
👨‍💼 Админ-панель

📊 Быстрая статистика:
• ⏳ Ожидающих пополнений: {pending_deposits}
• 🛒 Ожидающих заказов: {pending_orders}
• 👥 Пользователей: {len(db.users)}
• 📦 Товаров: {len(db.products)}

Выберите раздел для управления:
"""
        
        await callback.message.edit_text(
            text=admin_text,
            reply_markup=admin_panel_kb()
        )
        
    except Exception as e:
        print(f"Ошибка при открытии админ-панели: {e}")
        await callback.answer("Ошибка", show_alert=True)
    
    await callback.answer()

@dp.callback_query(F.data == 'admin_pending')
async def handle_admin_pending(callback: CallbackQuery):
    """Показать ожидающие заявки"""
    try:
        # Проверяем права администратора
        if callback.from_user.id not in config.ADMIN_IDS:
            await callback.answer("⛔ Нет доступа", show_alert=True)
            return
        
        pending_deposits = db.pending_deposits
        pending_orders = db.pending_orders
        
        if not pending_deposits and not pending_orders:
            text = "📭 Нет ожидающих заявок"
        else:
            text = "⏳ Ожидающие заявки:\n\n"
            
            if pending_deposits:
                text += f"💰 Пополнения ({len(pending_deposits)}):\n"
                for i, (deposit_id, deposit_data) in enumerate(pending_deposits.items(), 1):
                    text += f"{i}. 🆔 {deposit_id}\n"
                    text += f"   👤 @{deposit_data.get('username', 'N/A')} ({deposit_data.get('user_id')})\n"
                    text += f"   💰 {deposit_data.get('amount', 0)}₽\n"
                    text += f"   💳 {deposit_data.get('method', 'unknown')}\n\n"
            
            if pending_orders:
                text += f"🛒 Заказы ({len(pending_orders)}):\n"
                for i, (order_id, order_data) in enumerate(pending_orders.items(), 1):
                    text += f"{i}. 🆔 {order_id}\n"
                    text += f"   👤 @{order_data.get('username', 'N/A')} ({order_data.get('user_id')})\n"
                    text += f"   💰 {order_data.get('total', 0)}₽\n"
                    text += f"   💳 {order_data.get('payment_method', 'unknown')}\n\n"
        
        # Создаем клавиатуру
        builder = InlineKeyboardBuilder()
        builder.row(
            InlineKeyboardButton(text='🔄 Обновить', callback_data='admin_pending'),
            InlineKeyboardButton(text='🔙 Назад', callback_data='admin_panel')
        )
        
        await callback.message.edit_text(
            text=text,
            reply_markup=builder.as_markup()
        )
        
    except Exception as e:
        print(f"Ошибка при показе ожидающих заявок: {e}")
        await callback.answer("Ошибка", show_alert=True)
    
    await callback.answer()

@dp.callback_query(F.data == 'admin_users')
async def handle_admin_users(callback: CallbackQuery):
    """Показать пользователей"""
    try:
        # Проверяем права администратора
        if callback.from_user.id not in config.ADMIN_IDS:
            await callback.answer("⛔ Нет доступа", show_alert=True)
            return
        
        users = db.users
        if not users:
            text = "📭 Пользователей пока нет"
        else:
            text = "👥 Пользователи:\n\n"
            
            # Сортируем по балансу
            sorted_users = sorted(
                users.items(),
                key=lambda x: x[1].get('balance', 0),
                reverse=True
            )
            
            for i, (user_id, user_data) in enumerate(sorted_users[:10], 1):  # Первые 10
                balance = user_data.get('balance', 0)
                total_spent = user_data.get('total_spent', 0)
                total_orders = user_data.get('total_orders', 0)
                
                text += f"{i}. 🆔 {user_id}\n"
                text += f"   💰 Баланс: {balance:.2f}₽\n"
                text += f"   💸 Потрачено: {total_spent:.2f}₽\n"
                text += f"   📦 Заказов: {total_orders}\n\n"
        
        # Создаем клавиатуру
        builder = InlineKeyboardBuilder()
        builder.row(
            InlineKeyboardButton(text='🔙 Назад', callback_data='admin_panel')
        )
        
        await callback.message.edit_text(
            text=text,
            reply_markup=builder.as_markup()
        )
        
    except Exception as e:
        print(f"Ошибка при показе пользователей: {e}")
        await callback.answer("Ошибка", show_alert=True)
    
    await callback.answer()

@dp.callback_query(F.data == 'admin_stats')
async def handle_admin_stats(callback: CallbackQuery):
    """Показать статистику"""
    try:
        # Проверяем права администратора
        if callback.from_user.id not in config.ADMIN_IDS:
            await callback.answer("⛔ Нет доступа", show_alert=True)
            return
        
        # Собираем статистику
        categories_count = len(db.get_categories())
        products_count = len(db.products)
        users_count = len(db.users)
        
        # Статистика по транзакциям
        deposits = [t for t in db.transactions if t['type'] == 'deposit']
        purchases = [t for t in db.transactions if t['type'] == 'purchase']
        
        total_deposits = sum(t['amount'] for t in deposits)
        total_purchases = sum(abs(t['amount']) for t in purchases)
        
        # Статистика по пользователям
        total_balance = sum(user.get('balance', 0) for user in db.users.values())
        total_orders = sum(user.get('total_orders', 0) for user in db.users.values())
        
        # Формируем сообщение
        stats_text = f"""
📊 СТАТИСТИКА БОТА

📈 Общая статистика:
• 📁 Категорий: {categories_count}
• 📦 Товаров: {products_count}
• 👥 Пользователей: {users_count}
• ⏳ Ожидающих заявок: {len(db.pending_deposits) + len(db.pending_orders)}

💰 Финансовая статистика:
• 💳 Пополнений: {len(deposits)} на {total_deposits:.2f}₽
• 🛒 Покупок: {len(purchases)} на {total_purchases:.2f}₽
• 💰 Общий баланс пользователей: {total_balance:.2f}₽
• 📦 Всего заказов: {total_orders}

⚙️ Настройки:
• 🎁 Скидка: {db.settings.get('balance_discount', 10)}%
• 💸 Мин. пополнение: {db.settings.get('min_deposit', 100):.2f}₽
• 💰 Макс. пополнение: {db.settings.get('max_deposit', 50000):.2f}₽
"""
        
        # Создаем клавиатуру
        builder = InlineKeyboardBuilder()
        builder.row(
            InlineKeyboardButton(text='🔙 Назад', callback_data='admin_panel')
        )
        
        await callback.message.edit_text(
            text=stats_text,
            reply_markup=builder.as_markup()
        )
        
    except Exception as e:
        print(f"Ошибка при показе статистики: {e}")
        await callback.answer("Ошибка", show_alert=True)
    
    await callback.answer()

@dp.callback_query(F.data == 'admin_products')
async def handle_admin_products(callback: CallbackQuery, state: FSMContext):
    """Управление товарами"""
    try:
        # Проверяем права администратора
        if callback.from_user.id not in config.ADMIN_IDS:
            await callback.answer("⛔ Нет доступа", show_alert=True)
            return
        
        await state.clear()
        
        # Создаем клавиатуру управления товарами
        builder = InlineKeyboardBuilder()
        builder.row(
            InlineKeyboardButton(text='➕ Добавить товар', callback_data='admin_add_product'),
            InlineKeyboardButton(text='🗑️ Удалить товар', callback_data='admin_delete_product')
        )
        builder.row(
            InlineKeyboardButton(text='📋 Список товаров', callback_data='admin_list_products')
        )
        builder.row(
            InlineKeyboardButton(text='🔙 Назад', callback_data='admin_panel')
        )
        
        await callback.message.edit_text(
            text="📦 Управление товарами\n\nВыберите действие:",
            reply_markup=builder.as_markup()
        )
        
    except Exception as e:
        print(f"Ошибка при управлении товарами: {e}")
        await callback.answer("Ошибка", show_alert=True)
    
    await callback.answer()

@dp.callback_query(F.data == 'admin_categories')
async def handle_admin_categories(callback: CallbackQuery):
    """Управление категориями"""
    try:
        # Проверяем права администратора
        if callback.from_user.id not in config.ADMIN_IDS:
            await callback.answer("⛔ Нет доступа", show_alert=True)
            return
        
        # Создаем клавиатуру управления категориями
        builder = InlineKeyboardBuilder()
        builder.row(
            InlineKeyboardButton(text='➕ Добавить категорию', callback_data='admin_add_category'),
            InlineKeyboardButton(text='📋 Список категорий', callback_data='admin_list_categories')
        )
        builder.row(
            InlineKeyboardButton(text='🔙 Назад', callback_data='admin_panel')
        )
        
        await callback.message.edit_text(
            text="📁 Управление категориями\n\nВыберите действие:",
            reply_markup=builder.as_markup()
        )
        
    except Exception as e:
        print(f"Ошибка при управлении категориями: {e}")
        await callback.answer("Ошибка", show_alert=True)
    
    await callback.answer()

# ==================== ДОПОЛНИТЕЛЬНЫЕ ОБРАБОТЧИКИ ====================

@dp.callback_query(F.data == 'cancel')
async def handle_cancel(callback: CallbackQuery, state: FSMContext):
    """Отменить текущую операцию"""
    try:
        # Очищаем состояние FSM
        await state.clear()
        
        # Возвращаем в главное меню
        await callback.message.edit_text(
            text="❌ Операция отменена",
            reply_markup=main_menu_kb(callback.from_user.id)
        )
        
    except Exception as e:
        print(f"Ошибка при отмене операции: {e}")
        await callback.answer("Ошибка при отмене", show_alert=True)
    
    await callback.answer()

@dp.message(F.text & ~F.command)
async def handle_unknown_text(message: Message, state: FSMContext):
    """Обработать неизвестные текстовые сообщения"""
    current_state = await state.get_state()
    
    if not current_state:
        await message.answer(
            text="👋 Для навигации используйте кнопки меню:",
            reply_markup=main_menu_kb(message.from_user.id)
        )

# ==================== АДМИН КОМАНДЫ ====================

@dp.message(Command("addproduct"))
async def handle_add_product_command(message: Message, state: FSMContext):
    """Команда добавления товара"""
    try:
        # Проверяем права администратора
        if message.from_user.id not in config.ADMIN_IDS:
            await message.answer("⛔ У вас нет прав администратора")
            return
        
        categories = db.get_categories()
        if not categories:
            await message.answer(
                "❌ Нет доступных категорий.\n"
                "Сначала создайте категорию командой /addcategory"
            )
            return
        
        await state.set_state(AddProductStates.waiting_for_category)
        
        # Создаем клавиатуру с категориями
        builder = InlineKeyboardBuilder()
        for category in categories:
            builder.row(
                InlineKeyboardButton(
                    text=category["name"],
                    callback_data=f"admin_add_product_cat_{category['id']}"
                )
            )
        builder.row(InlineKeyboardButton(text='❌ Отмена', callback_data='cancel'))
        
        await message.answer(
            text="➕ Добавление нового товара\n\n"
                 "Выберите категорию для товара:",
            reply_markup=builder.as_markup()
        )
        
    except Exception as e:
        print(f"Ошибка при запуске добавления товара: {e}")
        await message.answer("❌ Произошла ошибка")
        await state.clear()

@dp.message(Command("addcategory"))
async def handle_add_category_command(message: Message):
    """Команда добавления категории"""
    try:
        # Проверяем права администратора
        if message.from_user.id not in config.ADMIN_IDS:
            await message.answer("⛔ У вас нет прав администратора")
            return
        
        # Извлекаем название категории из команды
        command_parts = message.text.split(maxsplit=1)
        if len(command_parts) < 2:
            await message.answer(
                "❌ Не указано название категории.\n\n"
                "Использование:\n"
                "/addcategory <название категории>\n\n"
                "Пример:\n"
                "/addcategory 💻 Цифровые услуги"
            )
            return
        
        category_name = command_parts[1].strip()
        
        # Валидация названия
        if len(category_name) < 2:
            await message.answer("❌ Название категории слишком короткое")
            return
        
        if len(category_name) > 50:
            await message.answer("❌ Название категории слишком длинное")
            return
        
        # Проверяем, не существует ли уже категория с таким названием
        existing_categories = db.get_categories()
        for cat in existing_categories:
            if cat['name'].lower() == category_name.lower():
                await message.answer(
                    f"❌ Категория с названием '{category_name}' уже существует"
                )
                return
        
        # Добавляем категорию
        category_id = db.add_category(category_name)
        
        await message.answer(
            text=f"✅ Категория добавлена!\n\n"
                 f"📁 Название: {category_name}\n"
                 f"🆔 ID: {category_id}",
            reply_markup=main_menu_kb(message.from_user.id)
        )
        
        print(f"✅ Добавлена новая категория: {category_name} (ID: {category_id})")
        
    except Exception as e:
        print(f"Ошибка при добавлении категории: {e}")
        await message.answer("❌ Ошибка при добавлении категории")

# ==================== ЗАПУСК БОТА ====================

async def main():
    """
    Основная функция запуска бота
    """
    # Выводим информацию о запуске
    startup_info = f"""
{'=' * 50}
🤖 БОТ ЗАПУЩЕН
{'=' * 50}

📊 Загруженные данные:
• 📁 Категорий: {len(db.categories)}
• 📦 Товаров: {len(db.products)}
• 👥 Пользователей: {len(db.users)}
• 💳 Транзакций: {len(db.transactions)}
• ⏳ Ожидающих заявок: {len(db.pending_deposits) + len(db.pending_orders)}

⚙️ Конфигурация:
• 👨‍💼 Администраторы: {config.ADMIN_IDS}
• 🎁 Скидка: {db.settings.get('balance_discount', 10)}%
• 💰 Лимиты пополнения: {db.settings.get('min_deposit', 100)}₽ - {db.settings.get('max_deposit', 50000)}₽
• 📊 Каналы: Оплата - {config.PAYMENT_CHANNEL_ID}, Заказы - {config.ORDER_CHANNEL_ID}

{'=' * 50}
✅ Система подтверждения заказов АКТИВИРОВАНА
✅ Бот готов к работе!
{'=' * 50}
"""
    print(startup_info)
    
    try:
        # Запускаем polling
        await dp.start_polling(
            bot,
            skip_updates=True
        )
        
    except KeyboardInterrupt:
        print("\n\n🛑 Бот остановлен пользователем")
    except Exception as e:
        print(f"❌ Критическая ошибка при запуске бота: {e}")
    finally:
        # Закрываем сессию бота
        await bot.session.close()
        print("✅ Сессия бота закрыта")

if __name__ == "__main__":
    # Запускаем бота
    asyncio.run(main())