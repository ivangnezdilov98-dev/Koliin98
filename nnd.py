import asyncio
import json
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Any

from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, 
    PhotoSize, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
)
from aiogram.filters import Command, CommandStart, CommandObject
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
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
    ADMIN_USERNAME = "@koliin98"  # Юзернейм продавца
    
    # ID каналов для заявок
    PAYMENT_CHANNEL_ID = -1001862240317  # Канал для заявок на пополнение
    ORDER_CHANNEL_ID = -1002893927706     # Канал для заявок на покупку
    SUPPORT_CHANNEL_ID = -1003085929502   # Канал для техподдержки
    
    # Скидки и настройки баланса
    BALANCE_DISCOUNT_PERCENT = 5  # Скидка при оплате с баланса (в процентах)
    MIN_DEPOSIT_AMOUNT = 100      # Минимальная сумма пополнения
    MAX_DEPOSIT_AMOUNT = 50000    # Максимальная сумма пополнения
    
    # Время на подтверждение скриншотом (секунды)
    SCREENSHOT_TIMEOUT = 600  # 10 минут = 600 секунд
    
    # Реквизиты для оплаты
    PAYMENT_DETAILS = {
        "sber": {
            "name": "СБП (Озон Банк)",
            "number": "+79225739192",
            "owner": "Иван Г.",
            "instruction": "Перевод по номеру телефона через СБП"
        },
        "yoomoney": {
            "name": "ЮMoney",
            "number": "4100111234567890",
            "owner": "Иван Г.",
            "instruction": "Перевод по номеру кошелька"
        },
        "crypto": {
            "name": "Криптовалюта",
            "address": "0x742d35Cc6634C0532925a3b844Bc9e0aC2F8a5c1",
            "instruction": "Перевод на крипто-адрес"
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
    waiting_for_quantity = State()
    waiting_for_description = State()

class AddCategoryStates(StatesGroup):
    waiting_for_category_name = State()

class DeleteProductStates(StatesGroup):
    waiting_for_product_id = State()

class DeleteCategoryStates(StatesGroup):
    waiting_for_category_id = State()

class EditProductStates(StatesGroup):
    waiting_for_product_id = State()
    waiting_for_edit_field = State()
    waiting_for_edit_value = State()

class EditCategoryStates(StatesGroup):
    waiting_for_category_id = State()
    waiting_for_new_name = State()

class DepositStates(StatesGroup):
    waiting_for_amount = State()
    waiting_for_payment_method = State()

class AdminConfigStates(StatesGroup):
    waiting_for_discount = State()
    waiting_for_min_deposit = State()
    waiting_for_max_deposit = State()

class PaymentConfirmationStates(StatesGroup):
    waiting_for_screenshot = State()
    waiting_for_comment = State()

class AdminRejectStates(StatesGroup):
    waiting_for_reject_reason = State()

# НОВЫЕ СОСТОЯНИЯ ДЛЯ ФИЛЬТРОВ
class FilterStates(StatesGroup):
    waiting_for_category_for_filter = State()
    waiting_for_filter_name = State()
    waiting_for_filter_id = State()
    waiting_for_new_filter_name = State()

class AssignFilterStates(StatesGroup):
    waiting_for_product_id = State()
    waiting_for_filter_selection = State()

# ==================== КЛАСС ДЛЯ ТАЙМЕРА ====================
class PaymentTimer:
    """Таймер для подтверждения платежей скриншотом"""
    
    def __init__(self):
        self.timers: Dict[str, asyncio.Task] = {}
    
    async def start_timer(self, payment_id: str, user_id: int, timeout_seconds: int = None):
        """Запустить таймер подтверждения"""
        if timeout_seconds is None:
            timeout_seconds = config.SCREENSHOT_TIMEOUT
        
        task = asyncio.create_task(
            self._payment_timeout(payment_id, user_id, timeout_seconds)
        )
        self.timers[payment_id] = task
    
    async def _payment_timeout(self, payment_id: str, user_id: int, timeout_seconds: int):
        """Таймаут подтверждения платежа"""
        await asyncio.sleep(timeout_seconds)
        
        payment = db.get_pending_payment(payment_id)
        if payment and payment['status'] == 'pending_screenshot':
            # Уведомляем пользователя
            try:
                await bot.send_message(
                    user_id,
                    f"⏰ Время подтверждения платежа истекло!\n\n"
                    f"🆔 ID платежа: {payment_id}\n"
                    f"💰 Сумма: {payment.get('amount', 0)}₽\n\n"
                    f"Платеж отменен. Если вы уже оплатили, "
                    f"свяжитесь с поддержкой: @{config.ADMIN_USERNAME.lstrip('@')}",
                    reply_markup=main_menu_reply_kb() if user_id not in config.ADMIN_IDS else admin_panel_reply_kb()
                )
            except Exception as e:
                print(f"Ошибка уведомления пользователя: {e}")
            
            # Удаляем из ожидания
            if payment_id in db.pending_payments:
                del db.pending_payments[payment_id]
                db.save_users_data()
            
            # Уведомляем админа
            try:
                admin_text = f"""
⏰ Таймаут подтверждения платежа

🆔 ID: {payment_id}
👤 User: {payment.get('username', 'N/A')} ({user_id})
💰 Сумма: {payment.get('amount', 0)}₽
📋 Тип: {payment.get('type', 'unknown')}

❌ Платеж автоматически отменен
                """
                for admin_id in config.ADMIN_IDS:
                    await bot.send_message(admin_id, admin_text)
            except Exception as e:
                print(f"Ошибка уведомления админа: {e}")
        
        # Удаляем таймер
        if payment_id in self.timers:
            del self.timers[payment_id]
    
    def cancel_timer(self, payment_id: str):
        """Отменить таймер"""
        if payment_id in self.timers:
            self.timers[payment_id].cancel()
            del self.timers[payment_id]

# Создаем экземпляр таймера
payment_timer = PaymentTimer()

# ==================== БАЗА ДАННЫХ ====================

class Database:
    def __init__(self):
        self.carts: Dict[int, Dict] = {}
        self.products: List[Dict] = []
        self.categories: List[Dict] = []
        self.filters: List[Dict] = []  # Новое: фильтры/подкатегории
        self.users: Dict[int, Dict] = {}
        self.transactions: List[Dict] = []
        self.pending_payments: Dict[str, Dict] = {}  # Новое: ожидающие подтверждения
        self.settings: Dict[str, Any] = {
            "balance_discount": config.BALANCE_DISCOUNT_PERCENT,
            "min_deposit": config.MIN_DEPOSIT_AMOUNT,
            "max_deposit": config.MAX_DEPOSIT_AMOUNT
        }
        self.load_data()
    
    def load_data(self):
        """Загружаем данные из файлов"""
        try:
            # Загружаем товары, категории и фильтры
            if os.path.exists(config.DATA_FILE):
                with open(config.DATA_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.products = data.get('products', [])
                    self.categories = data.get('categories', [])
                    self.filters = data.get('filters', [])  # Новое
            else:
                self.categories = [
                    {"id": 1, "name": "💻 Цифровые услуги"},
                    {"id": 2, "name": "🎨 Дизайн"},
                    {"id": 3, "name": "📝 Контент"}
                ]
                self.filters = []  # Новое
                self.save_products_data()
            
            # Загружаем пользователей, транзакции и pending платежи
            if os.path.exists(config.USERS_FILE):
                with open(config.USERS_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    # Конвертируем ключи строки в int для users
                    users_data = data.get('users', {})
                    self.users = {int(k): v for k, v in users_data.items()}
                    self.transactions = data.get('transactions', [])
                    self.settings = data.get('settings', self.settings)
                    self.pending_payments = data.get('pending_payments', {})
        except Exception as e:
            print(f"Ошибка загрузки данных: {e}")
            self.products = []
            self.categories = []
            self.filters = []  # Новое
            self.users = {}
            self.transactions = []
            self.pending_payments = {}
    
    def save_products_data(self):
        """Сохраняем товары, категории и фильтры"""
        try:
            data = {
                "products": self.products,
                "categories": self.categories,
                "filters": self.filters  # Новое
            }
            with open(config.DATA_FILE, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"Ошибка сохранения товаров: {e}")
    
    def save_users_data(self):
        """Сохраняем пользователей, транзакции и pending платежи"""
        try:
            data = {
                "users": self.users,
                "transactions": self.transactions,
                "settings": self.settings,
                "pending_payments": self.pending_payments
            }
            with open(config.USERS_FILE, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"Ошибка сохранения пользователей: {e}")
    
    # ============ МЕТОДЫ ДЛЯ РАБОТЫ С ФИЛЬТРАМИ ============
    
    def get_filters_by_category(self, category_id: int) -> List[Dict]:
        """Получить фильтры для категории"""
        return [f for f in self.filters if f.get("category_id") == category_id]
    
    def get_filter(self, filter_id: int) -> Optional[Dict]:
        """Получить фильтр по ID"""
        for filter_item in self.filters:
            if filter_item["id"] == filter_id:
                return filter_item
        return None
    
    def add_filter(self, category_id: int, name: str) -> int:
        """Добавить фильтр/тег/подкатегорию"""
        new_id = max([f["id"] for f in self.filters], default=0) + 1
        self.filters.append({
            "id": new_id,
            "category_id": category_id,
            "name": name,
            "created_at": datetime.now().isoformat()
        })
        self.save_products_data()
        return new_id
    
    def delete_filter(self, filter_id: int) -> bool:
        """Удалить фильтр"""
        initial_len = len(self.filters)
        self.filters = [f for f in self.filters if f["id"] != filter_id]
        
        # Убираем этот фильтр у всех товаров
        for product in self.products:
            if "filter_ids" in product and filter_id in product["filter_ids"]:
                product["filter_ids"].remove(filter_id)
        
        self.save_products_data()
        return len(self.filters) < initial_len
    
    def update_filter(self, filter_id: int, new_name: str) -> bool:
        """Обновить название фильтра"""
        for i, filter_item in enumerate(self.filters):
            if filter_item["id"] == filter_id:
                self.filters[i]["name"] = new_name
                self.save_products_data()
                return True
        return False
    
    def assign_filter_to_product(self, product_id: int, filter_id: int) -> bool:
        """Назначить фильтр товару"""
        product = self.get_product(product_id)
        if not product:
            return False
        
        filter_item = self.get_filter(filter_id)
        if not filter_item:
            return False
        
        if product.get("category_id") != filter_item.get("category_id"):
            return False
        
        if "filter_ids" not in product:
            product["filter_ids"] = []
        
        if filter_id not in product["filter_ids"]:
            product["filter_ids"].append(filter_id)
            self.save_products_data()
            return True
        return False
    
    def remove_filter_from_product(self, product_id: int, filter_id: int) -> bool:
        """Убрать фильтр у товара"""
        product = self.get_product(product_id)
        if not product:
            return False
        
        if "filter_ids" in product and filter_id in product["filter_ids"]:
            product["filter_ids"].remove(filter_id)
            self.save_products_data()
            return True
        return False
    
    def get_products_by_filter(self, category_id: int, filter_id: int) -> List[Dict]:
        """Получить товары по фильтру"""
        return [
            p for p in self.products 
            if p.get("category_id") == category_id 
            and "filter_ids" in p 
            and filter_id in p["filter_ids"]
        ]
    
    def get_product_filters(self, product_id: int) -> List[Dict]:
        """Получить фильтры товара"""
        product = self.get_product(product_id)
        if not product or "filter_ids" not in product:
            return []
        
        filters = []
        for filter_id in product["filter_ids"]:
            filter_item = self.get_filter(filter_id)
            if filter_item:
                filters.append(filter_item)
        
        return filters
    
    def get_available_filters_for_product(self, product_id: int) -> List[Dict]:
        """Получить доступные для товара фильтры (той же категории)"""
        product = self.get_product(product_id)
        if not product:
            return []
        
        category_filters = self.get_filters_by_category(product["category_id"])
        return category_filters
    
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
    
    def update_user(self, user_id: int, **kwargs):
        if user_id in self.users:
            self.users[user_id].update(kwargs)
            self.users[user_id]["last_activity"] = datetime.now().isoformat()
            self.save_users_data()
    
    def get_user_balance(self, user_id: int) -> float:
        user = self.get_user(user_id)
        return user.get("balance", 0.0)
    
    def add_balance(self, user_id: int, amount: float, description: str = "") -> bool:
        try:
            user = self.get_user(user_id)
            user["balance"] = user.get("balance", 0.0) + amount
            
            # Добавляем транзакцию
            transaction = {
                "id": len(self.transactions) + 1,
                "user_id": user_id,
                "type": "deposit",
                "amount": amount,
                "description": description,
                "status": "completed",
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
            
            # Добавляем транзакцию
            transaction = {
                "id": len(self.transactions) + 1,
                "user_id": user_id,
                "type": "purchase",
                "amount": -amount,
                "description": description,
                "status": "completed",
                "date": datetime.now().isoformat()
            }
            self.transactions.append(transaction)
            
            self.save_users_data()
            return True, "Оплата прошла успешно"
        except Exception as e:
            print(f"Ошибка списания баланса: {e}")
            return False, "Ошибка при списании средств"
    
    def get_user_transactions(self, user_id: int, limit: int = 10) -> List[Dict]:
        user_transactions = [t for t in self.transactions if t["user_id"] == user_id]
        return sorted(user_transactions, key=lambda x: x["date"], reverse=True)[:limit]
    
    # Работа с настройками
    def get_settings(self) -> Dict:
        return self.settings
    
    def update_settings(self, **kwargs):
        self.settings.update(kwargs)
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
    
    def delete_category(self, category_id: int) -> bool:
        initial_len = len(self.categories)
        self.categories = [cat for cat in self.categories if cat["id"] != category_id]
        self.products = [prod for prod in self.products if prod["category_id"] != category_id]
        # Удаляем фильтры этой категории
        self.filters = [f for f in self.filters if f.get("category_id") != category_id]
        self.save_products_data()
        return len(self.categories) < initial_len
    
    def update_category(self, category_id: int, new_name: str) -> bool:
        """Обновить название категории"""
        for i, category in enumerate(self.categories):
            if category["id"] == category_id:
                self.categories[i]["name"] = new_name
                self.save_products_data()
                return True
        return False
    
    def get_products_by_category(self, category_id: int) -> List[Dict]:
        return [p for p in self.products if p["category_id"] == category_id]
    
    def get_product(self, product_id: int) -> Optional[Dict]:
        for product in self.products:
            if product["id"] == product_id:
                return product
        return None
    
    def add_product(self, category_id: int, name: str, price: float, description: str = "", quantity: int = 9999, filter_ids: List[int] = None) -> int:
        new_id = max([prod["id"] for prod in self.products], default=0) + 1
        product = {
            "id": new_id,
            "category_id": category_id,
            "name": name,
            "price": price,
            "description": description,
            "quantity": quantity,
            "filter_ids": filter_ids if filter_ids else []  # Новое
        }
        self.products.append(product)
        self.save_products_data()
        return new_id
    
    def update_product(self, product_id: int, **kwargs) -> bool:
        for i, product in enumerate(self.products):
            if product["id"] == product_id:
                self.products[i].update(kwargs)
                self.save_products_data()
                return True
        return False
    
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
    
    # НОВЫЕ МЕТОДЫ ДЛЯ ПОДТВЕРЖДЕНИЯ СКРИНШОТОМ
    def add_pending_payment(self, payment_id: str, data: Dict):
        """Добавить платеж в ожидание скриншота"""
        data['created_at'] = datetime.now().isoformat()
        data['status'] = 'pending_screenshot'
        self.pending_payments[payment_id] = data
        self.save_users_data()
    
    def get_pending_payment(self, payment_id: str) -> Optional[Dict]:
        """Получить ожидающий платеж"""
        return self.pending_payments.get(payment_id)
    
    def confirm_payment(self, payment_id: str, screenshot_file_id: str, comment: str = "") -> Tuple[bool, Dict]:
        """Подтвердить платеж скриншотом"""
        if payment_id not in self.pending_payments:
            return False, {}
        
        payment = self.pending_payments[payment_id]
        payment['screenshot_file_id'] = screenshot_file_id
        payment['comment'] = comment
        payment['confirmed_at'] = datetime.now().isoformat()
        payment['status'] = 'confirmed'
        
        # Если это пополнение - добавляем баланс
        if payment.get('type') == 'deposit':
            success = self.add_balance(
                payment['user_id'], 
                payment['amount'], 
                f"Пополнение с подтверждением скриншотом | Коммент: {comment}"
            )
            if not success:
                return False, {}
        
        # Переносим в историю транзакций
        transaction = {
            "id": len(self.transactions) + 1,
            "user_id": payment.get('user_id'),
            "type": payment.get('type', 'deposit'),
            "amount": payment.get('amount', 0),
            "description": f"{payment.get('description', '')} | Коммент: {comment}",
            "status": "completed",
            "screenshot_file_id": screenshot_file_id,
            "payment_method": payment.get('method', 'unknown'),
            "date": datetime.now().isoformat()
        }
        self.transactions.append(transaction)
        
        # Сохраняем обновленный pending платеж
        self.save_users_data()
        return True, payment
    
    def get_user_pending_payment(self, user_id: int) -> Optional[Tuple[str, Dict]]:
        """Получить ожидающий платеж пользователя"""
        for payment_id, payment in self.pending_payments.items():
            if payment.get('user_id') == user_id and payment.get('status') == 'pending_screenshot':
                return payment_id, payment
        return None, None

db = Database()

# ==================== УТИЛИТЫ ====================

async def send_to_payment_channel_with_screenshot(order_data: Dict, screenshot_file_id: str = None, comment: str = ""):
    """Отправляем заявку на пополнение в канал оплаты со скриншотом"""
    text = (
        f"🔄 ЗАЯВКА НА ПОПОЛНЕНИЕ БАЛАНСА\n\n"
        f"👤 Пользователь: @{order_data.get('username', 'без username')}\n"
        f"🆔 ID: {order_data.get('user_id')}\n"
        f"💰 Сумма: {order_data.get('amount')}₽\n"
        f"💳 Способ: {order_data.get('method')}\n"
        f"📝 Комментарий: {comment or 'нет'}\n"
        f"📅 Дата: {datetime.now().strftime('%d.%m.%Y %H:%M')}\n"
        f"🆔 ID транзакции: {order_data.get('transaction_id')}\n\n"
    )
    
    if screenshot_file_id:
        text += f"✅ Пользователь подтвердил скриншотом\n"
    else:
        text += f"⏳ Ожидает скриншота от пользователя\n"
    
    text += f"Ожидает проверки и подтверждения администратором"
    
    # Создаем клавиатуру с кнопками подтверждения
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text='✅ Подтвердить', callback_data=f'confirm_deposit_{order_data.get("transaction_id")}'),
        InlineKeyboardButton(text='❌ Отклонить', callback_data=f'reject_deposit_{order_data.get("transaction_id")}')
    )
    builder.row(
        InlineKeyboardButton(text='📝 Отклонить с причиной', callback_data=f'reject_with_reason_deposit_{order_data.get("transaction_id")}')
    )
    
    try:
        if screenshot_file_id:
            # Отправляем со скриншотом
            message = await bot.send_photo(
                chat_id=config.PAYMENT_CHANNEL_ID,
                photo=screenshot_file_id,
                caption=text,
                reply_markup=builder.as_markup()
            )
        else:
            # Без скриншота
            message = await bot.send_message(
                chat_id=config.PAYMENT_CHANNEL_ID,
                text=text,
                reply_markup=builder.as_markup()
            )
        return message.message_id
    except Exception as e:
        print(f"Ошибка отправки в канал оплаты: {e}")
        return None

async def send_to_order_channel_with_screenshot(order_data: Dict, screenshot_file_id: str = None, comment: str = ""):
    """Отправляем заявку на покупку в канал заказов со скриншотом"""
    text = (
        f"🛒 НОВЫЙ ЗАКАЗ\n\n"
        f"👤 Покупатель: @{order_data.get('username', 'без username')}\n"
        f"🆔 ID: {order_data.get('user_id')}\n"
        f"💰 Сумма: {order_data.get('total')}₽\n"
        f"📝 Комментарий: {comment or 'нет'}\n"
        f"📦 Способ оплаты: {order_data.get('payment_method', 'Не указан')}\n"
        f"📅 Дата: {datetime.now().strftime('%d.%m.%Y %H:%M')}\n"
        f"🆔 ID заказа: {order_data.get('order_id')}\n\n"
        f"📋 Состав заказа:\n"
    )
    
    for item in order_data.get('items', []):
        text += f"• {item['name']} - {item['quantity']}шт. × {item['price']}₽ = {item['quantity'] * item['price']}₽\n"
    
    text += f"\n💰 ИТОГО: {order_data.get('total')}₽\n"
    
    if order_data.get('balance_used'):
        text += f"💳 Оплачено с баланса\n"
        text += f"🎁 Скидка: {db.settings.get('balance_discount', 0)}%\n"
    
    if screenshot_file_id:
        text += f"\n✅ Подтвержден скриншотом"
    else:
        text += f"\n⏳ Ожидает скриншота"
    
    # Создаем клавиатуру с кнопками подтверждения
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text='✅ Подтвердить', callback_data=f'confirm_order_{order_data.get("order_id")}'),
        InlineKeyboardButton(text='❌ Отклонить', callback_data=f'reject_order_{order_data.get("order_id")}')
    )
    builder.row(
        InlineKeyboardButton(text='📝 Отклонить с причиной', callback_data=f'reject_with_reason_order_{order_data.get("order_id")}')
    )
    
    try:
        if screenshot_file_id:
            # Отправляем со скриншотом
            message = await bot.send_photo(
                chat_id=config.ORDER_CHANNEL_ID,
                photo=screenshot_file_id,
                caption=text,
                reply_markup=builder.as_markup()
            )
        else:
            # Без скриншота
            message = await bot.send_message(
                chat_id=config.ORDER_CHANNEL_ID,
                text=text,
                reply_markup=builder.as_markup()
            )
        return message.message_id
    except Exception as e:
        print(f"Ошибка отправки в канал заказов: {e}")
        return None

async def send_to_support_channel(message: str, user_data: Dict):
    """Отправляем вопрос в канал поддержки"""
    text = (
        f"❓ ВОПРОС В ТЕХПОДДЕРЖКУ\n\n"
        f"👤 Пользователь: @{user_data.get('username', 'без username')}\n"
        f"🆔 ID: {user_data.get('user_id')}\n"
        f"📅 Дата: {datetime.now().strftime('%d.%m.%Y %H:%M')}\n\n"
        f"📝 Сообщение:\n{message}"
    )
    
    try:
        await bot.send_message(
            chat_id=config.SUPPORT_CHANNEL_ID,
            text=text
        )
    except Exception as e:
        print(f"Ошибка отправки в канал поддержки: {e}")

async def send_screenshot_instructions(user_id: int):
    """Отправить инструкцию по скриншоту"""
    instructions = """
📸 **Как сделать правильный скриншот:**

1. **После оплаты** сделайте скриншот экрана
2. **На скриншоте должно быть видно:**
   • Сумма перевода
   • Номер счета/карты получателя
   • Дата и время
   • Комментарий к переводу (если есть)
   • Статус "Успешно" или "Исполнено"

3. **Отправьте скриншот** в этот чат
4. **Добавьте комментарий** (по желанию)

⚠️ **Важно:**
• Скриншот должен быть ЧЕТКИМ
• Все данные должны быть читаемы
• У вас есть 10 минут на отправку
• Не редактируйте скриншот
    """
    
    await bot.send_message(user_id, instructions, parse_mode="Markdown")

# ==================== РЕАЛЬНЫЕ КЛАВИАТУРЫ (REPLY KEYBOARD) ====================

def main_menu_reply_kb() -> ReplyKeyboardMarkup:
    """Главное меню (реальные кнопки)"""
    builder = ReplyKeyboardBuilder()
    builder.row(
        KeyboardButton(text="🛒 Каталог"),
        KeyboardButton(text="📦 Корзина"),
    )
    builder.row(
        KeyboardButton(text="👤 Профиль"),
        KeyboardButton(text="💰 Баланс"),
    )
    builder.row(
        KeyboardButton(text="💳 Пополнить"),
        KeyboardButton(text="❓ Поддержка"),
    )
    if config.ADMIN_IDS:
        builder.row(KeyboardButton(text="👨‍💼 Админ"))
    return builder.as_markup(resize_keyboard=True)

def admin_panel_reply_kb() -> ReplyKeyboardMarkup:
    """Админ-панель (реальные кнопки)"""
    builder = ReplyKeyboardBuilder()
    builder.row(
                KeyboardButton(text="📦 Товары"),
        KeyboardButton(text="📁 Категории"),
    )
    builder.row(
        KeyboardButton(text="🏷️ Фильтры"),
        KeyboardButton(text="👥 Пользователи"),
    )
    builder.row(
        KeyboardButton(text="⚙️ Настройки"),
        KeyboardButton(text="📊 Статистика"),
    )
    builder.row(
        KeyboardButton(text="⏳ Ожидающие"),
        KeyboardButton(text="🔙 Главное меню"),
    )
    return builder.as_markup(resize_keyboard=True)

def profile_reply_kb() -> ReplyKeyboardMarkup:
    """Профиль (реальные кнопки)"""
    builder = ReplyKeyboardBuilder()
    builder.row(
        KeyboardButton(text="💼 История"),
        KeyboardButton(text="💳 Пополнить"),
    )
    builder.row(
        KeyboardButton(text="🛒 Мои заказы"),
        KeyboardButton(text="❓ Поддержка"),
    )
    builder.row(
        KeyboardButton(text="🔙 Главное меню"),
    )
    return builder.as_markup(resize_keyboard=True)

def categories_reply_kb() -> ReplyKeyboardMarkup:
    """Категории (реальные кнопки)"""
    builder = ReplyKeyboardBuilder()
    categories = db.get_categories()
    
    # Добавляем кнопки категорий (максимум 2 в ряд)
    for i in range(0, len(categories), 2):
        row_categories = categories[i:i+2]
        builder.row(*[KeyboardButton(text=cat["name"]) for cat in row_categories])
    
    builder.row(
        KeyboardButton(text="📦 Корзина"),
        KeyboardButton(text="🔙 Главное меню"),
    )
    return builder.as_markup(resize_keyboard=True)

def cart_reply_kb(with_balance: bool = False) -> ReplyKeyboardMarkup:
    """Корзина (реальные кнопки)"""
    builder = ReplyKeyboardBuilder()
    
    if with_balance:
        builder.row(KeyboardButton(text="💳 Оплатить балансом"))
    
    builder.row(
        KeyboardButton(text="💳 Оплатить"),
        KeyboardButton(text="🛒 Каталог"),
    )
    builder.row(
        KeyboardButton(text="🗑️ Очистить корзину"),
        KeyboardButton(text="🔙 Главное меню"),
    )
    return builder.as_markup(resize_keyboard=True)

def payment_methods_reply_kb() -> ReplyKeyboardMarkup:
    """Способы оплаты (реальные кнопки)"""
    builder = ReplyKeyboardBuilder()
    builder.row(
        KeyboardButton(text="🏦 СБП (Озон)"),
        KeyboardButton(text="💰 ЮMoney"),
    )
    builder.row(
        KeyboardButton(text="₿ Криптовалюта"),
        KeyboardButton(text="💳 Баланс"),
    )
    builder.row(
        KeyboardButton(text="🔙 Корзина"),
        KeyboardButton(text="🔙 Главное меню"),
    )
    return builder.as_markup(resize_keyboard=True)

def deposit_reply_kb() -> ReplyKeyboardMarkup:
    """Пополнение баланса (реальные кнопки)"""
    builder = ReplyKeyboardBuilder()
    builder.row(
        KeyboardButton(text="💰 500₽"),
        KeyboardButton(text="💰 1000₽"),
        KeyboardButton(text="💰 2000₽"),
    )
    builder.row(
        KeyboardButton(text="💰 5000₽"),
        KeyboardButton(text="💰 Своя сумма"),
    )
    builder.row(
        KeyboardButton(text="🔙 Профиль"),
        KeyboardButton(text="🔙 Главное меню"),
    )
    return builder.as_markup(resize_keyboard=True)

def admin_products_reply_kb() -> ReplyKeyboardMarkup:
    """Управление товарами (админ, реальные кнопки)"""
    builder = ReplyKeyboardBuilder()
    builder.row(
        KeyboardButton(text="➕ Добавить товар"),
        KeyboardButton(text="✏️ Редактировать"),
    )
    builder.row(
        KeyboardButton(text="❌ Удалить товар"),
        KeyboardButton(text="📋 Список"),
    )
    builder.row(
        KeyboardButton(text="🔙 Админ-панель"),
    )
    return builder.as_markup(resize_keyboard=True)

def admin_categories_reply_kb() -> ReplyKeyboardMarkup:
    """Управление категориями (админ, реальные кнопки)"""
    builder = ReplyKeyboardBuilder()
    builder.row(
        KeyboardButton(text="➕ Добавить категорию"),
        KeyboardButton(text="✏️ Редактировать категорию"),
    )
    builder.row(
        KeyboardButton(text="❌ Удалить категорию"),
        KeyboardButton(text="📋 Список категорий"),
    )
    builder.row(
        KeyboardButton(text="🔙 Админ-панель"),
    )
    return builder.as_markup(resize_keyboard=True)

def admin_filters_reply_kb() -> ReplyKeyboardMarkup:
    """Управление фильтрами (админ, реальные кнопки)"""
    builder = ReplyKeyboardBuilder()
    builder.row(
        KeyboardButton(text="➕ Добавить фильтр"),
        KeyboardButton(text="📋 Список фильтров"),
    )
    builder.row(
        KeyboardButton(text="✏️ Редактировать фильтр"),
        KeyboardButton(text="❌ Удалить фильтр"),
    )
    builder.row(
        KeyboardButton(text="🏷️ Назначить фильтр товару"),
        KeyboardButton(text="🗑️ Убрать фильтр с товара"),
    )
    builder.row(
        KeyboardButton(text="🔙 Админ-панель"),
    )
    return builder.as_markup(resize_keyboard=True)

def admin_settings_reply_kb() -> ReplyKeyboardMarkup:
    """Настройки бота (админ, реальные кнопки)"""
    builder = ReplyKeyboardBuilder()
    builder.row(
        KeyboardButton(text="🎁 Изменить скидку"),
        KeyboardButton(text="💰 Лимиты пополнения"),
    )
    builder.row(
        KeyboardButton(text="📊 Настройки"),
        KeyboardButton(text="🔄 Обновить"),
    )
    builder.row(
        KeyboardButton(text="🔙 Админ-панель"),
    )
    return builder.as_markup(resize_keyboard=True)

def cancel_reply_kb() -> ReplyKeyboardMarkup:
    """Клавиатура отмены (реальные кнопки)"""
    builder = ReplyKeyboardBuilder()
    builder.row(KeyboardButton(text="❌ Отмена"))
    return builder.as_markup(resize_keyboard=True)

def screenshot_reply_kb() -> ReplyKeyboardMarkup:
    """Подтверждение скриншота (реальные кнопки)"""
    builder = ReplyKeyboardBuilder()
    builder.row(
        KeyboardButton(text="📸 Отправить скриншот"),
        KeyboardButton(text="❌ Отменить"),
    )
    builder.row(KeyboardButton(text="📝 Инструкция"))
    return builder.as_markup(resize_keyboard=True)

# ==================== ИНЛАЙН КЛАВИАТУРЫ (INLINE KEYBOARD) ====================

def main_menu_inline_kb() -> InlineKeyboardMarkup:
    """Главное меню (инлайн кнопки)"""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text='🛒 Посмотреть услуги', callback_data='view_categories'),
        InlineKeyboardButton(text='📦 Моя корзина', callback_data='view_cart'),
    )
    builder.row(
        InlineKeyboardButton(text='👤 Мой профиль', callback_data='my_profile'),
        InlineKeyboardButton(text='💳 Пополнить баланс', callback_data='deposit'),
    )
    builder.row(
        InlineKeyboardButton(text='👨‍💼 Админ-панель', callback_data='admin_panel'),
    )
    return builder.as_markup()

def profile_inline_kb() -> InlineKeyboardMarkup:
    """Меню профиля (инлайн кнопки)"""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text='💼 История операций', callback_data='transaction_history'),
        InlineKeyboardButton(text='💳 Пополнить баланс', callback_data='deposit'),
    )
    builder.row(
        InlineKeyboardButton(text='🛒 Мои заказы', callback_data='my_orders'),
        InlineKeyboardButton(text='❓ Поддержка', callback_data='support'),
    )
    builder.row(
        InlineKeyboardButton(text='🔙 Главное меню', callback_data='main_menu'),
    )
    return builder.as_markup()

def categories_inline_kb() -> InlineKeyboardMarkup:
    """Категории товаров (инлайн кнопки)"""
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

def products_inline_kb(category_id: int, filter_id: int = None) -> InlineKeyboardMarkup:
    """Товары в категории (инлайн кнопки)"""
    builder = InlineKeyboardBuilder()
    
    if filter_id:
        products = db.get_products_by_filter(category_id, filter_id)
    else:
        products = db.get_products_by_category(category_id)
    
    for product in products:
        # Получаем фильтры товара
        product_filters = db.get_product_filters(product['id'])
        filter_text = ""
        if product_filters:
            filter_names = [f['name'] for f in product_filters[:2]]  # Берем первые 2 фильтра
            filter_text = f" [{', '.join(filter_names)}]"
            if len(product_filters) > 2:
                filter_text += "..."
        
        builder.row(
            InlineKeyboardButton(
                text=f"{product['name']}{filter_text} - {product['price']}₽",
                callback_data=f"product_{product['id']}"
            )
        )
    
    builder.row(
        InlineKeyboardButton(text='🔙 Назад к фильтрам', callback_data=f'category_{category_id}'),
        InlineKeyboardButton(text='📦 Корзина', callback_data='view_cart')
    )
    return builder.as_markup()

def filters_inline_kb(category_id: int) -> InlineKeyboardMarkup:
    """Фильтры для категории (инлайн кнопки)"""
    builder = InlineKeyboardBuilder()
    filters = db.get_filters_by_category(category_id)
    products = db.get_products_by_category(category_id)
    
    # Кнопка "Все товары"
    builder.row(
        InlineKeyboardButton(
            text=f"📦 Все товары ({len(products)})",
            callback_data=f"filter_all_{category_id}"
        )
    )
    
    # Кнопки фильтров
    for filter_item in filters:
        products_count = len(db.get_products_by_filter(category_id, filter_item['id']))
        builder.row(
            InlineKeyboardButton(
                text=f"🏷️ {filter_item['name']} ({products_count})",
                callback_data=f"filter_{filter_item['id']}_{category_id}"
            )
        )
    
    builder.row(
        InlineKeyboardButton(text='🔙 Назад к категориям', callback_data='view_categories'),
        InlineKeyboardButton(text='📦 Корзина', callback_data='view_cart')
    )
    return builder.as_markup()

def product_detail_inline_kb(product_id: int, category_id: int) -> InlineKeyboardMarkup:
    """Детали товара (инлайн кнопки)"""
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

def deposit_methods_inline_kb() -> InlineKeyboardMarkup:
    """Способы пополнения (инлайн кнопки)"""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text='🏦 СБП (Озон)', callback_data='deposit_sber'),
    )
    builder.row(
        InlineKeyboardButton(text='💰 ЮMoney', callback_data='deposit_yoomoney'),
        InlineKeyboardButton(text='₿ Криптовалюта', callback_data='deposit_crypto'),
    )
    builder.row(
        InlineKeyboardButton(text='🔙 Назад', callback_data='my_profile'),
    )
    return builder.as_markup()

def cart_inline_kb(with_balance: bool = False) -> InlineKeyboardMarkup:
    """Корзина (инлайн кнопки)"""
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
        InlineKeyboardButton(text='🗑️ Очистить корзину', callback_data='clear_cart_confirm'),
        InlineKeyboardButton(text='🔙 Главное меню', callback_data='main_menu')
    )
    return builder.as_markup()

def confirm_clear_inline_kb() -> InlineKeyboardMarkup:
    """Подтверждение очистки корзины (инлайн кнопки)"""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text='✅ Да, очистить', callback_data='clear_cart'),
        InlineKeyboardButton(text='❌ Нет, отмена', callback_data='view_cart')
    )
    return builder.as_markup()

def payment_choice_inline_kb() -> InlineKeyboardMarkup:
    """Выбор способа оплаты (инлайн кнопки)"""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text='💳 С баланса бота', callback_data='pay_balance'),
        InlineKeyboardButton(text='🏦 СБП (Озон)', callback_data='pay_sber'),
    )
    builder.row(
        InlineKeyboardButton(text='💰 ЮMoney', callback_data='pay_yoomoney'),
        InlineKeyboardButton(text='₿ Криптовалюта', callback_data='pay_crypto'),
    )
    builder.row(
        InlineKeyboardButton(text='🔙 Назад', callback_data='view_cart'),
    )
    return builder.as_markup()

def support_inline_kb() -> InlineKeyboardMarkup:
    """Поддержка (инлайн кнопки)"""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text='📞 Написать в поддержку', url=f'https://t.me/{config.ADMIN_USERNAME.lstrip("@")}'),
    )
    builder.row(
        InlineKeyboardButton(text='🔙 Мой профиль', callback_data='my_profile'),
        InlineKeyboardButton(text='🏠 Главное меню', callback_data='main_menu')
    )
    return builder.as_markup()

def admin_panel_inline_kb() -> InlineKeyboardMarkup:
    """Админ-панель (инлайн кнопки)"""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text='📦 Управление товарами', callback_data='admin_products'),
        InlineKeyboardButton(text='📁 Управление категориями', callback_data='admin_categories')
    )
    builder.row(
        InlineKeyboardButton(text='🏷️ Управление фильтрами', callback_data='admin_filters'),
        InlineKeyboardButton(text='👥 Управление пользователями', callback_data='admin_users')
    )
    builder.row(
        InlineKeyboardButton(text='⚙️ Настройки бота', callback_data='admin_settings'),
        InlineKeyboardButton(text='📊 Статистика', callback_data='admin_stats')
    )
    builder.row(
        InlineKeyboardButton(text='⏳ Ожидающие платежи', callback_data='admin_pending_payments'),
        InlineKeyboardButton(text='🔙 Главное меню', callback_data='main_menu')
    )
    return builder.as_markup()

def admin_products_inline_kb() -> InlineKeyboardMarkup:
    """Управление товарами (инлайн кнопки)"""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text='➕ Добавить товар', callback_data='admin_add_product'),
        InlineKeyboardButton(text='✏️ Редактировать товар', callback_data='admin_edit_product')
    )
    builder.row(
        InlineKeyboardButton(text='❌ Удалить товар', callback_data='admin_delete_product'),
        InlineKeyboardButton(text='📋 Список товаров', callback_data='admin_list_products')
    )
    builder.row(
        InlineKeyboardButton(text='🔙 В админ-панель', callback_data='admin_panel')
    )
    return builder.as_markup()

def admin_categories_inline_kb() -> InlineKeyboardMarkup:
    """Управление категориями (инлайн кнопки)"""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text='➕ Добавить категорию', callback_data='admin_add_category'),
        InlineKeyboardButton(text='✏️ Редактировать категорию', callback_data='admin_edit_category')
    )
    builder.row(
        InlineKeyboardButton(text='❌ Удалить категорию', callback_data='admin_delete_category'),
        InlineKeyboardButton(text='📋 Список категорий', callback_data='admin_list_categories')
    )
    builder.row(
        InlineKeyboardButton(text='🔙 В админ-панель', callback_data='admin_panel')
    )
    return builder.as_markup()

def admin_filters_inline_kb() -> InlineKeyboardMarkup:
    """Управление фильтрами (инлайн кнопки)"""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text='➕ Добавить фильтр', callback_data='admin_add_filter'),
        InlineKeyboardButton(text='📋 Список фильтров', callback_data='admin_list_filters')
    )
    builder.row(
        InlineKeyboardButton(text='✏️ Редактировать фильтр', callback_data='admin_edit_filter'),
        InlineKeyboardButton(text='❌ Удалить фильтр', callback_data='admin_delete_filter')
    )
    builder.row(
        InlineKeyboardButton(text='🏷️ Назначить фильтр товару', callback_data='admin_assign_filter'),
        InlineKeyboardButton(text='🗑️ Убрать фильтр с товара', callback_data='admin_remove_filter')
    )
    builder.row(
        InlineKeyboardButton(text='🔙 В админ-панель', callback_data='admin_panel')
    )
    return builder.as_markup()

def admin_settings_inline_kb() -> InlineKeyboardMarkup:
    """Настройки бота (инлайн кнопки)"""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text='🎁 Изменить скидку', callback_data='admin_change_discount'),
        InlineKeyboardButton(text='💰 Лимиты пополнения', callback_data='admin_change_limits')
    )
    builder.row(
        InlineKeyboardButton(text='📊 Просмотр настроек', callback_data='admin_view_settings'),
        InlineKeyboardButton(text='🔙 В админ-панель', callback_data='admin_panel')
    )
    return builder.as_markup()

def categories_list_inline_kb(action: str = 'select') -> InlineKeyboardMarkup:
    """Список категорий для выбора (инлайн кнопки)"""
    builder = InlineKeyboardBuilder()
    categories = db.get_categories()
    
    for category in categories:
        builder.row(
            InlineKeyboardButton(
                text=category["name"], 
                callback_data=f"admin_{action}_cat_{category['id']}"
            )
        )
    
    builder.row(
        InlineKeyboardButton(text='🔙 Назад', callback_data='admin_products')
    )
    return builder.as_markup()

def cancel_inline_kb() -> InlineKeyboardMarkup:
    """Отмена (инлайн кнопки)"""
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text='❌ Отмена', callback_data='cancel'))
    return builder.as_markup()

def screenshot_confirmation_inline_kb() -> InlineKeyboardMarkup:
    """Подтверждение скриншота (инлайн кнопки)"""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text='📸 Отправить скриншот', callback_data='send_screenshot_help'),
        InlineKeyboardButton(text='❌ Отменить платеж', callback_data='cancel_screenshot')
    )
    return builder.as_markup()

def after_screenshot_inline_kb() -> InlineKeyboardMarkup:
    """После отправки скриншота (инлайн кнопки)"""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text='📝 Добавить комментарий', callback_data='add_comment'),
        InlineKeyboardButton(text='➡️ Пропустить комментарий', callback_data='skip_comment')
    )
    return builder.as_markup()

# ==================== УНИВЕРСАЛЬНЫЕ ФУНКЦИИ ====================

async def send_dual_keyboard_message(
    message: Message, 
    text: str, 
    reply_kb: Optional[ReplyKeyboardMarkup] = None,
    inline_kb: Optional[InlineKeyboardMarkup] = None
):
    """Отправка сообщения с двумя типами клавиатур"""
    if reply_kb:
        await message.answer(text, reply_markup=reply_kb)
        if inline_kb:
            await message.answer("📱 Дополнительные опции:", reply_markup=inline_kb)
    elif inline_kb:
        await message.answer(text, reply_markup=inline_kb)
    else:
        await message.answer(text)

async def edit_dual_keyboard_message(
    callback: CallbackQuery,
    text: str,
    reply_kb: Optional[ReplyKeyboardMarkup] = None,
    inline_kb: Optional[InlineKeyboardMarkup] = None
):
    """Редактирование сообщения с двумя типами клавиатур"""
    if inline_kb:
        await callback.message.edit_text(text, reply_markup=inline_kb)
        if reply_kb:
            # Нельзя редактировать reply keyboard, нужно отправить новое сообщение
            await callback.message.answer("📱 Быстрые действия:", reply_markup=reply_kb)
    else:
        await callback.message.edit_text(text)

# ==================== ОБРАБОТЧИКИ КОМАНД ====================

@dp.message(CommandStart())
async def cmd_start(message: Message):
    """Команда /start"""
    # Регистрируем пользователя
    db.get_user(message.from_user.id)
    
    welcome_text = (
        "👋 Добро пожаловать в магазин виртуальных услуг!\n\n"
        "✨ Новые возможности:\n"
        "• 💳 Личный баланс - пополняйте и оплачивайте с него\n"
        "• 🎁 Скидка {discount}% при оплате с баланса\n"
        "• 📸 Подтверждение платежей скриншотом\n"
        "• ⏰ Автоматическая отмена через 10 минут\n"
        "• 🏷️ Фильтры для удобного поиска товаров\n\n"
        "Используйте кнопки ниже для навигации:".format(
            discount=db.settings.get("balance_discount", 10)
        )
    )
    
    if message.from_user.id in config.ADMIN_IDS:
        await send_dual_keyboard_message(
            message, 
            welcome_text + "\n\n👨‍💼 Вы администратор!",
            main_menu_reply_kb(),
            main_menu_inline_kb()
        )
    else:
        await send_dual_keyboard_message(
            message, 
            welcome_text,
            main_menu_reply_kb(),
            main_menu_inline_kb()
        )

@dp.message(Command("profile"))
async def cmd_profile(message: Message):
    """Команда /profile"""
    user = db.get_user(message.from_user.id)
    balance = user.get("balance", 0.0)
    total_spent = user.get("total_spent", 0.0)
    total_orders = user.get("total_orders", 0)
    
    text = (
        f"👤 Ваш профиль\n\n"
        f"💰 Баланс: {balance}₽\n"
        f"💳 Всего потрачено: {total_spent}₽\n"
        f"📦 Заказов: {total_orders} шт.\n"
        f"🎁 Скидка при оплате с баланса: {db.settings.get('balance_discount', 10)}%\n\n"
        f"📅 Дата регистрации:\n"
        f"{datetime.fromisoformat(user['registration_date']).strftime('%d.%m.%Y')}"
    )
    
    await send_dual_keyboard_message(
        message,
        text,
        profile_reply_kb(),
        profile_inline_kb()
    )

@dp.message(Command("balance"))
async def cmd_balance(message: Message):
    """Команда /balance"""
    user = db.get_user(message.from_user.id)
    balance = user.get("balance", 0.0)
    
    text = (
        f"💰 Ваш баланс: {balance}₽\n\n"
        f"🎁 При оплате с баланса вы получаете {db.settings.get('balance_discount', 10)}% скидку!\n\n"
        "💳 Для пополнения нажмите кнопку ниже:"
    )
    
    await send_dual_keyboard_message(
        message,
        text,
        profile_reply_kb(),
        profile_inline_kb()
    )

@dp.message(Command("admin"))
async def cmd_admin(message: Message):
    """Команда /admin"""
    if message.from_user.id not in config.ADMIN_IDS:
        await message.answer("⛔ У вас нет прав администратора")
        return
    
    await send_dual_keyboard_message(
        message,
        "👨‍💼 Панель администратора\n\nВыберите действие:",
        admin_panel_reply_kb(),
        admin_panel_inline_kb()
    )

@dp.message(Command("help"))
async def cmd_help(message: Message):
    """Команда /help"""
    help_text = (
        "🤖 Помощь по боту\n\n"
        "📋 Основные команды:\n"
        "/start - Запустить бота\n"
        "/profile - Ваш профиль\n"
        "/balance - Ваш баланс\n"
        "/help - Эта справка\n\n"
        
        "🛒 Покупки:\n"
        "• Используйте кнопку '🛒 Каталог' или '🛒 Посмотреть услуги'\n"
        "• Выбирайте категории и фильтры для поиска\n"
        "• Добавляйте товары в корзину\n"
        "• Оплачивайте с баланса (со скидкой!) или другими способами\n\n"
        
        "🏷️ Фильтры:\n"
        "• В каждой категории есть фильтры для удобного поиска\n"
        "• Выбирайте нужные фильтры для отображения товаров\n\n"
        
        "💳 Баланс:\n"
        "• Пополняйте баланс через меню профиля\n"
        "• После оплаты отправьте СКРИНШОТ чека\n"
        "• У вас есть 10 минут на подтверждение\n"
        "• Получайте скидку при оплате с баланса\n\n"
        
        "❓ Поддержка:\n"
        "• Используйте кнопку '❓ Поддержка' в профиле\n"
        "• Напишите напрямую продавцу\n\n"
        
        "Для навигации используйте кнопки меню."
    )
    
    await send_dual_keyboard_message(
        message,
        help_text,
        main_menu_reply_kb(),
        main_menu_inline_kb()
    )

@dp.message(Command("stats"))
async def cmd_stats(message: Message):
    """Команда /stats (только для админов)"""
    if message.from_user.id not in config.ADMIN_IDS:
        await message.answer("⛔ У вас нет прав администратора")
        return
    
    categories_count = len(db.get_categories())
    products_count = len(db.products)
    users_count = len(db.users)
    filters_count = len(db.filters)
    
    deposits = [t for t in db.transactions if t['type'] == 'deposit']
    purchases = [t for t in db.transactions if t['type'] == 'purchase']
    
    total_deposits = sum(t['amount'] for t in deposits)
    total_purchases = sum(abs(t['amount']) for t in purchases)
    
    confirmed_with_screenshot = len([t for t in db.transactions if t.get('screenshot_file_id')])
    
    text = (
        f"📊 Статистика бота\n\n"
        f"📈 Общая статистика:\n"
        f"• Категорий: {categories_count}\n"
        f"• Фильтров: {filters_count}\n"
        f"• Товаров: {products_count}\n"
        f"• Пользователей: {users_count}\n"
        f"• Ожидающих платежей: {len(db.pending_payments)}\n\n"
        f"💰 Финансовая статистика:\n"
        f"• Всего пополнений: {len(deposits)} на {total_deposits:.2f}₽\n"
        f"• Всего покупок: {len(purchases)} на {total_purchases:.2f}₽\n"
        f"• Подтверждено скриншотами: {confirmed_with_screenshot}\n\n"
        f"🎁 Настройки:\n"
        f"• Скидка: {db.settings.get('balance_discount', 10)}%"
    )
    
    await message.answer(text)

# ==================== ОБРАБОТЧИКИ ТЕКСТОВЫХ СООБЩЕНИЙ (РЕАЛЬНЫЕ КНОПКИ) ====================

@dp.message(F.text == "🛒 Каталог")
async def handle_catalog_button(message: Message):
    """Обработка кнопки Каталог"""
    await view_categories_callback_handler(message)

@dp.message(F.text == "📦 Корзина")
async def handle_cart_button(message: Message):
    """Обработка кнопки Корзина"""
    await view_cart_callback_handler(message)

@dp.message(F.text == "👤 Профиль")
async def handle_profile_button(message: Message):
    """Обработка кнопки Профиль"""
    await my_profile_callback_handler(message)

@dp.message(F.text == "💰 Баланс")
async def handle_balance_button(message: Message):
    """Обработка кнопки Баланс"""
    await cmd_balance(message)

@dp.message(F.text == "💳 Пополнить")
async def handle_deposit_button(message: Message, state: FSMContext):
    """Обработка кнопки Пополнить"""
    await deposit_callback_handler(message, state)

@dp.message(F.text == "❓ Поддержка")
async def handle_support_button(message: Message):
    """Обработка кнопки Поддержка"""
    await support_callback_handler(message)

@dp.message(F.text == "👨‍💼 Админ")
async def handle_admin_button(message: Message):
    """Обработка кнопки Админ"""
    if message.from_user.id in config.ADMIN_IDS:
        await cmd_admin(message)
    else:
        await message.answer("⛔ У вас нет прав администратора")

@dp.message(F.text == "🔙 Главное меню")
async def handle_main_menu_button(message: Message):
    """Обработка кнопки Главное меню"""
    await main_menu_callback_handler(message)

@dp.message(F.text == "💼 История")
async def handle_history_button(message: Message):
    """Обработка кнопки История"""
    await transaction_history_callback_handler(message)

@dp.message(F.text == "🛒 Мои заказы")
async def handle_my_orders_button(message: Message):
    """Обработка кнопки Мои заказы"""
    await my_orders_callback_handler(message)

@dp.message(F.text == "🗑️ Очистить корзину")
async def handle_clear_cart_button(message: Message):
    """Обработка кнопки Очистить корзину"""
    await clear_cart_confirm_callback_handler(message)

@dp.message(F.text == "💳 Оплатить балансом")
async def handle_pay_balance_button(message: Message):
    """Обработка кнопки Оплатить балансом"""
    await checkout_balance_callback_handler(message)

@dp.message(F.text == "💳 Оплатить")
async def handle_checkout_button(message: Message):
    """Обработка кнопки Оплатить"""
    await checkout_callback_handler(message)

@dp.message(F.text == "🏦 СБП (Озон)")
async def handle_sber_button(message: Message, state: FSMContext):
    """Обработка кнопки СБП"""
    await process_sber_payment(message, state)

@dp.message(F.text == "💰 ЮMoney")
async def handle_yoomoney_button(message: Message, state: FSMContext):
    """Обработка кнопки ЮMoney"""
    await process_yoomoney_payment(message, state)

@dp.message(F.text == "₿ Криптовалюта")
async def handle_crypto_button(message: Message, state: FSMContext):
    """Обработка кнопки Криптовалюта"""
    await process_crypto_payment(message, state)

@dp.message(F.text == "💳 Баланс")
async def handle_balance_payment_button(message: Message):
    """Обработка кнопки Баланс для оплаты"""
    await pay_balance_callback_handler(message)

@dp.message(F.text.startswith("💰 "))
async def handle_deposit_amount_button(message: Message, state: FSMContext):
    """Обработка кнопок с суммами пополнения"""
    if message.text == "💰 Своя сумма":
        await deposit_callback_handler(message, state)
    else:
        try:
            # Извлекаем сумму из текста (например: "💰 500₽" -> 500)
            amount_text = message.text.replace("💰 ", "").replace("₽", "").strip()
            amount = float(amount_text)
            
            # Проверяем лимиты
            min_deposit = db.settings.get('min_deposit', 100)
            max_deposit = db.settings.get('max_deposit', 50000)
            
            if amount < min_deposit:
                await message.answer(
                    f"❌ Сумма слишком мала! Минимальная сумма: {min_deposit}₽\n"
                    "Выберите другую сумму или введите свою:",
                    reply_markup=deposit_reply_kb()
                )
                return
            
            if amount > max_deposit:
                await message.answer(
                    f"❌ Сумма слишком велика! Максимальная сумма: {max_deposit}₽\n"
                    "Выберите другую сумму или введите свою:",
                    reply_markup=deposit_reply_kb()
                )
                return
            
            await state.update_data(amount=amount)
            await state.set_state(DepositStates.waiting_for_payment_method)
            
            text = (
                f"✅ Сумма: {amount}₽\n\n"
                "Выберите способ оплаты:\n\n"
                "⚠️ После оплаты отправьте скриншот чека"
            )
            
            await send_dual_keyboard_message(
                message,
                text,
                payment_methods_reply_kb(),
                deposit_methods_inline_kb()
            )
            
        except ValueError:
            await message.answer(
                "❌ Неверный формат суммы! Выберите сумму из списка или введите свою:",
                reply_markup=deposit_reply_kb()
            )

# ==================== ОБРАБОТЧИКИ РЕАЛЬНЫХ КНОПОК КАТЕГОРИЙ ====================

@dp.message(lambda message: any(cat["name"] in message.text for cat in db.get_categories()))
async def handle_category_button(message: Message):
    """Обработка кнопок категорий"""
    categories = db.get_categories()
    for category in categories:
        if category["name"] in message.text:
            await category_products_with_filters_callback_handler(message, category['id'])
            return

# ==================== УНИВЕРСАЛЬНЫЕ ОБРАБОТЧИКИ ДЛЯ ОБОИХ ТИПОВ КНОПОК ====================

async def main_menu_callback_handler(message_or_callback):
    """Универсальный обработчик главного меню"""
    text = "Главное меню:"
    
    if isinstance(message_or_callback, Message):
        await send_dual_keyboard_message(
            message_or_callback,
            text,
            main_menu_reply_kb(),
            main_menu_inline_kb()
        )
    else:
        await edit_dual_keyboard_message(
            message_or_callback,
            text,
            main_menu_reply_kb(),
            main_menu_inline_kb()
        )
        await message_or_callback.answer()

async def my_profile_callback_handler(message_or_callback):
    """Универсальный обработчик профиля"""
    if isinstance(message_or_callback, Message):
        user_id = message_or_callback.from_user.id
    else:
        user_id = message_or_callback.from_user.id
    
    user = db.get_user(user_id)
    balance = user.get("balance", 0.0)
    total_spent = user.get("total_spent", 0.0)
    total_orders = user.get("total_orders", 0)
    
    text = (
        f"👤 Ваш профиль\n\n"
        f"💰 Баланс: {balance}₽\n"
        f"💳 Всего потрачено: {total_spent}₽\n"
        f"📦 Заказов: {total_orders} шт.\n"
        f"🎁 Скидка при оплате с баланса: {db.settings.get('balance_discount', 10)}%\n\n"
        f"📅 Дата регистрации:\n"
        f"{datetime.fromisoformat(user['registration_date']).strftime('%d.%m.%Y')}"
    )
    
    if isinstance(message_or_callback, Message):
        await send_dual_keyboard_message(
            message_or_callback,
            text,
            profile_reply_kb(),
            profile_inline_kb()
        )
    else:
        await edit_dual_keyboard_message(
            message_or_callback,
            text,
            profile_reply_kb(),
            profile_inline_kb()
        )
        await message_or_callback.answer()

async def view_categories_callback_handler(message_or_callback):
    """Универсальный обработчик категорий"""
    categories = db.get_categories()
    if not categories:
        text = "📭 Категории пока пусты"
    else:
        text = "📁 Выберите категорию:"
    
    if isinstance(message_or_callback, Message):
        await send_dual_keyboard_message(
            message_or_callback,
            text,
            categories_reply_kb(),
            categories_inline_kb()
        )
    else:
        await edit_dual_keyboard_message(
            message_or_callback,
            text,
            categories_reply_kb(),
            categories_inline_kb()
        )
        await message_or_callback.answer()

async def category_products_with_filters_callback_handler(message_or_callback, category_id):
    """Универсальный обработчик товаров в категории с фильтрами"""
    products = db.get_products_by_category(category_id)
    category = db.get_category(category_id)
    filters = db.get_filters_by_category(category_id)
    
    if not products:
        text = f"📭 В категории '{category['name'] if category else 'Неизвестно'}' пока нет товаров"
        
        if isinstance(message_or_callback, Message):
            await send_dual_keyboard_message(
                message_or_callback,
                text,
                categories_reply_kb(),
                categories_inline_kb()
            )
        else:
            await edit_dual_keyboard_message(
                message_or_callback,
                text,
                None,
                categories_inline_kb()
            )
            await message_or_callback.answer()
        return
    
    # Если есть фильтры, показываем их
    if filters:
        text = f"🛒 Категория: '{category['name'] if category else 'Неизвестно'}'\n\n"
        text += f"📊 Товаров в категории: {len(products)}\n"
        text += "🏷️ Выберите фильтр для поиска:\n"
        
        if isinstance(message_or_callback, Message):
            await send_dual_keyboard_message(
                message_or_callback,
                text,
                None,
                filters_inline_kb(category_id)
            )
        else:
            await edit_dual_keyboard_message(
                message_or_callback,
                text,
                None,
                filters_inline_kb(category_id)
            )
            await message_or_callback.answer()
    else:
        # Если нет фильтров, показываем обычный список товаров
        await category_products_callback_handler(message_or_callback, category_id)

async def category_products_callback_handler(message_or_callback, category_id):
    """Универсальный обработчик товаров в категории"""
    products = db.get_products_by_category(category_id)
    category = db.get_category(category_id)
    
    if not products:
        text = f"📭 В категории '{category['name'] if category else 'Неизвестно'}' пока нет товаров"
    else:
        text = f"🛒 Товары в категории '{category['name'] if category else 'Неизвестно'}':"
    
    if isinstance(message_or_callback, Message):
        await send_dual_keyboard_message(
            message_or_callback,
            text,
            None,
            products_inline_kb(category_id)
        )
    else:
        await edit_dual_keyboard_message(
            message_or_callback,
            text,
            None,
            products_inline_kb(category_id)
        )
        await message_or_callback.answer()

async def view_cart_callback_handler(message_or_callback):
    """Универсальный обработчик корзины"""
    if isinstance(message_or_callback, Message):
        user_id = message_or_callback.from_user.id
    else:
        user_id = message_or_callback.from_user.id
    
    cart = db.get_cart(user_id)
    user_balance = db.get_user_balance(user_id)
    
    if not cart["items"]:
        text = "📭 Ваша корзина пуста"
        with_balance = False
    else:
        text = "📦 Ваша корзина:\n\n"
        for item_id, item in cart["items"].items():
            product = item["product"]
            text += f"• {product['name']}\n"
            text += f"  Количество: {item['quantity']} × {product['price']}₽ = {item['quantity'] * product['price']}₽\n\n"
        
        text += f"💰 Итого: {cart['total']}₽\n\n"
        
        discount_percent = db.settings.get("balance_discount", 10)
        discount_amount = cart['total'] * discount_percent / 100
        discounted_total = cart['total'] - discount_amount
        
        text += f"🎁 При оплате с баланса:\n"
        text += f"• Скидка: {discount_percent}% (-{discount_amount:.2f}₽)\n"
        text += f"• К оплате: {discounted_total:.2f}₽\n\n"
        text += f"💳 Ваш баланс: {user_balance}₽"
        
        with_balance = user_balance >= discounted_total
    
    if isinstance(message_or_callback, Message):
        await send_dual_keyboard_message(
            message_or_callback,
            text,
            cart_reply_kb(with_balance),
            cart_inline_kb(with_balance)
        )
    else:
        await edit_dual_keyboard_message(
            message_or_callback,
            text,
            cart_reply_kb(with_balance),
            cart_inline_kb(with_balance)
        )
        await message_or_callback.answer()

async def deposit_callback_handler(message_or_callback, state: FSMContext):
    """Универсальный обработчик пополнения"""
    if isinstance(message_or_callback, Message):
        user_id = message_or_callback.from_user.id
        await state.set_state(DepositStates.waiting_for_amount)
        
        text = (
            f"💳 Пополнение баланса\n\n"
            f"💰 Минимальная сумма: {db.settings.get('min_deposit', 100)}₽\n"
            f"📈 Максимальная сумма: {db.settings.get('max_deposit', 50000)}₽\n\n"
            "Выберите сумму пополнения или введите свою:\n\n"
            "⚠️ После оплаты вам нужно будет отправить скриншот чека в течение 10 минут"
        )
        
        await send_dual_keyboard_message(
            message_or_callback,
            text,
            deposit_reply_kb(),
            None
        )
    else:
        user_id = message_or_callback.from_user.id
        await state.set_state(DepositStates.waiting_for_amount)
        
        text = (
            f"💳 Пополнение баланса\n\n"
            f"💰 Минимальная сумма: {db.settings.get('min_deposit', 100)}₽\n"
            f"📈 Максимальная сумма: {db.settings.get('max_deposit', 50000)}₽\n\n"
            "Выберите сумму пополнения или введите свою:\n\n"
            "⚠️ После оплаты вам нужно будет отправить скриншот чека в течение 10 минут"
        )
        
        await edit_dual_keyboard_message(
            message_or_callback,
            text,
            deposit_reply_kb(),
            None
        )
        await message_or_callback.answer()

async def support_callback_handler(message_or_callback):
    """Универсальный обработчик поддержки"""
    text = (
        "❓ Поддержка\n\n"
        "Вы можете:\n"
        "• 📞 Написать напрямую продавцу\n"
        "• 🗣️ Отправить сообщение в техподдержку\n\n"
        "Выберите действие:"
    )
    
    if isinstance(message_or_callback, Message):
        await send_dual_keyboard_message(
            message_or_callback,
            text,
            None,
            support_inline_kb()
        )
    else:
        await edit_dual_keyboard_message(
            message_or_callback,
            text,
            None,
            support_inline_kb()
        )
        await message_or_callback.answer()

async def transaction_history_callback_handler(message_or_callback):
    """Универсальный обработчик истории транзакций"""
    if isinstance(message_or_callback, Message):
        user_id = message_or_callback.from_user.id
    else:
        user_id = message_or_callback.from_user.id
    
    transactions = db.get_user_transactions(user_id, limit=15)
    
    if not transactions:
        text = "📭 У вас еще нет транзакций"
    else:
        text = "📊 История транзакций:\n\n"
        
        for i, trans in enumerate(transactions, 1):
            date = datetime.fromisoformat(trans['date']).strftime('%d.%m.%Y %H:%M')
            amount = trans['amount']
            trans_type = trans['type']
            
            if trans_type == 'deposit':
                icon = "⬆️"
                amount_text = f"+{amount}₽"
                color = "🟢"
            else:
                icon = "⬇️"
                amount_text = f"-{abs(amount)}₽"
                color = "🔴"
            
            text += f"{color} {date}\n"
            text += f"{icon} {amount_text} - {trans.get('description', 'Транзакция')}\n"
            if trans.get('screenshot_file_id'):
                text += "📸 Подтверждено скриншотом\n"
            text += f"🆔 ID: {trans['id']}\n\n"
    
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text='💳 Пополнить баланс', callback_data='deposit'),
        InlineKeyboardButton(text='🔙 Назад', callback_data='my_profile')
    )
    
    if isinstance(message_or_callback, Message):
        await send_dual_keyboard_message(
            message_or_callback,
            text,
            profile_reply_kb(),
            builder.as_markup()
        )
    else:
        await edit_dual_keyboard_message(
            message_or_callback,
            text,
            profile_reply_kb(),
            builder.as_markup()
        )
        await message_or_callback.answer()

async def my_orders_callback_handler(message_or_callback):
    """Универсальный обработчик моих заказов"""
    if isinstance(message_or_callback, Message):
        user_id = message_or_callback.from_user.id
    else:
        user_id = message_or_callback.from_user.id
    
    transactions = [t for t in db.transactions if t["user_id"] == user_id and t["type"] == "purchase"]
    
    if not transactions:
        text = "📭 У вас еще нет заказов"
    else:
        text = "🛒 Ваши заказы:\n\n"
        
        for i, trans in enumerate(transactions[:10], 1):
            date = datetime.fromisoformat(trans['date']).strftime('%d.%m.%Y %H:%M')
            amount = abs(trans['amount'])
            
            text += f"🆔 Заказ #{trans.get('id', i)}\n"
            text += f"📅 Дата: {date}\n"
            text += f"💰 Сумма: {amount}₽\n"
            text += f"📝 {trans.get('description', 'Покупка')}\n"
            if trans.get('screenshot_file_id'):
                text += f"📸 Подтвержден скриншотом\n"
            text += "─" * 20 + "\n\n"
    
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text='🛒 Сделать заказ', callback_data='view_categories'),
        InlineKeyboardButton(text='🔙 Назад', callback_data='my_profile')
    )
    
    if isinstance(message_or_callback, Message):
        await send_dual_keyboard_message(
            message_or_callback,
            text,
            profile_reply_kb(),
            builder.as_markup()
        )
    else:
        await edit_dual_keyboard_message(
            message_or_callback,
            text,
            profile_reply_kb(),
            builder.as_markup()
        )
        await message_or_callback.answer()

async def clear_cart_confirm_callback_handler(message_or_callback):
    """Универсальный обработчик подтверждения очистки корзины"""
    if isinstance(message_or_callback, Message):
        user_id = message_or_callback.from_user.id
    else:
        user_id = message_or_callback.from_user.id
    
    cart = db.get_cart(user_id)
    
    if not cart["items"]:
        if isinstance(message_or_callback, Message):
            await message_or_callback.answer("Корзина уже пуста", show_alert=False)
        else:
            await message_or_callback.answer("Корзина уже пуста", show_alert=True)
        return
    
    text = "⚠️ Вы уверены, что хотите очистить корзину?"
    
    if isinstance(message_or_callback, Message):
        await send_dual_keyboard_message(
            message_or_callback,
            text,
            None,
            confirm_clear_inline_kb()
        )
    else:
        await edit_dual_keyboard_message(
            message_or_callback,
            text,
            None,
            confirm_clear_inline_kb()
        )
        await message_or_callback.answer()

async def checkout_balance_callback_handler(message_or_callback):
    """Универсальный обработчик оплаты балансом"""
    if isinstance(message_or_callback, Message):
        user_id = message_or_callback.from_user.id
    else:
        user_id = message_or_callback.from_user.id
    
    cart = db.get_cart(user_id)
    
    if not cart["items"]:
        if isinstance(message_or_callback, Message):
            await message_or_callback.answer("Корзина пуста", show_alert=False)
        else:
            await message_or_callback.answer("Корзина пуста", show_alert=True)
        return
    
    discount_percent = db.settings.get("balance_discount", 10)
    discount_amount = cart['total'] * discount_percent / 100
    total_with_discount = cart['total'] - discount_amount
    
    user_balance = db.get_user_balance(user_id)
    
    if user_balance < total_with_discount:
        text = (
            f"❌ Недостаточно средств на балансе!\n\n"
            f"Нужно: {total_with_discount:.2f}₽\n"
            f"Доступно: {user_balance}₽"
        )
        if isinstance(message_or_callback, Message):
            await message_or_callback.answer(text, show_alert=False)
        else:
            await message_or_callback.answer(text, show_alert=True)
        return
    
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text='✅ Да, оплатить', callback_data='confirm_balance_payment'),
        InlineKeyboardButton(text='❌ Нет, отмена', callback_data='view_cart')
    )
    
    text = (
        f"💳 Подтверждение оплаты с баланса\n\n"
        f"💰 Сумма заказа: {cart['total']}₽\n"
        f"🎁 Скидка ({discount_percent}%): -{discount_amount:.2f}₽\n"
        f"💸 К оплате: {total_with_discount:.2f}₽\n\n"
        f"💳 Ваш баланс: {user_balance}₽\n"
        f"💳 Баланс после оплаты: {user_balance - total_with_discount:.2f}₽\n\n"
        f"Подтвердить оплату?"
    )
    
    if isinstance(message_or_callback, Message):
        await send_dual_keyboard_message(
            message_or_callback,
            text,
            None,
            builder.as_markup()
        )
    else:
        await edit_dual_keyboard_message(
            message_or_callback,
            text,
            None,
            builder.as_markup()
        )
        await message_or_callback.answer()

async def checkout_callback_handler(message_or_callback):
    """Универсальный обработчик оформления заказа"""
    if isinstance(message_or_callback, Message):
        user_id = message_or_callback.from_user.id
    else:
        user_id = message_or_callback.from_user.id
    
    cart = db.get_cart(user_id)
    
    if not cart["items"]:
        if isinstance(message_or_callback, Message):
            await message_or_callback.answer("Корзина пуста", show_alert=False)
        else:
            await message_or_callback.answer("Корзина пуста", show_alert=True)
        return
    
    discount_percent = db.settings.get("balance_discount", 10)
    discount_amount = cart['total'] * discount_percent / 100
    discounted_total = cart['total'] - discount_amount
    
    text = (
        f"💰 К оплате: {cart['total']}₽\n\n"
        f"🎁 При оплате с баланса:\n"
        f"• Скидка: {discount_percent}% (-{discount_amount:.2f}₽)\n"
        f"• К оплате: {discounted_total:.2f}₽\n\n"
        "💳 Выберите способ оплаты:\n\n"
        "⚠️ После оплаты нужно будет отправить скриншот чека"
    )
    
    if isinstance(message_or_callback, Message):
        await send_dual_keyboard_message(
            message_or_callback,
            text,
            payment_methods_reply_kb(),
            payment_choice_inline_kb()
        )
    else:
        await edit_dual_keyboard_message(
            message_or_callback,
            text,
            payment_methods_reply_kb(),
            payment_choice_inline_kb()
        )
        await message_or_callback.answer()

async def pay_balance_callback_handler(message_or_callback):
    """Универсальный обработчик оплаты балансом через меню"""
    await checkout_balance_callback_handler(message_or_callback)

# ==================== ОБРАБОТЧИКИ ПЛАТЕЖЕЙ С РЕАЛЬНЫМИ КНОПКАМИ ====================

async def process_sber_payment(message: Message, state: FSMContext):
    """Обработка оплаты через СБП с реальной кнопки"""
    await process_external_payment_button(message, state, "sber")

async def process_yoomoney_payment(message: Message, state: FSMContext):
    """Обработка оплаты через ЮMoney с реальной кнопки"""
    await process_external_payment_button(message, state, "yoomoney")

async def process_crypto_payment(message: Message, state: FSMContext):
    """Обработка оплаты через криптовалюту с реальной кнопки"""
    await process_external_payment_button(message, state, "crypto")

async def process_external_payment_button(message: Message, state: FSMContext, method: str):
    """Общая обработка внешних платежей с реальных кнопок"""
    data = await state.get_data()
    amount = data.get('amount')
    
    if not amount:
        # Если сумма не установлена в состоянии, это заказ из корзины
        cart = db.get_cart(message.from_user.id)
        if not cart["items"]:
            await message.answer("Корзина пуста", reply_markup=cart_reply_kb())
            return
        
        amount = cart['total']
        is_cart_payment = True
    else:
        is_cart_payment = False
    
    payment_info = config.PAYMENT_DETAILS.get(method, {})
    
    if is_cart_payment:
        transaction_id = f"ORD_{message.from_user.id}_{int(datetime.now().timestamp())}"
        # Создаем pending платеж для заказа
        items_list = []
        cart = db.get_cart(message.from_user.id)
        for item_id, item in cart["items"].items():
            product = item["product"]
            items_list.append({
                'id': product['id'],
                'name': product['name'],
                'price': product['price'],
                'quantity': item['quantity']
            })
        
        payment_data = {
            'user_id': message.from_user.id,
            'username': message.from_user.username,
            'amount': amount,
            'method': payment_info.get('name', method),
            'transaction_id': transaction_id,
            'type': 'purchase',
            'description': f"Покупка товаров на {amount}₽",
            'cart_data': {
                'items': items_list,
                'total': amount
            }
        }
    else:
        transaction_id = f"DEP_{message.from_user.id}_{int(datetime.now().timestamp())}"
        # Создаем pending платеж для пополнения
        payment_data = {
            'user_id': message.from_user.id,
            'username': message.from_user.username,
            'amount': amount,
            'method': payment_info.get('name', method),
            'transaction_id': transaction_id,
            'type': 'deposit',
            'description': f"Пополнение баланса на {amount}₽ через {payment_info.get('name', method)}"
        }
    
    db.add_pending_payment(transaction_id, payment_data)
    
    # Запускаем таймер
    await payment_timer.start_timer(transaction_id, message.from_user.id)
    
    text = ""
    if method == 'sber':
        text = (
            f"🏦 Оплата через {payment_info['name']}\n\n"
            f"💰 Сумма к оплате: {amount}₽\n\n"
            f"📱 Номер телефона:\n"
            f"{payment_info['number']}\n\n"
            f"👤 Получатель:\n"
            f"{payment_info['owner']}\n\n"
            f"📋 Инструкция:\n"
            f"{payment_info['instruction']}\n\n"
        )
    elif method == 'yoomoney':
        text = (
            f"💰 Оплата через {payment_info['name']}\n\n"
            f"💰 Сумма к оплате: {amount}₽\n\n"
            f"💳 Номер кошелька:\n"
            f"{payment_info['number']}\n\n"
            f"👤 Получатель:\n"
            f"{payment_info['owner']}\n\n"
            f"📋 Инструкция:\n"
            f"{payment_info['instruction']}\n\n"
        )
    elif method == 'crypto':
        text = (
            f"₿ Оплата через {payment_info['name']}\n\n"
            f"💰 Сумма к оплате: {amount}₽\n\n"
            f"🔗 Адрес кошелька:\n"
            f"{payment_info['address']}\n\n"
            f"📋 Инструкция:\n"
            f"{payment_info['instruction']}\n\n"
        )
    
    if is_cart_payment:
        text += f"🆔 В комментарии укажите:\nЗаказ {transaction_id}"
    else:
        text += f"🆔 В комментарии укажите:\nПополнение #{message.from_user.id}"
    
    text += (
        f"\n\n📸 После оплаты ОТПРАВЬТЕ СКРИНШОТ ЧЕКА в этот чат\n"
        f"⏰ У вас есть 10 минут на отправку\n"
        f"❌ По истечении времени заявка будет отменена"
    )
    
    # Сохраняем данные в состоянии
    await state.update_data(
        transaction_id=transaction_id,
        payment_type='purchase' if is_cart_payment else 'deposit'
    )
    
    # Переходим к ожиданию скриншота
    await state.set_state(PaymentConfirmationStates.waiting_for_screenshot)
    
    await send_dual_keyboard_message(
        message,
        text,
        screenshot_reply_kb(),
        screenshot_confirmation_inline_kb()
    )

# ==================== ОБРАБОТЧИКИ АДМИН ПАНЕЛИ (РЕАЛЬНЫЕ КНОПКИ) ====================

@dp.message(F.text == "📦 Товары")
async def handle_admin_products_button(message: Message):
    """Обработка кнопки Товары в админке"""
    if message.from_user.id not in config.ADMIN_IDS:
        await message.answer("⛔ У вас нет прав администратора")
        return
    
    await admin_products_callback_handler(message)

@dp.message(F.text == "📁 Категории")
async def handle_admin_categories_button(message: Message):
    """Обработка кнопки Категории в админке"""
    if message.from_user.id not in config.ADMIN_IDS:
        await message.answer("⛔ У вас нет прав администратора")
        return
    
    await admin_categories_callback_handler(message)

@dp.message(F.text == "🏷️ Фильтры")
async def handle_admin_filters_button(message: Message):
    """Обработка кнопки Фильтры в админке"""
    if message.from_user.id not in config.ADMIN_IDS:
        await message.answer("⛔ У вас нет прав администратора")
        return
    
    await admin_filters_callback_handler(message)

@dp.message(F.text == "👥 Пользователи")
async def handle_admin_users_button(message: Message):
    """Обработка кнопки Пользователи в админке"""
    if message.from_user.id not in config.ADMIN_IDS:
        await message.answer("⛔ У вас нет прав администратора")
        return
    
    await admin_users_callback_handler(message)

@dp.message(F.text == "⚙️ Настройки")
async def handle_admin_settings_button(message: Message):
    """Обработка кнопки Настройки в админке"""
    if message.from_user.id not in config.ADMIN_IDS:
        await message.answer("⛔ У вас нет прав администратора")
        return
    
    await admin_settings_callback_handler(message)

@dp.message(F.text == "📊 Статистика")
async def handle_admin_stats_button(message: Message):
    """Обработка кнопки Статистика в админке"""
    if message.from_user.id not in config.ADMIN_IDS:
        await message.answer("⛔ У вас нет прав администратора")
        return
    
    await admin_stats_callback_handler(message)

@dp.message(F.text == "⏳ Ожидающие")
async def handle_admin_pending_button(message: Message):
    """Обработка кнопки Ожидающие в админке"""
    if message.from_user.id not in config.ADMIN_IDS:
        await message.answer("⛔ У вас нет прав администратора")
        return
    
    await admin_pending_payments_callback_handler(message)

@dp.message(F.text == "🔙 Админ-панель")
async def handle_back_admin_button(message: Message):
    """Обработка кнопки Назад в админке"""
    if message.from_user.id not in config.ADMIN_IDS:
        await message.answer("⛔ У вас нет прав администратора")
        return
    
    await admin_panel_callback_handler(message)

@dp.message(F.text == "➕ Добавить товар")
async def handle_add_product_button(message: Message, state: FSMContext):
    """Обработка кнопки Добавить товар"""
    if message.from_user.id not in config.ADMIN_IDS:
        await message.answer("⛔ У вас нет прав администратора")
        return
    
    await admin_add_product_callback_handler(message, state)

@dp.message(F.text == "✏️ Редактировать")
async def handle_edit_product_button(message: Message, state: FSMContext):
    """Обработка кнопки Редактировать товар"""
    if message.from_user.id not in config.ADMIN_IDS:
        await message.answer("⛔ У вас нет прав администратора")
        return
    
    await admin_edit_product_callback_handler(message, state)

@dp.message(F.text == "❌ Удалить товар")
async def handle_delete_product_button(message: Message, state: FSMContext):
    """Обработка кнопки Удалить товар"""
    if message.from_user.id not in config.ADMIN_IDS:
        await message.answer("⛔ У вас нет прав администратора")
        return
    
    await admin_delete_product_callback_handler(message, state)

@dp.message(F.text == "📋 Список")
async def handle_list_products_button(message: Message):
    """Обработка кнопки Список товаров"""
    if message.from_user.id not in config.ADMIN_IDS:
        await message.answer("⛔ У вас нет прав администратора")
        return
    
    await admin_list_products_callback_handler(message)

# ==================== УНИВЕРСАЛЬНЫЕ ОБРАБОТЧИКИ АДМИН ПАНЕЛИ ====================

async def admin_panel_callback_handler(message_or_callback):
    """Универсальный обработчик админ панели"""
    if isinstance(message_or_callback, Message):
        if message_or_callback.from_user.id not in config.ADMIN_IDS:
            await message_or_callback.answer("⛔ У вас нет прав администратора")
            return
    else:
        if message_or_callback.from_user.id not in config.ADMIN_IDS:
            await message_or_callback.answer("⛔ Нет доступа", show_alert=True)
            return
    
    text = "👨‍💼 Панель администратора\n\nВыберите действие:"
    
    if isinstance(message_or_callback, Message):
        await send_dual_keyboard_message(
            message_or_callback,
            text,
            admin_panel_reply_kb(),
            admin_panel_inline_kb()
        )
    else:
        await edit_dual_keyboard_message(
            message_or_callback,
            text,
            admin_panel_reply_kb(),
            admin_panel_inline_kb()
        )
        await message_or_callback.answer()

async def admin_products_callback_handler(message_or_callback):
    """Универсальный обработчик управления товарами"""
    if isinstance(message_or_callback, Message):
        if message_or_callback.from_user.id not in config.ADMIN_IDS:
            await message_or_callback.answer("⛔ У вас нет прав администратора")
            return
    else:
        if message_or_callback.from_user.id not in config.ADMIN_IDS:
            await message_or_callback.answer("⛔ Нет доступа", show_alert=True)
            return
    
    text = "📦 Управление товарами\n\nВыберите действие:"
    
    if isinstance(message_or_callback, Message):
        await send_dual_keyboard_message(
            message_or_callback,
            text,
            admin_products_reply_kb(),
            admin_products_inline_kb()
        )
    else:
        await edit_dual_keyboard_message(
            message_or_callback,
            text,
            admin_products_reply_kb(),
            admin_products_inline_kb()
        )
        await message_or_callback.answer()

async def admin_categories_callback_handler(message_or_callback):
    """Универсальный обработчик управления категориями"""
    if isinstance(message_or_callback, Message):
        if message_or_callback.from_user.id not in config.ADMIN_IDS:
            await message_or_callback.answer("⛔ У вас нет прав администратора")
            return
    else:
        if message_or_callback.from_user.id not in config.ADMIN_IDS:
            await message_or_callback.answer("⛔ Нет доступа", show_alert=True)
            return
    
    text = "📁 Управление категориями\n\nВыберите действие:"
    
    if isinstance(message_or_callback, Message):
        await send_dual_keyboard_message(
            message_or_callback,
            text,
            admin_categories_reply_kb(),
            admin_categories_inline_kb()
        )
    else:
        await edit_dual_keyboard_message(
            message_or_callback,
            text,
            admin_categories_reply_kb(),
            admin_categories_inline_kb()
        )
        await message_or_callback.answer()

async def admin_filters_callback_handler(message_or_callback):
    """Универсальный обработчик управления фильтрами"""
    if isinstance(message_or_callback, Message):
        if message_or_callback.from_user.id not in config.ADMIN_IDS:
            await message_or_callback.answer("⛔ У вас нет прав администратора")
            return
    else:
        if message_or_callback.from_user.id not in config.ADMIN_IDS:
            await message_or_callback.answer("⛔ Нет доступа", show_alert=True)
            return
    
    text = "🏷️ Управление фильтрами/тегами/подкатегориями\n\nВыберите действие:"
    
    if isinstance(message_or_callback, Message):
        await send_dual_keyboard_message(
            message_or_callback,
            text,
            admin_filters_reply_kb(),
            admin_filters_inline_kb()
        )
    else:
        await edit_dual_keyboard_message(
            message_or_callback,
            text,
            admin_filters_reply_kb(),
            admin_filters_inline_kb()
        )
        await message_or_callback.answer()

async def admin_users_callback_handler(message_or_callback):
    """Универсальный обработчик управления пользователями"""
    if isinstance(message_or_callback, Message):
        if message_or_callback.from_user.id not in config.ADMIN_IDS:
            await message_or_callback.answer("⛔ У вас нет прав администратора")
            return
    else:
        if message_or_callback.from_user.id not in config.ADMIN_IDS:
            await message_or_callback.answer("⛔ Нет доступа", show_alert=True)
            return
    
    # Получаем статистику по пользователям
    total_users = len(db.users)
    total_balance = sum(user.get("balance", 0) for user in db.users.values())
    total_orders = sum(user.get("total_orders", 0) for user in db.users.values())
    
    # Топ пользователей по балансу
    top_users = sorted(
        [(uid, data) for uid, data in db.users.items()],
        key=lambda x: x[1].get("balance", 0),
        reverse=True
    )[:5]
    
    text = (
        f"👥 Управление пользователями\n\n"
        f"📊 Статистика:\n"
        f"• Всего пользователей: {total_users}\n"
        f"• Общая сумма балансов: {total_balance:.2f}₽\n"
        f"• Всего заказов: {total_orders}\n"
        f"• Ожидающих платежей: {len(db.pending_payments)}\n\n"
        f"🏆 Топ-5 по балансу:\n"
    )
    
    for i, (uid, user_data) in enumerate(top_users, 1):
        balance = user_data.get("balance", 0)
        orders = user_data.get("total_orders", 0)
        spent = user_data.get("total_spent", 0)
        text += f"{i}. ID: {uid} | 💰{balance}₽ | 📦{orders} | 💸{spent}₽\n"
    
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text='📊 Подробная статистика', callback_data='admin_user_stats'),
        InlineKeyboardButton(text='🔙 В админ-панель', callback_data='admin_panel')
    )
    
    if isinstance(message_or_callback, Message):
        await send_dual_keyboard_message(
            message_or_callback,
            text,
            admin_panel_reply_kb(),
            builder.as_markup()
        )
    else:
        await edit_dual_keyboard_message(
            message_or_callback,
            text,
            admin_panel_reply_kb(),
            builder.as_markup()
        )
        await message_or_callback.answer()

async def admin_settings_callback_handler(message_or_callback):
    """Универсальный обработчик настроек бота"""
    if isinstance(message_or_callback, Message):
        if message_or_callback.from_user.id not in config.ADMIN_IDS:
            await message_or_callback.answer("⛔ У вас нет прав администратора")
            return
    else:
        if message_or_callback.from_user.id not in config.ADMIN_IDS:
            await message_or_callback.answer("⛔ Нет доступа", show_alert=True)
            return
    
    text = "⚙️ Настройки бота\n\nВыберите настройку для изменения:"
    
    if isinstance(message_or_callback, Message):
        await send_dual_keyboard_message(
            message_or_callback,
            text,
            admin_settings_reply_kb(),
            admin_settings_inline_kb()
        )
    else:
        await edit_dual_keyboard_message(
            message_or_callback,
            text,
            admin_settings_reply_kb(),
            admin_settings_inline_kb()
        )
        await message_or_callback.answer()

async def admin_stats_callback_handler(message_or_callback):
    """Универсальный обработчик статистики"""
    if isinstance(message_or_callback, Message):
        if message_or_callback.from_user.id not in config.ADMIN_IDS:
            await message_or_callback.answer("⛔ У вас нет прав администратора")
            return
    else:
        if message_or_callback.from_user.id not in config.ADMIN_IDS:
            await message_or_callback.answer("⛔ Нет доступа", show_alert=True)
            return
    
    categories_count = len(db.get_categories())
    filters_count = len(db.filters)
    products_count = len(db.products)
    users_count = len(db.users)
    
    deposits = [t for t in db.transactions if t['type'] == 'deposit']
    purchases = [t for t in db.transactions if t['type'] == 'purchase']
    
    total_deposits = sum(t['amount'] for t in deposits)
    total_purchases = sum(abs(t['amount']) for t in purchases)
    
    confirmed_with_screenshot = len([t for t in db.transactions if t.get('screenshot_file_id')])
    
    # Статистика по пользователям
    active_users = 0
    now = datetime.now()
    for user in db.users.values():
        last_activity = datetime.fromisoformat(user.get('last_activity', now.isoformat()))
        if (now - last_activity).days < 30:
            active_users += 1
    
    # Статистика по заказам сегодня
    today = datetime.now().date()
    today_orders = 0
    for t in purchases:
        trans_date = datetime.fromisoformat(t['date']).date()
        if trans_date == today:
            today_orders += 1
    
    text = (
        f"📊 Расширенная статистика\n\n"
        f"📈 Общая статистика:\n"
        f"• Категорий: {categories_count}\n"
        f"• Фильтров: {filters_count}\n"
        f"• Товаров: {products_count}\n"
        f"• Пользователей: {users_count}\n"
        f"• Активных (30 дней): {active_users}\n"
        f"• Ожидающих платежей: {len(db.pending_payments)}\n\n"
        f"💰 Финансовая статистика:\n"
        f"• Всего пополнений: {len(deposits)} на {total_deposits:.2f}₽\n"
        f"• Всего покупок: {len(purchases)} на {total_purchases:.2f}₽\n"
        f"• Заказов сегодня: {today_orders}\n"
        f"• Подтверждено скриншотами: {confirmed_with_screenshot}\n\n"
        f"🎁 Настройки:\n"
        f"• Скидка: {db.settings.get('balance_discount', 10)}%\n"
        f"• Балансы пользователей: {sum(u.get('balance', 0) for u in db.users.values()):.2f}₽"
    )
    
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text='🔙 В админ-панель', callback_data='admin_panel')
    )
    
    if isinstance(message_or_callback, Message):
        await send_dual_keyboard_message(
            message_or_callback,
            text,
            admin_panel_reply_kb(),
            builder.as_markup()
        )
    else:
        await edit_dual_keyboard_message(
            message_or_callback,
            text,
            admin_panel_reply_kb(),
            builder.as_markup()
        )
        await message_or_callback.answer()

async def admin_pending_payments_callback_handler(message_or_callback):
    """Универсальный обработчик ожидающих платежей"""
    if isinstance(message_or_callback, Message):
        if message_or_callback.from_user.id not in config.ADMIN_IDS:
            await message_or_callback.answer("⛔ У вас нет прав администратора")
            return
    else:
        if message_or_callback.from_user.id not in config.ADMIN_IDS:
            await message_or_callback.answer("⛔ Нет доступа", show_alert=True)
            return
    
    pending_payments = db.pending_payments
    
    if not pending_payments:
        text = "📭 Нет ожидающих подтверждения платежей"
    else:
        text = "⏳ Ожидающие подтверждения:\n\n"
        
        for i, (payment_id, payment) in enumerate(pending_payments.items(), 1):
            created = datetime.fromisoformat(payment.get('created_at', datetime.now().isoformat()))
            elapsed = datetime.now() - created
            minutes_left = max(0, config.SCREENSHOT_TIMEOUT - elapsed.total_seconds()) / 60
            
            text += f"{i}. 🆔 {payment_id}\n"
            text += f"   👤 User: @{payment.get('username', 'N/A')} ({payment.get('user_id')})\n"
            text += f"   💰 Сумма: {payment.get('amount')}₽\n"
            text += f"   📝 Тип: {payment.get('type', 'unknown')}\n"
            text += f"   💳 Метод: {payment.get('method', 'unknown')}\n"
            text += f"   ⏰ Осталось: {minutes_left:.1f} мин.\n"
            text += f"   📅 Создан: {created.strftime('%H:%M:%S')}\n\n"
    
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text='🔄 Обновить', callback_data='admin_pending_payments'),
        InlineKeyboardButton(text='🔙 Назад', callback_data='admin_panel')
    )
    
    if isinstance(message_or_callback, Message):
        await send_dual_keyboard_message(
            message_or_callback,
            text,
            admin_panel_reply_kb(),
            builder.as_markup()
        )
    else:
        await edit_dual_keyboard_message(
            message_or_callback,
            text,
            admin_panel_reply_kb(),
            builder.as_markup()
        )
        await message_or_callback.answer()

async def admin_add_product_callback_handler(message_or_callback, state: FSMContext):
    """Универсальный обработчик добавления товара"""
    if isinstance(message_or_callback, Message):
        if message_or_callback.from_user.id not in config.ADMIN_IDS:
            await message_or_callback.answer("⛔ У вас нет прав администратора")
            return
    else:
        if message_or_callback.from_user.id not in config.ADMIN_IDS:
            await message_or_callback.answer("⛔ Нет доступа", show_alert=True)
            return
    
    categories = db.get_categories()
    if not categories:
        if isinstance(message_or_callback, Message):
            await message_or_callback.answer("Сначала добавьте категории", show_alert=False)
        else:
            await message_or_callback.answer("Сначала добавьте категории", show_alert=True)
        return
    
    if isinstance(message_or_callback, Message):
        text = "➕ Добавление нового товара\n\nВыберите категорию для товара:"
        await send_dual_keyboard_message(
            message_or_callback,
            text,
            None,
            categories_list_inline_kb('add_product')
        )
    else:
        await message_or_callback.message.edit_text(
            "➕ Добавление нового товара\n\n"
            "Выберите категорию для товара:",
            reply_markup=categories_list_inline_kb('add_product')
        )
        await message_or_callback.answer()

async def admin_edit_product_callback_handler(message_or_callback, state: FSMContext):
    """Универсальный обработчик редактирования товара"""
    if isinstance(message_or_callback, Message):
        if message_or_callback.from_user.id not in config.ADMIN_IDS:
            await message_or_callback.answer("⛔ У вас нет прав администратора")
            return
        
        await state.set_state(EditProductStates.waiting_for_product_id)
        
        await send_dual_keyboard_message(
            message_or_callback,
            "✏️ Редактирование товара\n\nВведите ID товара для редактирования:",
            cancel_reply_kb(),
            cancel_inline_kb()
        )
    else:
        if message_or_callback.from_user.id not in config.ADMIN_IDS:
            await message_or_callback.answer("⛔ Нет доступа", show_alert=True)
            return
        
        await state.set_state(EditProductStates.waiting_for_product_id)
        
        await message_or_callback.message.edit_text(
            "✏️ Редактирование товара\n\nВведите ID товара для редактирования:",
            reply_markup=cancel_inline_kb()
        )
        await message_or_callback.answer()

async def admin_delete_product_callback_handler(message_or_callback, state: FSMContext):
    """Универсальный обработчик удаления товара"""
    if isinstance(message_or_callback, Message):
        if message_or_callback.from_user.id not in config.ADMIN_IDS:
            await message_or_callback.answer("⛔ У вас нет прав администратора")
            return
        
        await state.set_state(DeleteProductStates.waiting_for_product_id)
        
        await send_dual_keyboard_message(
            message_or_callback,
            "❌ Удаление товара\n\nВведите ID товара для удаления:",
            cancel_reply_kb(),
            cancel_inline_kb()
        )
    else:
        if message_or_callback.from_user.id not in config.ADMIN_IDS:
            await message_or_callback.answer("⛔ Нет доступа", show_alert=True)
            return
        
        await state.set_state(DeleteProductStates.waiting_for_product_id)
        
        await message_or_callback.message.edit_text(
            "❌ Удаление товара\n\nВведите ID товара для удаления:",
            reply_markup=cancel_inline_kb()
        )
        await message_or_callback.answer()

async def admin_list_products_callback_handler(message_or_callback):
    """Универсальный обработчик списка товаров"""
    if isinstance(message_or_callback, Message):
        if message_or_callback.from_user.id not in config.ADMIN_IDS:
            await message_or_callback.answer("⛔ У вас нет прав администратора")
            return
    else:
        if message_or_callback.from_user.id not in config.ADMIN_IDS:
            await message_or_callback.answer("⛔ Нет доступа", show_alert=True)
            return
    
    products = db.products
    categories = {cat['id']: cat['name'] for cat in db.categories}
    
    if not products:
        text = "📭 Товаров пока нет"
    else:
        text = "📦 Список всех товаров:\n\n"
        
        for product in products:
            category_name = categories.get(product['category_id'], 'Неизвестно')
            
            # Получаем фильтры товара
            product_filters = db.get_product_filters(product['id'])
            filter_text = ""
            if product_filters:
                filter_names = [f['name'] for f in product_filters]
                filter_text = f"\n🏷️ Фильтры: {', '.join(filter_names)}"
            
            text += f"🆔 ID: {product['id']}\n"
            text += f"📦 Название: {product['name']}\n"
            text += f"💰 Цена: {product['price']}₽\n"
            text += f"📊 В наличии: {product.get('quantity', 9999)} шт.\n"
            text += f"📁 Категория: {category_name}{filter_text}\n"
            text += "─" * 30 + "\n\n"
    
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text='➕ Добавить товар', callback_data='admin_add_product'),
        InlineKeyboardButton(text='🔙 Назад', callback_data='admin_products')
    )
    
    if isinstance(message_or_callback, Message):
        await send_dual_keyboard_message(
            message_or_callback,
            text,
            admin_products_reply_kb(),
            builder.as_markup()
        )
    else:
        await edit_dual_keyboard_message(
            message_or_callback,
            text,
            admin_products_reply_kb(),
            builder.as_markup()
        )
        await message_or_callback.answer()

# ==================== ОБРАБОТЧИКИ СКРИНШОТОВ С РЕАЛЬНЫМИ КНОПКАМИ ====================

@dp.message(F.text == "📸 Отправить скриншот")
async def handle_screenshot_button(message: Message):
    """Обработка кнопки отправки скриншота"""
    await message.answer(
        "📸 Отправьте скриншот чека:\n\n"
        "Нажмите на значок 📎 и выберите фото",
        reply_markup=screenshot_reply_kb()
    )

@dp.message(F.text == "📝 Инструкция")
async def handle_screenshot_instructions_button(message: Message):
    """Обработка кнопки инструкции по скриншоту"""
    await send_screenshot_instructions(message.from_user.id)
    await message.answer("✅ Инструкция отправлена!")

@dp.message(F.text == "❌ Отменить")
async def handle_cancel_screenshot_button(message: Message, state: FSMContext):
    """Обработка кнопки отмены скриншота"""
    user_id = message.from_user.id
    
    # Находим pending платеж пользователя
    payment_id, payment = db.get_user_pending_payment(user_id)
    
    if payment_id:
        # Отменяем таймер
        payment_timer.cancel_timer(payment_id)
        
        # Удаляем из ожидания
        if payment_id in db.pending_payments:
            del db.pending_payments[payment_id]
            db.save_users_data()
    
    await state.clear()
    
    if user_id in config.ADMIN_IDS:
        await send_dual_keyboard_message(
            message,
            "❌ Подтверждение платежа отменено",
            admin_panel_reply_kb(),
            admin_panel_inline_kb()
        )
    else:
        await send_dual_keyboard_message(
            message,
            "❌ Подтверждение платежа отменено",
            main_menu_reply_kb(),
            main_menu_inline_kb()
        )

# ==================== ОБРАБОТКА СКРИНШОТОВ ====================

@dp.message(PaymentConfirmationStates.waiting_for_screenshot, F.photo)
async def handle_screenshot_photo(message: Message, state: FSMContext):
    """Обработка скриншота"""
    # Получаем file_id самого большого размера фото
    file_id = message.photo[-1].file_id
    
    # Находим pending платеж пользователя
    payment_id, payment = db.get_user_pending_payment(message.from_user.id)
    
    if not payment_id:
        await message.answer(
            "❌ Не найден ожидающий платеж\n"
            "Возможно, время подтверждения истекло",
            reply_markup=main_menu_reply_kb() if message.from_user.id not in config.ADMIN_IDS else admin_panel_reply_kb()
        )
        await state.clear()
        return
    
    # Сохраняем file_id в состоянии
    await state.update_data(
        screenshot_file_id=file_id,
        payment_id=payment_id
    )
    
    # Отправляем инструкцию для комментария
    await message.answer(
        "✅ Скриншот получен!\n\n"
        "Теперь вы можете добавить КОММЕНТАРИЙ к платежу (по желанию):\n"
        "• Например: 'Оплатил через Сбербанк'\n"
        "• Или отправьте '-' чтобы пропустить\n\n"
        "Для пропуска комментария нажмите кнопку ниже",
        reply_markup=after_screenshot_inline_kb()
    )
    
    # Останавливаем таймер
    payment_timer.cancel_timer(payment_id)

@dp.message(PaymentConfirmationStates.waiting_for_screenshot)
async def handle_no_photo(message: Message):
    """Если отправили не фото"""
    await message.answer(
        "❌ Пожалуйста, отправьте СКРИНШОТ (фото) чека!\n"
        "Нажмите на значок 📎 и выберите фото\n\n"
        "Или нажмите кнопку '📸 Отправить скриншот' для инструкции",
        reply_markup=screenshot_reply_kb()
    )

# ==================== СУЩЕСТВУЮЩИЕ КОЛБЭК ОБРАБОТЧИКИ (сохранены для совместимости) ====================

@dp.callback_query(F.data == 'main_menu')
async def main_menu_callback(callback: CallbackQuery):
    await main_menu_callback_handler(callback)

@dp.callback_query(F.data == 'my_profile')
async def my_profile_callback(callback: CallbackQuery):
    await my_profile_callback_handler(callback)

@dp.callback_query(F.data == 'view_categories')
async def view_categories_callback(callback: CallbackQuery):
    await view_categories_callback_handler(callback)

@dp.callback_query(F.data.startswith('category_'))
async def category_products_callback(callback: CallbackQuery):
    try:
        category_id = int(callback.data.split('_')[1])
        await category_products_with_filters_callback_handler(callback, category_id)
    except Exception as e:
        print(f"Ошибка в category_products_callback: {e}")
        await callback.answer("Ошибка загрузки товаров", show_alert=True)

@dp.callback_query(F.data.startswith('filter_all_'))
async def show_all_products_filter(callback: CallbackQuery):
    """Показать все товары в категории"""
    category_id = int(callback.data.split('_')[2])
    products = db.get_products_by_category(category_id)
    category = db.get_category(category_id)
    
    if not products:
        await callback.answer("В этой категории нет товаров", show_alert=True)
        return
    
    text = f"📦 Все товары в категории '{category['name']}':\n\n"
    
    await callback.message.edit_text(
        text,
        reply_markup=products_inline_kb(category_id)
    )
    await callback.answer()

@dp.callback_query(F.data.startswith('filter_'))
async def show_products_by_filter(callback: CallbackQuery):
    """Показать товары по фильтру"""
    try:
        data_parts = callback.data.split('_')
        filter_id = int(data_parts[1])
        category_id = int(data_parts[2])
        
        filter_item = db.get_filter(filter_id)
        category = db.get_category(category_id)
        
        if not filter_item or not category:
            await callback.answer("Фильтр или категория не найдены", show_alert=True)
            return
        
        products = db.get_products_by_filter(category_id, filter_id)
        
        if not products:
            await callback.answer(f"В фильтре '{filter_item['name']}' нет товаров", show_alert=True)
            return
        
        text = f"🏷️ Фильтр: {filter_item['name']}\n"
        text += f"📁 Категория: {category['name']}\n\n"
        
        await callback.message.edit_text(
            text,
            reply_markup=products_inline_kb(category_id, filter_id)
        )
        await callback.answer()
        
    except Exception as e:
        print(f"Ошибка в show_products_by_filter: {e}")
        await callback.answer("Ошибка загрузки товаров", show_alert=True)

@dp.callback_query(F.data.startswith('product_'))
async def product_detail_callback(callback: CallbackQuery):
    try:
        product_id = int(callback.data.split('_')[1])
        product = db.get_product(product_id)
        
        if not product:
            await callback.answer("Товар не найден", show_alert=True)
            return
        
        category = db.get_category(product["category_id"])
        
        # Получаем фильтры товара
        product_filters = db.get_product_filters(product_id)
        filters_text = ""
        if product_filters:
            filter_names = [f['name'] for f in product_filters]
            filters_text = f"\n🏷️ Фильтры: {', '.join(filter_names)}\n"
        
        text = (
            f"📦 {product['name']}\n\n"
            f"💰 Цена: {product['price']}₽\n"
            f"{filters_text}"
            f"📝 Описание: {product.get('description', 'Нет описания')}\n"
            f"📊 В наличии: {product.get('quantity', 9999)} шт.\n"
            f"📁 Категория: {category['name'] if category else 'Не указана'}"
        )
        
        await callback.message.edit_text(
            text, 
            reply_markup=product_detail_inline_kb(product_id, product["category_id"])
        )
        await callback.answer()
    except Exception as e:
        print(f"Ошибка в product_detail_callback: {e}")
        await callback.answer("Ошибка загрузки товара", show_alert=True)

@dp.callback_query(F.data.startswith('add_to_cart_'))
async def add_to_cart_callback(callback: CallbackQuery):
    try:
        product_id = int(callback.data.split('_')[3])
        success, message = db.add_to_cart(callback.from_user.id, product_id)
        
        await callback.answer(message, show_alert=True)
        if success:
            product = db.get_product(product_id)
            if product:
                category = db.get_category(product["category_id"])
                
                # Получаем фильтры товара
                product_filters = db.get_product_filters(product_id)
                filters_text = ""
                if product_filters:
                    filter_names = [f['name'] for f in product_filters]
                    filters_text = f"\n🏷️ Фильтры: {', '.join(filter_names)}\n"
                
                text = (
                    f"📦 {product['name']}\n\n"
                    f"💰 Цена: {product['price']}₽\n"
                    f"{filters_text}"
                    f"📝 Описание: {product.get('description', 'Нет описания')}\n"
                    f"📊 В наличии: {product.get('quantity', 9999)} шт.\n"
                    f"📁 Категория: {category['name'] if category else 'Не указана'}\n\n"
                    f"✅ {message}"
                )
                
                await callback.message.edit_text(
                    text, 
                    reply_markup=product_detail_inline_kb(product_id, product["category_id"])
                )
    except Exception as e:
        print(f"Ошибка в add_to_cart_callback: {e}")
        await callback.answer("Ошибка при добавлении в корзину", show_alert=True)

@dp.callback_query(F.data.startswith('remove_from_cart_'))
async def remove_from_cart_callback(callback: CallbackQuery):
    try:
        product_id = int(callback.data.split('_')[3])
        success = db.remove_from_cart(callback.from_user.id, product_id)
        
        if success:
            message = "Товар удален из корзины"
        else:
            message = "Товара нет в корзине"
        
        await callback.answer(message, show_alert=True)
        
        # Обновляем информацию о товаре
        product = db.get_product(product_id)
        if product:
            category = db.get_category(product["category_id"])
            
            # Получаем фильтры товара
            product_filters = db.get_product_filters(product_id)
            filters_text = ""
            if product_filters:
                filter_names = [f['name'] for f in product_filters]
                filters_text = f"\n🏷️ Фильтры: {', '.join(filter_names)}\n"
            
            text = (
                f"📦 {product['name']}\n\n"
                f"💰 Цена: {product['price']}₽\n"
                f"{filters_text}"
                f"📝 Описание: {product.get('description', 'Нет описания')}\n"
                f"📊 В наличии: {product.get('quantity', 9999)} шт.\n"
                f"📁 Категория: {category['name'] if category else 'Не указана'}\n\n"
                f"✅ {message}"
            )
            
            await callback.message.edit_text(
                text, 
                reply_markup=product_detail_inline_kb(product_id, product["category_id"])
            )
    except Exception as e:
        print(f"Ошибка в remove_from_cart_callback: {e}")
        await callback.answer("Ошибка при удалении из корзины", show_alert=True)

@dp.callback_query(F.data == 'view_cart')
async def view_cart_callback(callback: CallbackQuery):
    await view_cart_callback_handler(callback)

@dp.callback_query(F.data == 'clear_cart_confirm')
async def clear_cart_confirm_callback(callback: CallbackQuery):
    await clear_cart_confirm_callback_handler(callback)

@dp.callback_query(F.data == 'clear_cart')
async def clear_cart_callback(callback: CallbackQuery):
    db.clear_cart(callback.from_user.id)
    await callback.message.edit_text(
        "✅ Корзина очищена",
        reply_markup=main_menu_inline_kb()
    )
    await callback.answer()

@dp.callback_query(F.data == 'transaction_history')
async def transaction_history_callback(callback: CallbackQuery):
    await transaction_history_callback_handler(callback)

@dp.callback_query(F.data == 'support')
async def support_callback(callback: CallbackQuery):
    await support_callback_handler(callback)

@dp.callback_query(F.data == 'my_orders')
async def my_orders_callback(callback: CallbackQuery):
    await my_orders_callback_handler(callback)

@dp.callback_query(F.data == 'deposit')
async def deposit_callback(callback: CallbackQuery, state: FSMContext):
    await deposit_callback_handler(callback, state)

@dp.callback_query(F.data.startswith('deposit_'))
async def process_deposit_method(callback: CallbackQuery, state: FSMContext):
    method = callback.data.replace('deposit_', '')
    
    if method not in config.PAYMENT_DETAILS:
        await callback.answer("Неизвестный способ оплаты", show_alert=True)
        return
    
    payment_info = config.PAYMENT_DETAILS[method]
    data = await state.get_data()
    amount = data.get('amount', 0)
    
    transaction_id = f"DEP_{callback.from_user.id}_{int(datetime.now().timestamp())}"
    
    await state.update_data(
        method=method,
        transaction_id=transaction_id
    )
    
    text = (
        f"💳 Пополнение баланса\n\n"
        f"💰 Сумма: {amount}₽\n"
        f"🆔 ID транзакции: {transaction_id}\n\n"
        f"📋 Инструкция для оплаты:\n\n"
    )
    
    if method == 'sber':
        text += (
            f"🏦 {payment_info['name']}\n\n"
            f"📱 Номер телефона:\n{payment_info['number']}\n\n"
            f"👤 Получатель: {payment_info['owner']}\n\n"
            f"📝 В комментарии укажите:\n"
            f"Пополнение #{callback.from_user.id}\n\n"
        )
    elif method == 'yoomoney':
        text += (
            f"💰 {payment_info['name']}\n\n"
            f"💳 Номер кошелька:\n{payment_info['number']}\n\n"
            f"👤 Получатель: {payment_info['owner']}\n\n"
            f"📝 В комментарии укажите:\n"
            f"Пополнение #{callback.from_user.id}\n\n"
        )
    elif method == 'crypto':
        text += (
            f"₿ {payment_info['name']}\n\n"
            f"🔗 Адрес кошелька:\n{payment_info['address']}\n\n"
            f"📝 В комментарии укажите:\n"
            f"Пополнение #{callback.from_user.id}\n\n"
        )
    
    text += (
        "📸 После оплаты ОТПРАВЬТЕ СКРИНШОТ ЧЕКА в этот чат\n"
        "⏰ У вас есть 10 минут на отправку\n"
        "❌ По истечении времени заявка будет отменена"
    )
    
    # Создаем pending платеж
    payment_data = {
        'user_id': callback.from_user.id,
        'username': callback.from_user.username,
        'amount': amount,
        'method': payment_info['name'],
        'transaction_id': transaction_id,
        'type': 'deposit',
        'description': f"Пополнение баланса на {amount}₽ через {payment_info['name']}"
    }
    
    db.add_pending_payment(transaction_id, payment_data)
    
    # Запускаем таймер
    await payment_timer.start_timer(transaction_id, callback.from_user.id)
    
    # Переходим к ожиданию скриншота
    await state.set_state(PaymentConfirmationStates.waiting_for_screenshot)
    
    await callback.message.edit_text(text, reply_markup=screenshot_confirmation_inline_kb())
    await callback.answer()

@dp.callback_query(F.data == 'send_screenshot_help')
async def send_screenshot_help_callback(callback: CallbackQuery):
    """Показать инструкцию по скриншоту"""
    await send_screenshot_instructions(callback.from_user.id)
    await callback.answer("Инструкция отправлена в чат")

@dp.callback_query(F.data == 'add_comment')
async def add_comment_callback(callback: CallbackQuery, state: FSMContext):
    """Добавить комментарий"""
    await state.set_state(PaymentConfirmationStates.waiting_for_comment)
    
    await callback.message.edit_text(
        "📝 Введите комментарий к платежу:\n\n"
        "Примеры:\n"
        "• 'Оплатил через Сбербанк'\n"
        "• 'Перевод с карты Тинькофф'\n"
        "• 'Сумма: 1000₽, время: 14:30'\n\n"
        "Или отправьте '-' чтобы пропустить",
        reply_markup=cancel_inline_kb()
    )
    await callback.answer()

@dp.callback_query(F.data == 'skip_comment')
async def skip_comment_callback(callback: CallbackQuery, state: FSMContext):
    """Пропустить комментарий"""
    data = await state.get_data()
    screenshot_file_id = data.get('screenshot_file_id')
    payment_id = data.get('payment_id')
    
    if not payment_id:
        await callback.message.edit_text(
            "❌ Ошибка: не найден платеж",
            reply_markup=main_menu_inline_kb() if callback.from_user.id not in config.ADMIN_IDS else admin_panel_inline_kb()
        )
        await state.clear()
        return
    
    # Подтверждаем платеж без комментария
    success, payment = db.confirm_payment(payment_id, screenshot_file_id, "")
    
    if success:
        # Отправляем в соответствующий канал
        if payment.get('type') == 'deposit':
            await send_to_payment_channel_with_screenshot(payment, screenshot_file_id, "")
        else:
            await send_to_order_channel_with_screenshot(payment, screenshot_file_id, "")
        
        # Уведомляем пользователя
        await callback.message.edit_text(
            f"✅ Платеж подтвержден!\n\n"
            f"💰 Сумма: {payment.get('amount', 0)}₽\n"
            f"🆔 ID транзакции: {payment_id}\n"
            f"📝 Комментарий: нет\n\n"
            f"Баланс будет зачислен после проверки администратором.\n"
            f"Обычно это занимает 5-15 минут.",
            reply_markup=main_menu_inline_kb() if callback.from_user.id not in config.ADMIN_IDS else admin_panel_inline_kb()
        )
        
        # Уведомляем админа
        try:
            admin_text = f"""
💰 Новый платеж с подтверждением!

👤 Пользователь: @{payment.get('username', 'N/A')}
🆔 User ID: {payment.get('user_id')}
💰 Сумма: {payment.get('amount', 0)}₽
💳 Способ: {payment.get('method', 'unknown')}
🆔 Транзакция: {payment_id}
✅ Подтвержден скриншотом
"""
            for admin_id in config.ADMIN_IDS:
                await bot.send_message(admin_id, admin_text)
        except Exception as e:
            print(f"Ошибка уведомления админа: {e}")
    else:
        await callback.message.edit_text(
            "❌ Ошибка при подтверждении платежа",
            reply_markup=main_menu_inline_kb() if callback.from_user.id not in config.ADMIN_IDS else admin_panel_inline_kb()
        )
    
    await state.clear()
    await callback.answer()

@dp.message(PaymentConfirmationStates.waiting_for_comment)
async def handle_payment_comment(message: Message, state: FSMContext):
    """Обработка комментария к платежу"""
    comment = message.text.strip()
    if comment == '-':
        comment = ""
    
    data = await state.get_data()
    screenshot_file_id = data.get('screenshot_file_id')
    payment_id = data.get('payment_id')
    
    if not payment_id:
        await message.answer(
            "❌ Не найден платеж для подтверждения",
            reply_markup=main_menu_reply_kb() if message.from_user.id not in config.ADMIN_IDS else admin_panel_reply_kb()
        )
        await state.clear()
        return
    
    # Подтверждаем платеж с комментарием
    success, payment = db.confirm_payment(payment_id, screenshot_file_id, comment)
    
    if success:
        # Отправляем в соответствующий канал
        if payment.get('type') == 'deposit':
            await send_to_payment_channel_with_screenshot(payment, screenshot_file_id, comment)
        else:
            await send_to_order_channel_with_screenshot(payment, screenshot_file_id, comment)
        
        # Уведомляем пользователя
        text = (
            f"✅ Платеж подтвержден!\n\n"
            f"💰 Сумма: {payment.get('amount', 0)}₽\n"
            f"🆔 ID транзакции: {payment_id}\n"
            f"📝 Ваш комментарий: {comment or 'нет'}\n\n"
        )
        
        if payment.get('type') == 'deposit':
            text += "Баланс будет зачислен после проверки администратором.\nОбычно это занимает 5-15 минут."
        else:
            text += "Заказ отправлен на обработку.\nС вами свяжутся для уточнения деталей."
        
        if message.from_user.id in config.ADMIN_IDS:
            await send_dual_keyboard_message(
                message,
                text,
                admin_panel_reply_kb(),
                admin_panel_inline_kb()
            )
        else:
            await send_dual_keyboard_message(
                message,
                text,
                main_menu_reply_kb(),
                main_menu_inline_kb()
            )
        
        # Уведомляем админа
        try:
            admin_text = f"""
💰 Новый платеж с подтверждением!

👤 Пользователь: @{payment.get('username', 'N/A')}
🆔 User ID: {payment.get('user_id')}
💰 Сумма: {payment.get('amount', 0)}₽
💳 Способ: {payment.get('method', 'unknown')}
🆔 Транзакция: {payment_id}
📝 Комментарий: {comment or 'нет'}
✅ Подтвержден скриншотом
"""
            for admin_id in config.ADMIN_IDS:
                await bot.send_message(admin_id, admin_text)
        except Exception as e:
            print(f"Ошибка уведомления админа: {e}")
    else:
        if message.from_user.id in config.ADMIN_IDS:
            await send_dual_keyboard_message(
                message,
                "❌ Ошибка при подтверждении платежа",
                admin_panel_reply_kb(),
                admin_panel_inline_kb()
            )
        else:
            await send_dual_keyboard_message(
                message,
                "❌ Ошибка при подтверждении платежа",
                main_menu_reply_kb(),
                main_menu_inline_kb()
            )
    
    await state.clear()

@dp.callback_query(F.data == 'cancel_screenshot')
async def cancel_screenshot_callback(callback: CallbackQuery, state: FSMContext):
    """Отмена загрузки скриншота"""
    user_id = callback.from_user.id
    
    # Находим pending платеж пользователя
    payment_id, payment = db.get_user_pending_payment(user_id)
    
    if payment_id:
        # Отменяем таймер
        payment_timer.cancel_timer(payment_id)
        
        # Удаляем из ожидания
        if payment_id in db.pending_payments:
            del db.pending_payments[payment_id]
            db.save_users_data()
    
    await state.clear()
    
    if user_id in config.ADMIN_IDS:
        await callback.message.edit_text(
            "❌ Подтверждение платежа отменено",
            reply_markup=admin_panel_inline_kb()
        )
    else:
        await callback.message.edit_text(
            "❌ Подтверждение платежа отменено",
            reply_markup=main_menu_inline_kb()
        )
    await callback.answer()

# ==================== ОБРАБОТКА КОРЗИНЫ И ПЛАТЕЖЕЙ ====================

@dp.callback_query(F.data == 'checkout')
async def checkout_callback(callback: CallbackQuery):
    await checkout_callback_handler(callback)

@dp.callback_query(F.data == 'checkout_balance')
async def checkout_balance_callback(callback: CallbackQuery):
    await checkout_balance_callback_handler(callback)

@dp.callback_query(F.data == 'pay_balance')
async def pay_balance_callback(callback: CallbackQuery):
    await pay_balance_callback_handler(callback)

@dp.callback_query(F.data == 'pay_sber')
async def pay_sber_callback(callback: CallbackQuery, state: FSMContext):
    """Оплата через СБП с подтверждением скриншотом"""
    await process_external_payment_callback(callback, state, "sber")

@dp.callback_query(F.data == 'pay_yoomoney')
async def pay_yoomoney_callback(callback: CallbackQuery, state: FSMContext):
    """Оплата через ЮMoney с подтверждением скриншотом"""
    await process_external_payment_callback(callback, state, "yoomoney")

@dp.callback_query(F.data == 'pay_crypto')
async def pay_crypto_callback(callback: CallbackQuery, state: FSMContext):
    """Оплата через криптовалюту с подтверждением скриншотом"""
    await process_external_payment_callback(callback, state, "crypto")

async def process_external_payment_callback(callback: CallbackQuery, state: FSMContext, method: str):
    """Общая обработка внешних платежей"""
    cart = db.get_cart(callback.from_user.id)
    
    if not cart["items"]:
        await callback.answer("Корзина пуста", show_alert=True)
        return
    
    payment_info = config.PAYMENT_DETAILS.get(method, {})
    order_id = f"ORD_{callback.from_user.id}_{int(datetime.now().timestamp())}"
    
    # Сохраняем данные заказа
    items_list = []
    for item_id, item in cart["items"].items():
        product = item["product"]
        items_list.append({
            'id': product['id'],
            'name': product['name'],
            'price': product['price'],
            'quantity': item['quantity']
        })
    
    # Создаем pending платеж для заказа
    payment_data = {
        'user_id': callback.from_user.id,
        'username': callback.from_user.username,
        'amount': cart['total'],
        'method': payment_info.get('name', method),
        'transaction_id': order_id,
        'type': 'purchase',
        'description': f"Покупка товаров на {cart['total']}₽",
        'cart_data': {
            'items': items_list,
            'total': cart['total']
        }
    }
    
    db.add_pending_payment(order_id, payment_data)
    
    # Запускаем таймер
    await payment_timer.start_timer(order_id, callback.from_user.id)
    
    text = ""
    if method == 'sber':
        text = (
            f"🏦 Оплата через {payment_info['name']}\n\n"
            f"💰 Сумма к оплате: {cart['total']}₽\n\n"
            f"📱 Номер телефона:\n"
            f"{payment_info['number']}\n\n"
            f"👤 Получатель:\n"
            f"{payment_info['owner']}\n\n"
            f"📋 Инструкция:\n"
            f"{payment_info['instruction']}\n\n"
            f"🆔 В комментарии укажите:\n"
            f"Заказ {order_id}"
        )
    elif method == 'yoomoney':
        text = (
            f"💰 Оплата через {payment_info['name']}\n\n"
            f"💰 Сумма к оплате: {cart['total']}₽\n\n"
            f"💳 Номер кошелька:\n"
            f"{payment_info['number']}\n\n"
            f"👤 Получатель:\n"
            f"{payment_info['owner']}\n\n"
            f"📋 Инструкция:\n"
            f"{payment_info['instruction']}\n\n"
            f"🆔 В комментарии укажите:\n"
            f"Заказ {order_id}"
        )
    elif method == 'crypto':
        text = (
            f"₿ Оплата через {payment_info['name']}\n\n"
            f"💰 Сумма к оплате: {cart['total']}₽\n\n"
            f"🔗 Адрес кошелька:\n"
            f"{payment_info['address']}\n\n"
            f"📋 Инструкция:\n"
            f"{payment_info['instruction']}\n\n"
            f"🆔 В комментарии укажите:\n"
            f"Заказ {order_id}"
        )
    
    text += (
        f"\n\n📸 После оплаты ОТПРАВЬТЕ СКРИНШОТ ЧЕКА в этот чат\n"
        f"⏰ У вас есть 10 минут на отправку\n"
        f"❌ По истечении времени заявка будет отменена"
    )
    
    # Сохраняем order_id в состоянии
    await state.update_data(order_id=order_id)
    
    # Переходим к ожиданию скриншота
    await state.set_state(PaymentConfirmationStates.waiting_for_screenshot)
    
    await callback.message.edit_text(
        text,
        reply_markup=screenshot_confirmation_inline_kb()
    )
    await callback.answer()

@dp.callback_query(F.data == 'confirm_balance_payment')
async def confirm_balance_payment_callback(callback: CallbackQuery):
    """Подтверждение и обработка оплаты балансом"""
    cart = db.get_cart(callback.from_user.id)
    
    discount_percent = db.settings.get("balance_discount", 10)
    discount_amount = cart['total'] * discount_percent / 100
    total_with_discount = cart['total'] - discount_amount
    
    order_id = f"ORD_{callback.from_user.id}_{int(datetime.now().timestamp())}"
    
    items_list = []
    for item_id, item in cart["items"].items():
        product = item["product"]
        items_list.append({
            'id': product['id'],
            'name': product['name'],
            'price': product['price'],
            'quantity': item['quantity']
        })
    
    success, message = db.deduct_balance(
        callback.from_user.id,
        total_with_discount,
        f"Оплата заказа {order_id} со скидкой {discount_percent}%"
    )
    
    if not success:
        await callback.answer(f"Ошибка: {message}", show_alert=True)
        return
    
    order_data = {
        'user_id': callback.from_user.id,
        'username': callback.from_user.username,
        'total': total_with_discount,
        'original_total': cart['total'],
        'discount': discount_percent,
        'discount_amount': discount_amount,
        'payment_method': 'Баланс бота',
        'order_id': order_id,
        'items': items_list,
        'balance_used': True
    }
    
    await send_to_order_channel_with_screenshot(order_data)
    
    db.clear_cart(callback.from_user.id)
    
    text = (
        f"✅ Заказ успешно оплачен с баланса!\n\n"
        f"🆔 Номер заказа: {order_id}\n"
        f"💰 Сумма: {total_with_discount:.2f}₽\n"
        f"🎁 Скидка: {discount_percent}% (-{discount_amount:.2f}₽)\n"
        f"📦 Товаров: {len(items_list)} позиций\n\n"
        f"📋 Заказ отправлен на обработку.\n"
        f"С вами свяжутся для уточнения деталей.\n\n"
        f"💳 Остаток баланса: {db.get_user_balance(callback.from_user.id)}₽"
    )
    
    await callback.message.edit_text(
        text,
        reply_markup=main_menu_inline_kb()
    )
    await callback.answer()

# ==================== АДМИН-ПАНЕЛЬ КОЛБЭКИ ====================

@dp.callback_query(F.data == 'admin_panel')
async def admin_panel_callback(callback: CallbackQuery):
    await admin_panel_callback_handler(callback)

@dp.callback_query(F.data == 'admin_products')
async def admin_products_callback(callback: CallbackQuery):
    await admin_products_callback_handler(callback)

@dp.callback_query(F.data == 'admin_categories')
async def admin_categories_callback(callback: CallbackQuery):
    await admin_categories_callback_handler(callback)

@dp.callback_query(F.data == 'admin_filters')
async def admin_filters_callback(callback: CallbackQuery):
    await admin_filters_callback_handler(callback)

@dp.callback_query(F.data == 'admin_users')
async def admin_users_callback(callback: CallbackQuery):
    await admin_users_callback_handler(callback)

@dp.callback_query(F.data == 'admin_settings')
async def admin_settings_callback(callback: CallbackQuery):
    await admin_settings_callback_handler(callback)

@dp.callback_query(F.data == 'admin_stats')
async def admin_stats_callback(callback: CallbackQuery):
    await admin_stats_callback_handler(callback)

@dp.callback_query(F.data == 'admin_pending_payments')
async def admin_pending_payments_callback(callback: CallbackQuery):
    await admin_pending_payments_callback_handler(callback)

@dp.callback_query(F.data == 'admin_add_product')
async def admin_add_product_callback(callback: CallbackQuery, state: FSMContext):
    await admin_add_product_callback_handler(callback, state)

@dp.callback_query(F.data == 'admin_edit_product')
async def admin_edit_product_callback(callback: CallbackQuery, state: FSMContext):
    await admin_edit_product_callback_handler(callback, state)

@dp.callback_query(F.data == 'admin_delete_product')
async def admin_delete_product_callback(callback: CallbackQuery, state: FSMContext):
    await admin_delete_product_callback_handler(callback, state)

@dp.callback_query(F.data == 'admin_list_products')
async def admin_list_products_callback(callback: CallbackQuery):
    await admin_list_products_callback_handler(callback)

# ==================== ОБРАБОТЧИКИ ФИЛЬТРОВ ====================

@dp.callback_query(F.data == 'admin_add_filter')
async def admin_add_filter_callback(callback: CallbackQuery, state: FSMContext):
    """Добавление фильтра"""
    if callback.from_user.id not in config.ADMIN_IDS:
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    
    categories = db.get_categories()
    if not categories:
        await callback.answer("Сначала добавьте категории", show_alert=True)
        return
    
    await state.set_state(FilterStates.waiting_for_category_for_filter)
    
    builder = InlineKeyboardBuilder()
    for category in categories:
        builder.row(
            InlineKeyboardButton(
                text=category["name"],
                callback_data=f"add_filter_category_{category['id']}"
            )
        )
    builder.row(InlineKeyboardButton(text='❌ Отмена', callback_data='admin_filters'))
    
    await callback.message.edit_text(
        "➕ Добавление нового фильтра/тега\n\n"
        "Выберите категорию для фильтра:",
        reply_markup=builder.as_markup()
    )
    await callback.answer()

@dp.callback_query(F.data.startswith('add_filter_category_'))
async def admin_add_filter_category_callback(callback: CallbackQuery, state: FSMContext):
    """Выбор категории для добавления фильтра"""
    if callback.from_user.id not in config.ADMIN_IDS:
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    
    category_id = int(callback.data.split('_')[3])
    await state.update_data(category_id=category_id)
    await state.set_state(FilterStates.waiting_for_filter_name)
    
    category = db.get_category(category_id)
    
    await callback.message.edit_text(
        f"➕ Добавление фильтра для категории: {category['name']}\n\n"
        "Введите название фильтра/тега/подкатегории:\n\n"
        "Примеры:\n"
        "• 'Для Instagram'\n"
        "• 'Логотипы'\n"
        "• 'Статьи'\n"
        "• 'Дизайн баннеров'",
        reply_markup=cancel_inline_kb()
    )
    await callback.answer()

@dp.callback_query(F.data == 'admin_list_filters')
async def admin_list_filters_callback(callback: CallbackQuery):
    """Список всех фильтров"""
    if callback.from_user.id not in config.ADMIN_IDS:
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    
    filters = db.filters
    categories = {cat['id']: cat['name'] for cat in db.categories}
    
    if not filters:
        text = "📭 Фильтров пока нет"
    else:
        text = "🏷️ Список всех фильтров:\n\n"
        
        for filter_item in filters:
            category_name = categories.get(filter_item['category_id'], 'Неизвестно')
            # Подсчитываем товары с этим фильтром
            products_with_filter = len([
                p for p in db.products 
                if "filter_ids" in p and filter_item['id'] in p["filter_ids"]
            ])
            
            text += f"🆔 ID: {filter_item['id']}\n"
            text += f"🏷️ Название: {filter_item['name']}\n"
            text += f"📁 Категория: {category_name}\n"
            text += f"📦 Товаров: {products_with_filter} шт.\n"
            text += "─" * 30 + "\n\n"
    
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text='➕ Добавить фильтр', callback_data='admin_add_filter'),
        InlineKeyboardButton(text='🔙 Назад', callback_data='admin_filters')
    )
    
    await callback.message.edit_text(text, reply_markup=builder.as_markup())
    await callback.answer()

@dp.callback_query(F.data == 'admin_edit_filter')
async def admin_edit_filter_callback(callback: CallbackQuery, state: FSMContext):
    """Редактирование фильтра"""
    if callback.from_user.id not in config.ADMIN_IDS:
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    
    await state.set_state(FilterStates.waiting_for_filter_id)
    
    await callback.message.edit_text(
        "✏️ Редактирование фильтра\n\n"
        "Введите ID фильтра для редактирования:",
        reply_markup=cancel_inline_kb()
    )
    await callback.answer()

@dp.callback_query(F.data == 'admin_delete_filter')
async def admin_delete_filter_callback(callback: CallbackQuery, state: FSMContext):
    """Удаление фильтра"""
    if callback.from_user.id not in config.ADMIN_IDS:
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    
    filters = db.filters
    if not filters:
        await callback.answer("Нет фильтров для удаления", show_alert=True)
        return
    
    builder = InlineKeyboardBuilder()
    for filter_item in filters:
        category = db.get_category(filter_item['category_id'])
        category_name = category['name'] if category else 'Неизвестно'
        
        builder.row(
            InlineKeyboardButton(
                text=f"{filter_item['name']} ({category_name})",
                callback_data=f"delete_filter_{filter_item['id']}"
            )
        )
    
    builder.row(InlineKeyboardButton(text='🔙 Назад', callback_data='admin_filters'))
    
    await callback.message.edit_text(
        "❌ Удаление фильтра\n\n"
        "Выберите фильтр для удаления:",
        reply_markup=builder.as_markup()
    )
    await callback.answer()

@dp.callback_query(F.data.startswith('delete_filter_'))
async def admin_delete_filter_confirm_callback(callback: CallbackQuery):
    """Подтверждение удаления фильтра"""
    if callback.from_user.id not in config.ADMIN_IDS:
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    
    filter_id = int(callback.data.split('_')[2])
    filter_item = db.get_filter(filter_id)
    
    if not filter_item:
        await callback.answer("Фильтр не найден", show_alert=True)
        return
    
    category = db.get_category(filter_item['category_id'])
    
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text='✅ Да, удалить', callback_data=f'confirm_delete_filter_{filter_id}'),
        InlineKeyboardButton(text='❌ Нет, отмена', callback_data='admin_filters')
    )
    
    await callback.message.edit_text(
        f"⚠️ Подтверждение удаления фильтра\n\n"
        f"🏷️ Фильтр: {filter_item['name']}\n"
        f"📁 Категория: {category['name'] if category else 'Неизвестно'}\n"
        f"🆔 ID: {filter_id}\n\n"
        f"⚠️ Внимание! Этот фильтр будет убран у всех товаров.\n\n"
        f"Вы уверены, что хотите удалить этот фильтр?",
        reply_markup=builder.as_markup()
    )
    await callback.answer()

@dp.callback_query(F.data.startswith('confirm_delete_filter_'))
async def confirm_delete_filter_callback(callback: CallbackQuery):
    """Подтверждение удаления фильтра"""
    if callback.from_user.id not in config.ADMIN_IDS:
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    
    filter_id = int(callback.data.split('_')[3])
    filter_item = db.get_filter(filter_id)
    
    if not filter_item:
        await callback.answer("Фильтр не найден", show_alert=True)
        return
    
    success = db.delete_filter(filter_id)
    
    if success:
        await callback.message.edit_text(
            f"✅ Фильтр удален!\n\n"
            f"🏷️ Название: {filter_item['name']}\n"
            f"🆔 ID: {filter_id}",
            reply_markup=admin_filters_inline_kb()
        )
    else:
        await callback.message.edit_text(
            "❌ Ошибка при удалении фильтра",
            reply_markup=admin_filters_inline_kb()
        )
    await callback.answer()

@dp.callback_query(F.data == 'admin_assign_filter')
async def admin_assign_filter_callback(callback: CallbackQuery, state: FSMContext):
    """Назначение фильтра товару"""
    if callback.from_user.id not in config.ADMIN_IDS:
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    
    await state.set_state(AssignFilterStates.waiting_for_product_id)
    
    await callback.message.edit_text(
        "🏷️ Назначение фильтра товару\n\n"
        "Введите ID товара, которому хотите назначить фильтр:",
        reply_markup=cancel_inline_kb()
    )
    await callback.answer()

@dp.callback_query(F.data == 'admin_remove_filter')
async def admin_remove_filter_callback(callback: CallbackQuery, state: FSMContext):
    """Удаление фильтра у товара"""
    if callback.from_user.id not in config.ADMIN_IDS:
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    
    await state.set_state(AssignFilterStates.waiting_for_product_id)
    
    await callback.message.edit_text(
        "🗑️ Удаление фильтра у товара\n\n"
        "Введите ID товара, у которого хотите убрать фильтр:",
        reply_markup=cancel_inline_kb()
    )
    await callback.answer()

@dp.callback_query(F.data.startswith('admin_add_product_cat_'))
async def admin_add_product_to_category_callback(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in config.ADMIN_IDS:
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    
    category_id = int(callback.data.split('_')[4])
    
    await state.update_data(category_id=category_id)
    await state.set_state(AddProductStates.waiting_for_name)
    
    category = db.get_category(category_id)
    
    await callback.message.edit_text(
        f"➕ Добавление товара в категорию: {category['name']}\n\n"
        "Введите название товара:",
        reply_markup=cancel_inline_kb()
    )
    await callback.answer()

@dp.callback_query(F.data == 'admin_change_discount')
async def admin_change_discount_callback(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in config.ADMIN_IDS:
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return

    await state.set_state(AdminConfigStates.waiting_for_discount)

    await callback.message.edit_text(
        "🎁 Изменение скидки при оплате с баланса\n\n"
        f"Текущая скидка: {db.settings.get('balance_discount', 10)}%\n\n"
        "Введите новое значение скидки (от 0 до 50%):",
        reply_markup=cancel_inline_kb()
    )
    await callback.answer()

@dp.callback_query(F.data == 'admin_change_limits')
async def admin_change_limits_callback(callback: CallbackQuery):
    if callback.from_user.id not in config.ADMIN_IDS:
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return

    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text='💰 Изменить минимальную сумму', callback_data='change_min_deposit'),
        InlineKeyboardButton(text='📈 Изменить максимальную сумму', callback_data='change_max_deposit')
    )
    builder.row(
        InlineKeyboardButton(text='🔙 Назад', callback_data='admin_settings')
    )

    await callback.message.edit_text(
        f"💰 Изменение лимитов пополнения\n\n"
        f"Текущие лимиты:\n"
        f"• Минимальная сумма: {db.settings.get('min_deposit', 100)}₽\n"
        f"• Максимальная сумма: {db.settings.get('max_deposit', 50000)}₽\n\n"
        f"Выберите, что изменить:",
        reply_markup=builder.as_markup()
    )
    await callback.answer()

@dp.callback_query(F.data == 'change_min_deposit')
async def change_min_deposit_callback(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in config.ADMIN_IDS:
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return

    await state.set_state(AdminConfigStates.waiting_for_min_deposit)

    await callback.message.edit_text(
        "💰 Изменение минимальной суммы пополнения\n\n"
        f"Текущее значение: {db.settings.get('min_deposit', 100)}₽\n\n"
        "Введите новую минимальную сумму (в рублях):",
        reply_markup=cancel_inline_kb()
    )
    await callback.answer()

@dp.callback_query(F.data == 'change_max_deposit')
async def change_max_deposit_callback(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in config.ADMIN_IDS:
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return

    await state.set_state(AdminConfigStates.waiting_for_max_deposit)

    await callback.message.edit_text(
        "📈 Изменение максимальной суммы пополнения\n\n"
        f"Текущее значение: {db.settings.get('max_deposit', 50000)}₽\n\n"
        "Введите новую максимальную сумму (в рублях):",
        reply_markup=cancel_inline_kb()
    )
    await callback.answer()

@dp.callback_query(F.data == 'admin_view_settings')
async def admin_view_settings_callback(callback: CallbackQuery):
    if callback.from_user.id not in config.ADMIN_IDS:
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return

    settings = db.get_settings()

    text = "⚙️ Все настройки бота:\n\n"

    for key, value in settings.items():
        if key == 'balance_discount':
            text += f"🎁 {key}: {value}%\n"
        elif 'deposit' in key:
            text += f"💰 {key}: {value}₽\n"
        else:
            text += f"📋 {key}: {value}\n"

    text += f"\n⏰ Таймаут скриншота: {config.SCREENSHOT_TIMEOUT} сек. ({config.SCREENSHOT_TIMEOUT/60:.1f} мин.)\n"
    text += f"\n📊 ID каналов:\n"
    text += f"• PAYMENT_CHANNEL_ID: {config.PAYMENT_CHANNEL_ID}\n"
    text += f"• ORDER_CHANNEL_ID: {config.ORDER_CHANNEL_ID}\n"
    text += f"• SUPPORT_CHANNEL_ID: {config.SUPPORT_CHANNEL_ID}\n"
    text += f"\n💳 Платежные реквизиты:\n"
    
    for method, details in config.PAYMENT_DETAILS.items():
        text += f"• {method}: {details.get('name')}\n"

    await callback.message.edit_text(
        text,
        reply_markup=admin_settings_inline_kb()
    )
    await callback.answer()

@dp.callback_query(F.data == 'admin_user_stats')
async def admin_user_stats_callback(callback: CallbackQuery):
    """Детальная статистика пользователей"""
    if callback.from_user.id not in config.ADMIN_IDS:
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    
    users = db.users
    if not users:
        text = "📭 Пользователей пока нет"
    else:
        text = "👥 Детальная статистика пользователей:\n\n"
        
        sorted_users = sorted(users.items(), key=lambda x: x[1].get("balance", 0), reverse=True)
        
        for i, (user_id, user_data) in enumerate(sorted_users[:20], 1):
            balance = user_data.get("balance", 0)
            orders = user_data.get("total_orders", 0)
            spent = user_data.get("total_spent", 0)
            reg_date = datetime.fromisoformat(user_data.get("registration_date", datetime.now().isoformat())).strftime('%d.%m.%Y')
            
            text += f"{i}. ID: {user_id}\n"
            text += f"   💰 Баланс: {balance}₽\n"
            text += f"   💸 Потрачено: {spent}₽\n"
            text += f"   📦 Заказов: {orders}\n"
            text += f"   📅 Регистрация: {reg_date}\n\n"
    
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text='🔙 Назад', callback_data='admin_users')
    )
    
    await callback.message.edit_text(text, reply_markup=builder.as_markup())
    await callback.answer()

# ==================== ОБРАБОТЧИКИ ПОДТВЕРЖДЕНИ
# ==================== ОБРАБОТЧИКИ ПОДТВЕРЖДЕНИЯ ЗАЯВОК ====================

@dp.callback_query(F.data.startswith('confirm_deposit_'))
async def confirm_deposit_callback(callback: CallbackQuery):
    """Обработка подтверждения пополнения"""
    if callback.from_user.id not in config.ADMIN_IDS:
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    
    transaction_id = callback.data.replace('confirm_deposit_', '')
    
    # Ищем транзакцию в истории
    transaction = None
    for trans in db.transactions:
        if trans.get('type') == 'deposit' and trans.get('description', '').find(transaction_id) != -1:
            transaction = trans
            break
    
    if not transaction:
        await callback.answer("❌ Транзакция не найдена", show_alert=True)
        return
    
    # Обновляем сообщение в канале
    try:
        if callback.message.photo:
            await bot.edit_message_caption(
                chat_id=config.PAYMENT_CHANNEL_ID,
                message_id=callback.message.message_id,
                caption=callback.message.caption + f"\n\n✅ ПОДТВЕРЖДЕНО АДМИНИСТРАТОРОМ: @{callback.from_user.username}",
                reply_markup=None
            )
        else:
            await bot.edit_message_text(
                chat_id=config.PAYMENT_CHANNEL_ID,
                message_id=callback.message.message_id,
                text=callback.message.text + f"\n\n✅ ПОДТВЕРЖДЕНО АДМИНИСТРАТОРОМ: @{callback.from_user.username}",
                reply_markup=None
            )
    except Exception as e:
        print(f"Ошибка обновления сообщения: {e}")
    
    # Уведомляем пользователя
    user_id = transaction['user_id']
    try:
        await bot.send_message(
            user_id,
            f"✅ Ваше пополнение подтверждено администратором!\n\n"
            f"💰 Сумма: {transaction['amount']}₽\n"
            f"🆔 ID транзакции: {transaction_id}\n"
            f"💳 Текущий баланс: {db.get_user_balance(user_id)}₽"
        )
    except Exception as e:
        print(f"Ошибка уведомления пользователя: {e}")
        await callback.answer(f"Пользователь не смог получить уведомление. Ошибка: {str(e)}", show_alert=True)
    
    await callback.answer("✅ Пополнение подтверждено")

@dp.callback_query(F.data.startswith('reject_deposit_'))
async def reject_deposit_callback(callback: CallbackQuery):
    """Обработка отклонения пополнения"""
    if callback.from_user.id not in config.ADMIN_IDS:
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    
    transaction_id = callback.data.replace('reject_deposit_', '')
    
    # Ищем транзакцию в истории
    transaction = None
    for trans in db.transactions:
        if trans.get('type') == 'deposit' and trans.get('description', '').find(transaction_id) != -1:
            transaction = trans
            break
    
    if not transaction:
        await callback.answer("❌ Транзакция не найдена", show_alert=True)
        return
    
    # Обновляем сообщение в канале
    try:
        if callback.message.photo:
            await bot.edit_message_caption(
                chat_id=config.PAYMENT_CHANNEL_ID,
                message_id=callback.message.message_id,
                caption=callback.message.caption + f"\n\n❌ ОТКЛОНЕНО АДМИНИСТРАТОРОМ: @{callback.from_user.username}",
                reply_markup=None
            )
        else:
            await bot.edit_message_text(
                chat_id=config.PAYMENT_CHANNEL_ID,
                message_id=callback.message.message_id,
                text=callback.message.text + f"\n\n❌ ОТКЛОНЕНО АДМИНИСТРАТОРОМ: @{callback.from_user.username}",
                reply_markup=None
            )
    except Exception as e:
        print(f"Ошибка обновления сообщения: {e}")
    
    # Уведомляем пользователя
    user_id = transaction['user_id']
    try:
        await bot.send_message(
            user_id,
            f"❌ Ваше пополнение отклонено администратором!\n\n"
            f"💰 Сумма: {transaction['amount']}₽\n"
            f"🆔 ID транзакции: {transaction_id}\n\n"
            f"💳 Если у вас есть вопросы, обратитесь в поддержку: @{config.ADMIN_USERNAME.lstrip('@')}"
        )
    except Exception as e:
        print(f"Ошибка уведомления пользователя: {e}")
        await callback.answer(f"Пользователь не смог получить уведомление. Ошибка: {str(e)}", show_alert=True)
    
    await callback.answer("❌ Пополнение отклонено")

@dp.callback_query(F.data.startswith('confirm_order_'))
async def confirm_order_callback(callback: CallbackQuery):
    """Обработка подтверждения заказа"""
    if callback.from_user.id not in config.ADMIN_IDS:
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    
    order_id = callback.data.replace('confirm_order_', '')
    
    # Ищем заказ в истории
    order = None
    for trans in db.transactions:
        if trans.get('type') == 'purchase' and trans.get('description', '').find(order_id) != -1:
            order = trans
            break
    
    if not order:
        await callback.answer("❌ Заказ не найден", show_alert=True)
        return
    
    # Обновляем сообщение в канале
    try:
        if callback.message.photo:
            await bot.edit_message_caption(
                chat_id=config.ORDER_CHANNEL_ID,
                message_id=callback.message.message_id,
                caption=callback.message.caption + f"\n\n✅ ПОДТВЕРЖДЕНО АДМИНИСТРАТОРОМ: @{callback.from_user.username}",
                reply_markup=None
            )
        else:
            await bot.edit_message_text(
                chat_id=config.ORDER_CHANNEL_ID,
                message_id=callback.message.message_id,
                text=callback.message.text + f"\n\n✅ ПОДТВЕРЖДЕНО АДМИНИСТРАТОРОМ: @{callback.from_user.username}",
                reply_markup=None
            )
    except Exception as e:
        print(f"Ошибка обновления сообщения: {e}")
    
    # Уведомляем пользователя
    user_id = order['user_id']
    try:
        await bot.send_message(
            user_id,
            f"✅ Ваш заказ подтвержден администратором!\n\n"
            f"🆔 Номер заказа: {order_id}\n"
            f"💰 Сумма: {abs(order['amount'])}₽\n"
            f"📦 Товары отправлены в ЛС\n\n"
            f"💳 Если возникнут вопросы, обратитесь в поддержку: @{config.ADMIN_USERNAME.lstrip('@')}"
        )
    except Exception as e:
        print(f"Ошибка уведомления пользователя: {e}")
        await callback.answer(f"Пользователь не смог получить уведомление. Ошибка: {str(e)}", show_alert=True)
    
    await callback.answer("✅ Заказ подтверждено")

@dp.callback_query(F.data.startswith('reject_order_'))
async def reject_order_callback(callback: CallbackQuery):
    """Обработка отклонения заказа"""
    if callback.from_user.id not in config.ADMIN_IDS:
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    
    order_id = callback.data.replace('reject_order_', '')
    
    # Ищем заказ в истории
    order = None
    for trans in db.transactions:
        if trans.get('type') == 'purchase' and trans.get('description', '').find(order_id) != -1:
            order = trans
            break
    
    if not order:
        await callback.answer("❌ Заказ не найден", show_alert=True)
        return
    
    # Возвращаем деньги, если оплата была с баланса
    user_id = order['user_id']
    if order.get('payment_method') == 'Баланс бота':
        db.add_balance(
            user_id,
            abs(order['amount']),
            f"Возврат средств по отмененному заказу {order_id}"
        )
    
    # Обновляем сообщение в канале
    try:
        if callback.message.photo:
            await bot.edit_message_caption(
                chat_id=config.ORDER_CHANNEL_ID,
                message_id=callback.message.message_id,
                caption=callback.message.caption + f"\n\n❌ ОТКЛОНЕНО АДМИНИСТРАТОРОМ: @{callback.from_user.username}",
                reply_markup=None
            )
        else:
            await bot.edit_message_text(
                chat_id=config.ORDER_CHANNEL_ID,
                message_id=callback.message.message_id,
                text=callback.message.text + f"\n\n❌ ОТКЛОНЕНО АДМИНИСТРАТОРОМ: @{callback.from_user.username}",
                reply_markup=None
            )
    except Exception as e:
        print(f"Ошибка обновления сообщения: {e}")
    
    # Уведомляем пользователя
    try:
        message_text = f"❌ Ваш заказ отклонен администратором!\n\n🆔 Номер заказа: {order_id}"
        
        if order.get('payment_method') == 'Баланс бота':
            message_text += f"\n💰 Средства возвращены на баланс"
            message_text += f"\n💳 Текущий баланс: {db.get_user_balance(user_id)}₽"
        
        message_text += f"\n\n💳 Если у вас есть вопросы, обратитесь в поддержку: @{config.ADMIN_USERNAME.lstrip('@')}"
        
        await bot.send_message(user_id, message_text)
    except Exception as e:
        print(f"Ошибка уведомления пользователя: {e}")
        await callback.answer(f"Пользователь не смог получить уведомление. Ошибка: {str(e)}", show_alert=True)
    
    await callback.answer("❌ Заказ отклонено")

@dp.callback_query(F.data.startswith('reject_with_reason_'))
async def reject_with_reason_callback(callback: CallbackQuery, state: FSMContext):
    """Отклонение с указанием причины"""
    if callback.from_user.id not in config.ADMIN_IDS:
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    
    data_parts = callback.data.split('_')
    reject_type = data_parts[3]  # 'deposit' или 'order'
    transaction_id = '_'.join(data_parts[4:])
    
    await state.update_data(
        reject_type=reject_type,
        transaction_id=transaction_id,
        message_id=callback.message.message_id,
        chat_id=callback.message.chat.id
    )
    
    await state.set_state(AdminRejectStates.waiting_for_reject_reason)
    
    await callback.message.answer(
        "📝 Укажите причину отклонения:\n\n"
        "Примеры:\n"
        "• 'Неверная сумма'\n"
        "• 'Скриншот не читается'\n"
        "• 'Оплата не поступила'\n"
        "• 'Технические проблемы'",
        reply_markup=cancel_inline_kb()
    )
    await callback.answer()

@dp.message(AdminRejectStates.waiting_for_reject_reason)
async def process_reject_reason(message: Message, state: FSMContext):
    """Обработка причины отклонения"""
    if message.from_user.id not in config.ADMIN_IDS:
        await state.clear()
        return
    
    reason = message.text.strip()
    if not reason:
        await message.answer("❌ Причина не может быть пустой. Введите причину:", reply_markup=cancel_inline_kb())
        return
    
    data = await state.get_data()
    reject_type = data.get('reject_type')
    transaction_id = data.get('transaction_id')
    message_id = data.get('message_id')
    chat_id = data.get('chat_id')
    
    # Обновляем сообщение в канале
    try:
        # Получаем сообщение
        if chat_id == config.PAYMENT_CHANNEL_ID:
            channel_message = await bot.get_message(chat_id, message_id)
            if channel_message.photo:
                await bot.edit_message_caption(
                    chat_id=chat_id,
                    message_id=message_id,
                    caption=channel_message.caption + f"\n\n❌ ОТКЛОНЕНО АДМИНИСТРАТОРОМ: @{message.from_user.username}\n📝 Причина: {reason}",
                    reply_markup=None
                )
            else:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=channel_message.text + f"\n\n❌ ОТКЛОНЕНО АДМИНИСТРАТОРОМ: @{message.from_user.username}\n📝 Причина: {reason}",
                    reply_markup=None
                )
        else:
            channel_message = await bot.get_message(chat_id, message_id)
            if channel_message.photo:
                await bot.edit_message_caption(
                    chat_id=chat_id,
                    message_id=message_id,
                    caption=channel_message.caption + f"\n\n❌ ОТКЛОНЕНО АДМИНИСТРАТОРОМ: @{message.from_user.username}\n📝 Причина: {reason}",
                    reply_markup=None
                )
            else:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=channel_message.text + f"\n\n❌ ОТКЛОНЕНО АДМИНИСТРАТОРОМ: @{message.from_user.username}\n📝 Причина: {reason}",
                    reply_markup=None
                )
    except Exception as e:
        print(f"Ошибка обновления сообщения: {e}")
    
    # Находим пользователя
    user_id = None
    if reject_type == 'deposit':
        for trans in db.transactions:
            if trans.get('type') == 'deposit' and trans.get('description', '').find(transaction_id) != -1:
                user_id = trans['user_id']
                break
    else:
        for trans in db.transactions:
            if trans.get('type') == 'purchase' and trans.get('description', '').find(transaction_id) != -1:
                user_id = trans['user_id']
                # Возвращаем деньги, если оплата была с баланса
                if trans.get('payment_method') == 'Баланс бота':
                    db.add_balance(
                        user_id,
                        abs(trans['amount']),
                        f"Возврат средств по отмененному заказу {transaction_id}"
                    )
                break
    
    # Уведомляем пользователя
    if user_id:
        try:
            if reject_type == 'deposit':
                message_text = f"❌ Ваше пополнение отклонено администратором!\n\n🆔 ID транзакции: {transaction_id}\n📝 Причина: {reason}\n\n💳 Если у вас есть вопросы, обратитесь в поддержку: @{config.ADMIN_USERNAME.lstrip('@')}"
            else:
                message_text = f"❌ Ваш заказ отклонен администратором!\n\n🆔 Номер заказа: {transaction_id}\n📝 Причина: {reason}"
                if trans.get('payment_method') == 'Баланс бota':
                    message_text += f"\n💰 Средства возвращены на баланс"
                    message_text += f"\n💳 Текущий баланс: {db.get_user_balance(user_id)}₽"
                message_text += f"\n\n💳 Если у вас есть вопросы, обратитесь в поддержку: @{config.ADMIN_USERNAME.lstrip('@')}"
            
            await bot.send_message(user_id, message_text)
        except Exception as e:
            print(f"Ошибка уведомления пользователя: {e}")
    
    await message.answer(f"✅ Заявка отклонена с причиной: {reason}")
    await state.clear()

# ==================== ОБРАБОТЧИКИ СОСТОЯНИЙ ====================

@dp.message(AddCategoryStates.waiting_for_category_name)
async def process_category_name(message: Message, state: FSMContext):
    if message.from_user.id not in config.ADMIN_IDS:
        await state.clear()
        return
    
    category_name = message.text.strip()
    
    if len(category_name) < 2:
        await message.answer(
            "❌ Название категории слишком короткое. Введите название еще раз:",
            reply_markup=cancel_reply_kb()
        )
        return
    
    category_id = db.add_category(category_name)
    
    await state.clear()
    
    await send_dual_keyboard_message(
        message,
        f"✅ Категория добавлена!\n\n📁 Название: {category_name}\n🆔 ID: {category_id}",
        admin_categories_reply_kb(),
        admin_categories_inline_kb()
    )

@dp.message(AddProductStates.waiting_for_name)
async def process_product_name(message: Message, state: FSMContext):
    if message.from_user.id not in config.ADMIN_IDS:
        await state.clear()
        return
    
    if not message.text or len(message.text.strip()) < 2:
        await message.answer("❌ Название слишком короткое. Введите название товара:", reply_markup=cancel_reply_kb())
        return
    
    await state.update_data(name=message.text.strip())
    await state.set_state(AddProductStates.waiting_for_price)
    
    await message.answer(
        "✅ Название сохранено!\n\n"
        "Теперь введите цену товара (только число):\n"
        "Пример: 1500 или 2999.99",
        reply_markup=cancel_reply_kb()
    )

@dp.message(AddProductStates.waiting_for_price)
async def process_product_price(message: Message, state: FSMContext):
    if message.from_user.id not in config.ADMIN_IDS:
        await state.clear()
        return
    
    try:
        price = float(message.text.strip().replace(',', '.'))
        if price <= 0:
            raise ValueError
    except:
        await message.answer(
            "❌ Неверная цена! Введите число больше 0:\n"
            "Пример: 1500",
            reply_markup=cancel_reply_kb()
        )
        return
    
    await state.update_data(price=price)
    await state.set_state(AddProductStates.waiting_for_quantity)
    
    await message.answer(
        f"✅ Цена сохранена: {price}₽\n\n"
        "Теперь введите количество товара (целое число):\n"
        "Пример: 10 или 9999 для неограниченного количества",
        reply_markup=cancel_reply_kb()
    )

@dp.message(AddProductStates.waiting_for_quantity)
async def process_product_quantity(message: Message, state: FSMContext):
    if message.from_user.id not in config.ADMIN_IDS:
        await state.clear()
        return
    
    try:
        quantity = int(message.text.strip())
        if quantity < 0:
            raise ValueError
    except:
        await message.answer(
            "❌ Неверное количество! Введите целое число больше или равное 0:\n"
            "Пример: 10",
            reply_markup=cancel_reply_kb()
        )
        return
    
    await state.update_data(quantity=quantity)
    await state.set_state(AddProductStates.waiting_for_description)
    
    await message.answer(
        f"✅ Количество сохранено: {quantity} шт.\n\n"
        "Теперь введите описание товара:\n"
        "(или отправьте - чтобы пропустить)",
        reply_markup=cancel_reply_kb()
    )

@dp.message(AddProductStates.waiting_for_description)
async def process_product_description(message: Message, state: FSMContext):
    if message.from_user.id not in config.ADMIN_IDS:
        await state.clear()
        return
    
    description = message.text.strip()
    if description == "-":
        description = ""
    
    await state.update_data(description=description)
    
    data = await state.get_data()
    
    category = db.get_category(data['category_id'])
    
    confirmation_text = (
        "📋 Подтвердите данные товара:\n\n"
        f"📁 Категория: {category['name']}\n"
        f"📦 Название: {data['name']}\n"
        f"💰 Цена: {data['price']}₽\n"
        f"📊 Количество: {data['quantity']} шт.\n"
        f"📝 Описание: {data['description'] or 'Без описания'}\n\n"
        "Все верно?"
    )
    
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text='✅ Да, добавить', callback_data='confirm_add_product'),
        InlineKeyboardButton(text='❌ Нет, отмена', callback_data='cancel_add_product')
    )
    
    await message.answer(
        confirmation_text,
        reply_markup=builder.as_markup()
    )

@dp.callback_query(F.data == 'confirm_add_product')
async def confirm_add_product_callback(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in config.ADMIN_IDS:
        await callback.answer("⛔ Нет доступа", show_alert=True)
        await state.clear()
        return
    
    data = await state.get_data()
    
    product_id = db.add_product(
        category_id=data['category_id'],
        name=data['name'],
        price=data['price'],
        description=data['description'],
        quantity=data['quantity']
    )
    
    await state.clear()
    
    await callback.message.edit_text(
        f"✅ Товар успешно добавлен!\n\n"
        f"📦 Название: {data['name']}\n"
        f"💰 Цена: {data['price']}₽\n"
        f"📊 Количество: {data['quantity']} шт.\n"
        f"🆔 ID товара: {product_id}",
        reply_markup=admin_products_inline_kb()
    )
    await callback.answer()

@dp.callback_query(F.data == 'cancel_add_product')
async def cancel_add_product_callback(callback: CallbackQuery, state: FSMContext):
    """Отмена добавления товара"""
    await state.clear()
    await callback.message.edit_text(
        "❌ Добавление товара отменено",
        reply_markup=admin_products_inline_kb()
    )
    await callback.answer()

@dp.message(FilterStates.waiting_for_filter_name)
async def process_filter_name(message: Message, state: FSMContext):
    """Обработка названия фильтра"""
    if message.from_user.id not in config.ADMIN_IDS:
        await state.clear()
        return
    
    filter_name = message.text.strip()
    
    if len(filter_name) < 2:
        await message.answer(
            "❌ Название фильтра слишком короткое. Введите еще раз:",
            reply_markup=cancel_reply_kb()
        )
        return
    
    data = await state.get_data()
    category_id = data.get('category_id')
    
    if not category_id:
        await message.answer("❌ Ошибка данных. Начните заново.", reply_markup=admin_filters_reply_kb())
        await state.clear()
        return
    
    filter_id = db.add_filter(category_id, filter_name)
    category = db.get_category(category_id)
    
    await state.clear()
    
    await send_dual_keyboard_message(
        message,
        f"✅ Фильтр добавлен!\n\n"
        f"🏷️ Название: {filter_name}\n"
        f"📁 Категория: {category['name']}\n"
        f"🆔 ID фильтра: {filter_id}",
        admin_filters_reply_kb(),
        admin_filters_inline_kb()
    )

@dp.message(FilterStates.waiting_for_filter_id)
async def process_edit_filter_id(message: Message, state: FSMContext):
    """Обработка ID фильтра для редактирования"""
    if message.from_user.id not in config.ADMIN_IDS:
        await state.clear()
        return
    
    try:
        filter_id = int(message.text.strip())
        filter_item = db.get_filter(filter_id)
        
        if not filter_item:
            await message.answer(
                f"❌ Фильтр с ID {filter_id} не найден. Введите правильный ID:",
                reply_markup=cancel_reply_kb()
            )
            return
        
        await state.update_data(filter_id=filter_id)
        await state.set_state(FilterStates.waiting_for_new_filter_name)
        
        category = db.get_category(filter_item['category_id'])
        
        await message.answer(
            f"✏️ Редактирование фильтра\n\n"
            f"🏷️ Текущее название: {filter_item['name']}\n"
            f"📁 Категория: {category['name']}\n"
            f"🆔 ID: {filter_id}\n\n"
            f"Введите новое название фильтра:",
            reply_markup=cancel_reply_kb()
        )
        
    except ValueError:
        await message.answer(
            "❌ Неверный ID! Введите числовой ID фильтра:",
            reply_markup=cancel_reply_kb()
        )

@dp.message(FilterStates.waiting_for_new_filter_name)
async def process_new_filter_name(message: Message, state: FSMContext):
    """Обработка нового названия фильтра"""
    if message.from_user.id not in config.ADMIN_IDS:
        await state.clear()
        return
    
    new_name = message.text.strip()
    
    if len(new_name) < 2:
        await message.answer(
            "❌ Название фильтра слишком короткое. Введите еще раз:",
            reply_markup=cancel_reply_kb()
        )
        return
    
    data = await state.get_data()
    filter_id = data.get('filter_id')
    
    if not filter_id:
        await message.answer("❌ Ошибка данных. Начните заново.", reply_markup=admin_filters_reply_kb())
        await state.clear()
        return
    
    filter_item = db.get_filter(filter_id)
    if not filter_item:
        await message.answer("❌ Фильтр не найден", reply_markup=admin_filters_reply_kb())
        await state.clear()
        return
    
    old_name = filter_item['name']
    success = db.update_filter(filter_id, new_name)
    
    if success:
        await send_dual_keyboard_message(
            message,
            f"✅ Фильтр обновлен!\n\n"
            f"🏷️ Старое название: {old_name}\n"
            f"🏷️ Новое название: {new_name}",
            admin_filters_reply_kb(),
            admin_filters_inline_kb()
        )
    else:
        await send_dual_keyboard_message(
            message,
            "❌ Ошибка при обновлении фильтра",
            admin_filters_reply_kb(),
            admin_filters_inline_kb()
        )
    
    await state.clear()

@dp.message(EditCategoryStates.waiting_for_new_name)
async def process_category_edit(message: Message, state: FSMContext):
    if message.from_user.id not in config.ADMIN_IDS:
        await state.clear()
        return
    
    new_name = message.text.strip()
    
    if len(new_name) < 2:
        await message.answer(
            "❌ Название категории слишком короткое. Введите новое название еще раз:",
            reply_markup=cancel_reply_kb()
        )
        return
    
    data = await state.get_data()
    category_id = data.get('category_id')
    
    if not category_id:
        await message.answer("❌ Ошибка данных. Начните заново.", reply_markup=admin_categories_reply_kb())
        await state.clear()
        return
    
    category = db.get_category(category_id)
    if not category:
        await message.answer("❌ Категория не найдена", reply_markup=admin_categories_reply_kb())
        await state.clear()
        return
    
    success = db.update_category(category_id, new_name)
    
    if success:
        await send_dual_keyboard_message(
            message,
            f"✅ Категория обновлена!\n\n📁 Старое название: {category['name']}\n📁 Новое название: {new_name}\n🆔 ID: {category_id}",
            admin_categories_reply_kb(),
            admin_categories_inline_kb()
        )
    else:
        await send_dual_keyboard_message(
            message,
            "❌ Ошибка при обновлении категории",
            admin_categories_reply_kb(),
            admin_categories_inline_kb()
        )
    
    await state.clear()

@dp.message(DeleteProductStates.waiting_for_product_id)
async def process_delete_product(message: Message, state: FSMContext):
    if message.from_user.id not in config.ADMIN_IDS:
        await state.clear()
        return
    
    try:
        product_id = int(message.text.strip())
        product = db.get_product(product_id)
        
        if not product:
            await message.answer(
                f"❌ Товар с ID {product_id} не найден. Введите правильный ID:",
                reply_markup=cancel_reply_kb()
            )
            return
        
        success = db.delete_product(product_id)
        
        if success:
            await send_dual_keyboard_message(
                message,
                f"✅ Товар удален!\n\n📦 Название: {product['name']}\n🆔 ID: {product_id}",
                admin_products_reply_kb(),
                admin_products_inline_kb()
            )
        else:
            await send_dual_keyboard_message(
                message,
                "❌ Ошибка при удалении товара",
                admin_products_reply_kb(),
                admin_products_inline_kb()
            )
        
        await state.clear()
    except ValueError:
        await message.answer(
            "❌ Неверный ID! Введите числовой ID товара:",
            reply_markup=cancel_reply_kb()
        )

@dp.message(EditProductStates.waiting_for_product_id)
async def process_edit_product_id(message: Message, state: FSMContext):
    if message.from_user.id not in config.ADMIN_IDS:
        await state.clear()
        return
    
    try:
        product_id = int(message.text.strip())
        product = db.get_product(product_id)
        
        if not product:
            await message.answer(
                f"❌ Товар с ID {product_id} не найден. Введите правильный ID:",
                reply_markup=cancel_reply_kb()
            )
            return
        
        await state.update_data(product_id=product_id)
        
        category = db.get_category(product["category_id"])
        
        text = (
            f"✏️ Редактирование товара\n\n"
            f"📦 Товар: {product['name']}\n"
            f"🆔 ID: {product_id}\n"
            f"📁 Категория: {category['name'] if category else 'Не указана'}\n\n"
            f"Текущие данные:\n"
            f"• Название: {product['name']}\n"
            f"• Цена: {product['price']}₽\n"
            f"• Количество: {product.get('quantity', 9999)} шт.\n"
            f"• Описание: {product.get('description', 'Нет описания')}\n\n"
            f"Выберите поле для редактирования:"
        )
        
        builder = InlineKeyboardBuilder()
        builder.row(
            InlineKeyboardButton(text='📝 Название', callback_data='edit_field_name'),
            InlineKeyboardButton(text='💰 Цена', callback_data='edit_field_price')
        )
        builder.row(
            InlineKeyboardButton(text='📊 Количество', callback_data='edit_field_quantity'),
            InlineKeyboardButton(text='📝 Описание', callback_data='edit_field_description')
        )
        builder.row(
            InlineKeyboardButton(text='🔙 Назад', callback_data='admin_products')
        )
        
        await message.answer(
            text,
            reply_markup=builder.as_markup()
        )
        
        await state.set_state(EditProductStates.waiting_for_edit_field)
    except ValueError:
        await message.answer(
            "❌ Неверный ID! Введите числовой ID товара:",
            reply_markup=cancel_reply_kb()
        )

@dp.callback_query(F.data.startswith('edit_field_'))
async def process_edit_field_callback(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in config.ADMIN_IDS:
        await callback.answer("⛔ Нет доступа", show_alert=True)
        await state.clear()
        return
    
    field = callback.data.replace('edit_field_', '')
    field_names = {
        'name': 'название',
        'price': 'цену',
        'quantity': 'количество',
        'description': 'описание'
    }
    
    if field not in field_names:
        await callback.answer("Неизвестное поле", show_alert=True)
        return
    
    await state.update_data(edit_field=field)
    await state.set_state(EditProductStates.waiting_for_edit_value)
    
    await callback.message.edit_text(
        f"Введите новое {field_names[field]} товара:\n\n"
        f"Для отмены нажмите ❌ Отмена",
        reply_markup=cancel_inline_kb()
    )
    await callback.answer()

@dp.message(EditProductStates.waiting_for_edit_value)
async def process_edit_value(message: Message, state: FSMContext):
    """Обработка ввода нового значения для редактирования товара"""
    if message.from_user.id not in config.ADMIN_IDS:
        await state.clear()
        return
    
    data = await state.get_data()
    product_id = data.get('product_id')
    field = data.get('edit_field')
    
    if not product_id or not field:
        await message.answer("❌ Ошибка данных. Начните заново.", reply_markup=admin_products_reply_kb())
        await state.clear()
        return
    
    product = db.get_product(product_id)
    if not product:
        await message.answer("❌ Товар не найден", reply_markup=admin_products_reply_kb())
        await state.clear()
        return
    
    new_value = message.text.strip()
    
    try:
        if field == 'name':
            if len(new_value) < 2:
                await message.answer("❌ Название слишком короткое. Введите еще раз:", reply_markup=cancel_reply_kb())
                return
            update_data = {'name': new_value}
            
        elif field == 'price':
            try:
                price = float(new_value.replace(',', '.'))
                if price <= 0:
                    raise ValueError
                update_data = {'price': price}
            except:
                    await message.answer("❌ Неверная цена. Введите число больше 0:", reply_markup=cancel_reply_kb())
            return
                
        elif field == 'quantity':
            try:
                quantity = int(new_value)
                if quantity < 0:
                    raise ValueError
                update_data = {'quantity': quantity}
            except:
                await message.answer("❌ Неверное количество. Введите целое число >= 0:", reply_markup=cancel_reply_kb())
                return
                
        elif field == 'description':
            update_data = {'description': new_value}
            
        else:
            await message.answer("❌ Неизвестное поле", reply_markup=admin_products_reply_kb())
            await state.clear()
            return
        
        success = db.update_product(product_id, **update_data)
        
        if success:
            field_names = {
                'name': 'Название',
                'price': 'Цена',
                'quantity': 'Количество',
                'description': 'Описание'
            }
            
            await send_dual_keyboard_message(
                message,
                f"✅ Товар обновлен!\n\n{field_names[field]}: {new_value}",
                admin_products_reply_kb(),
                admin_products_inline_kb()
            )
        else:
            await send_dual_keyboard_message(
                message,
                "❌ Ошибка при обновлении товара",
                admin_products_reply_kb(),
                admin_products_inline_kb()
            )
    
    except Exception as e:
        await send_dual_keyboard_message(
            message,
            f"❌ Ошибка: {str(e)}",
            admin_products_reply_kb(),
            admin_products_inline_kb()
        )
    
    await state.clear()

@dp.message(AdminConfigStates.waiting_for_discount)
async def process_discount_change(message: Message, state: FSMContext):
    """Обработка изменения скидки"""
    if message.from_user.id not in config.ADMIN_IDS:
        await state.clear()
        return
    
    try:
        discount = float(message.text.strip().replace(',', '.'))
        if discount < 0 or discount > 50:
            await message.answer(
                "❌ Скидка должна быть от 0 до 50%\n"
                "Введите значение еще раз:",
                reply_markup=cancel_reply_kb()
            )
            return
        
        db.update_settings(balance_discount=discount)
        
        await send_dual_keyboard_message(
            message,
            f"✅ Скидка изменена!\n\n🎁 Новая скидка: {discount}%\nПрименяется при оплате с баланса",
            admin_settings_reply_kb(),
            admin_settings_inline_kb()
        )
        
    except ValueError:
        await message.answer(
            "❌ Неверный формат! Введите число от 0 до 50:\n"
            "Пример: 10 или 15.5",
            reply_markup=cancel_reply_kb()
        )
    
    await state.clear()

@dp.message(AdminConfigStates.waiting_for_min_deposit)
async def process_min_deposit_change(message: Message, state: FSMContext):
    """Обработка изменения минимального депозита"""
    if message.from_user.id not in config.ADMIN_IDS:
        await state.clear()
        return
    
    try:
        min_deposit = float(message.text.strip().replace(',', '.'))
        if min_deposit < 1:
            await message.answer(
                "❌ Минимальная сумма должна быть больше 0\n"
                "Введите значение еще раз:",
                reply_markup=cancel_reply_kb()
            )
            return
        
        db.update_settings(min_deposit=min_deposit)
        
        await send_dual_keyboard_message(
            message,
            f"✅ Минимальный депозит изменен!\n\n💰 Новая минимальная сумма: {min_deposit}₽",
            admin_settings_reply_kb(),
            admin_settings_inline_kb()
        )
        
    except ValueError:
        await message.answer(
            "❌ Неверный формат! Введите число:\n"
            "Пример: 100 или 500",
            reply_markup=cancel_reply_kb()
        )
    
    await state.clear()

@dp.message(AdminConfigStates.waiting_for_max_deposit)
async def process_max_deposit_change(message: Message, state: FSMContext):
    """Обработка изменения максимального депозита"""
    if message.from_user.id not in config.ADMIN_IDS:
        await state.clear()
        return
    
    try:
        max_deposit = float(message.text.strip().replace(',', '.'))
        if max_deposit < 1:
            await message.answer(
                "❌ Максимальная сумма должна быть больше 0\n"
                "Введите значение еще раз:",
                reply_markup=cancel_reply_kb()
            )
            return
        
        db.update_settings(max_deposit=max_deposit)
        
        await send_dual_keyboard_message(
            message,
            f"✅ Максимальный депозит изменен!\n\n💰 Новая максимальная сумма: {max_deposit}₽",
            admin_settings_reply_kb(),
            admin_settings_inline_kb()
        )
        
    except ValueError:
        await message.answer(
            "❌ Неверный формат! Введите число:\n"
            "Пример: 50000 или 100000",
            reply_markup=cancel_reply_kb()
        )
    
    await state.clear()

@dp.message(DepositStates.waiting_for_amount)
async def process_deposit_amount(message: Message, state: FSMContext):
    try:
        amount = float(message.text.strip().replace(',', '.'))
        min_deposit = db.settings.get('min_deposit', 100)
        max_deposit = db.settings.get('max_deposit', 50000)
        
        if amount < min_deposit:
            await message.answer(
                f"❌ Сумма слишком мала! Минимальная сумма: {min_deposit}₽\n"
                f"Введите сумму еще раз:",
                reply_markup=cancel_reply_kb()
            )
            return
        
        if amount > max_deposit:
            await message.answer(
                f"❌ Сумма слишком велика! Максимальная сумма: {max_deposit}₽\n"
                f"Введите сумму еще раз:",
                reply_markup=cancel_reply_kb()
            )
            return
        
        await state.update_data(amount=amount)
        await state.set_state(DepositStates.waiting_for_payment_method)
        
        text = (
            f"✅ Сумма: {amount}₽\n\n"
            "Выберите способ оплаты:\n\n"
            "⚠️ После оплаты отправьте скриншот чека"
        )
        
        await send_dual_keyboard_message(
            message,
            text,
            payment_methods_reply_kb(),
            deposit_methods_inline_kb()
        )
        
    except ValueError:
        await message.answer(
            "❌ Неверный формат суммы! Введите число:\n"
            "Пример: 1000 или 1500.50",
            reply_markup=cancel_reply_kb()
        )

@dp.message(AssignFilterStates.waiting_for_product_id)
async def process_assign_filter_product_id(message: Message, state: FSMContext):
    """Обработка ID товара для назначения фильтра"""
    if message.from_user.id not in config.ADMIN_IDS:
        await state.clear()
        return
    
    try:
        product_id = int(message.text.strip())
        product = db.get_product(product_id)
        
        if not product:
            await message.answer(
                f"❌ Товар с ID {product_id} не найден. Введите правильный ID:",
                reply_markup=cancel_reply_kb()
            )
            return
        
        # Получаем доступные фильтры для этого товара
        available_filters = db.get_available_filters_for_product(product_id)
        
        if not available_filters:
            category = db.get_category(product['category_id'])
            await message.answer(
                f"❌ Для категории '{category['name']}' нет доступных фильтров.\n"
                f"Сначала добавьте фильтры для этой категории.",
                reply_markup=admin_filters_reply_kb()
            )
            await state.clear()
            return
        
        await state.update_data(product_id=product_id)
        await state.set_state(AssignFilterStates.waiting_for_filter_selection)
        
        builder = InlineKeyboardBuilder()
        
        # Группируем фильтры по 2 в ряд
        for i in range(0, len(available_filters), 2):
            row_filters = available_filters[i:i+2]
            buttons = []
            for filter_item in row_filters:
                # Проверяем, есть ли уже этот фильтр у товара
                has_filter = "filter_ids" in product and filter_item['id'] in product["filter_ids"]
                emoji = "✅" if has_filter else "⬜"
                buttons.append(
                    InlineKeyboardButton(
                        text=f"{emoji} {filter_item['name']}",
                        callback_data=f"select_filter_{filter_item['id']}"
                    )
                )
            builder.row(*buttons)
        
        builder.row(InlineKeyboardButton(text='🔙 Отмена', callback_data='admin_filters'))
        
        category = db.get_category(product['category_id'])
        
        await message.answer(
            f"🏷️ Выберите фильтры для товара:\n\n"
            f"📦 Товар: {product['name']}\n"
            f"📁 Категория: {category['name']}\n\n"
            f"✅ - уже назначен\n⬜ - можно назначить\n\n"
            f"Выберите фильтры:",
            reply_markup=builder.as_markup()
        )
        
    except ValueError:
        await message.answer(
            "❌ Неверный ID! Введите числовой ID товара:",
            reply_markup=cancel_reply_kb()
        )

@dp.callback_query(F.data.startswith('select_filter_'))
async def process_select_filter_callback(callback: CallbackQuery, state: FSMContext):
    """Выбор фильтра для назначения товару"""
    if callback.from_user.id not in config.ADMIN_IDS:
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    
    filter_id = int(callback.data.split('_')[2])
    data = await state.get_data()
    product_id = data.get('product_id')
    
    if not product_id:
        await callback.answer("❌ Ошибка данных", show_alert=True)
        return
    
    product = db.get_product(product_id)
    filter_item = db.get_filter(filter_id)
    
    if not product or not filter_item:
        await callback.answer("❌ Товар или фильтр не найден", show_alert=True)
        return
    
    # Проверяем, есть ли уже этот фильтр у товара
    has_filter = "filter_ids" in product and filter_id in product["filter_ids"]
    
    if has_filter:
        # Убираем фильтр
        success = db.remove_filter_from_product(product_id, filter_id)
        if success:
            await callback.answer(f"❌ Фильтр '{filter_item['name']}' убран", show_alert=True)
        else:
            await callback.answer(f"❌ Ошибка при удалении фильтра", show_alert=True)
    else:
        # Добавляем фильтр
        success = db.assign_filter_to_product(product_id, filter_id)
        if success:
            await callback.answer(f"✅ Фильтр '{filter_item['name']}' назначен", show_alert=True)
        else:
            await callback.answer(f"❌ Ошибка при назначении фильтра", show_alert=True)
    
    # Обновляем список фильтров
    product = db.get_product(product_id)  # Обновляем данные
    available_filters = db.get_available_filters_for_product(product_id)
    
    builder = InlineKeyboardBuilder()
    
    for i in range(0, len(available_filters), 2):
        row_filters = available_filters[i:i+2]
        buttons = []
        for f in row_filters:
            has_filter_now = "filter_ids" in product and f['id'] in product["filter_ids"]
            emoji = "✅" if has_filter_now else "⬜"
            buttons.append(
                InlineKeyboardButton(
                    text=f"{emoji} {f['name']}",
                    callback_data=f"select_filter_{f['id']}"
                )
            )
        builder.row(*buttons)
    
    builder.row(InlineKeyboardButton(text='🔙 Готово', callback_data='admin_filters'))
    
    category = db.get_category(product['category_id'])
    
    await callback.message.edit_text(
        f"🏷️ Выберите фильтры для товара:\n\n"
        f"📦 Товар: {product['name']}\n"
        f"📁 Категория: {category['name']}\n\n"
        f"✅ - уже назначен\n⬜ - можно назначить\n\n"
        f"Выберите фильтры:",
        reply_markup=builder.as_markup()
    )
    await callback.answer()

# ==================== ОБРАБОТКА ТЕКСТОВЫХ СООБЩЕНИЙ ====================

@dp.message(F.text & ~F.command)
async def handle_text_message(message: Message, state: FSMContext):
    """Обработка текстовых сообщений"""
    current_state = await state.get_state()
    
    # Если не в состоянии FSM, показываем главное меню
    if not current_state:
        # Если это не FSM состояние, проверяем, может это ID товара для удаления/редактирования
        if message.from_user.id in config.ADMIN_IDS:
            # Проверяем, не пытается ли администратор удалить/редактировать товар через сообщение
            try:
                # Проверяем, не является ли сообщение числом (ID товара)
                product_id = int(message.text.strip())
                product = db.get_product(product_id)
                
                if product:
                    # Если товар найден, предлагаем действия
                    category = db.get_category(product["category_id"])
                    
                    builder = InlineKeyboardBuilder()
                    builder.row(
                        InlineKeyboardButton(text='✏️ Редактировать', callback_data=f'edit_product_{product_id}'),
                        InlineKeyboardButton(text='❌ Удалить', callback_data=f'delete_product_{product_id}')
                    )
                    builder.row(
                        InlineKeyboardButton(text='🔙 Отмена', callback_data='admin_products')
                    )
                    
                    await message.answer(
                        f"📦 Найден товар:\n\n"
                        f"Название: {product['name']}\n"
                        f"Цена: {product['price']}₽\n"
                        f"Категория: {category['name'] if category else 'Не указана'}\n\n"
                        f"Выберите действие:",
                        reply_markup=builder.as_markup()
                    )
                    return
            except ValueError:
                pass  # Не число, продолжаем как обычное сообщение
        
        # Для обычных пользователей показываем главное меню
        if message.from_user.id in config.ADMIN_IDS:
            await send_dual_keyboard_message(
                message,
                "👨‍💼 Вы администратор. Используйте кнопки меню или команды для управления ботом.",
                admin_panel_reply_kb(),
                admin_panel_inline_kb()
            )
        else:
            await send_dual_keyboard_message(
                message,
                "👋 Используйте кнопки меню для навигации:",
                main_menu_reply_kb(),
                main_menu_inline_kb()
            )
        return

# ==================== ДОПОЛНИТЕЛЬНЫЕ ОБРАБОТЧИКИ ====================

@dp.callback_query(F.data == 'cancel')
async def cancel_callback(callback: CallbackQuery, state: FSMContext):
    """Отмена текущей операции"""
    await state.clear()
    if callback.from_user.id in config.ADMIN_IDS:
        await callback.message.edit_text(
            "❌ Операция отменена",
            reply_markup=admin_panel_inline_kb()
        )
    else:
        await callback.message.edit_text(
            "❌ Операция отменена",
            reply_markup=main_menu_inline_kb()
        )
    await callback.answer()

@dp.callback_query(F.data.startswith('edit_product_'))
async def edit_product_direct_callback(callback: CallbackQuery, state: FSMContext):
    """Прямое редактирование товара из меню"""
    if callback.from_user.id not in config.ADMIN_IDS:
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    
    product_id = int(callback.data.split('_')[2])
    product = db.get_product(product_id)
    
    if not product:
        await callback.answer("Товар не найден", show_alert=True)
        return
    
    await state.update_data(product_id=product_id)
    
    category = db.get_category(product["category_id"])
    
    text = (
        f"✏️ Редактирование товара\n\n"
        f"📦 Товар: {product['name']}\n"
        f"🆔 ID: {product_id}\n"
        f"📁 Категория: {category['name'] if category else 'Не указана'}\n\n"
        f"Текущие данные:\n"
        f"• Название: {product['name']}\n"
        f"• Цена: {product['price']}₽\n"
        f"• Количество: {product.get('quantity', 9999)} шт.\n"
        f"• Описание: {product.get('description', 'Нет описания')}\n\n"
        f"Выберите поле для редактирования:"
    )
    
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text='📝 Название', callback_data='edit_field_name'),
        InlineKeyboardButton(text='💰 Цена', callback_data='edit_field_price')
    )
    builder.row(
        InlineKeyboardButton(text='📊 Количество', callback_data='edit_field_quantity'),
        InlineKeyboardButton(text='📝 Описание', callback_data='edit_field_description')
    )
    builder.row(
        InlineKeyboardButton(text='🔙 Назад', callback_data='admin_products')
    )
    
    await callback.message.edit_text(
        text,
        reply_markup=builder.as_markup()
    )
    
    await state.set_state(EditProductStates.waiting_for_edit_field)
    await callback.answer()

@dp.callback_query(F.data.startswith('delete_product_'))
async def delete_product_direct_callback(callback: CallbackQuery):
    """Прямое удаление товара из меню"""
    if callback.from_user.id not in config.ADMIN_IDS:
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    
    product_id = int(callback.data.split('_')[2])
    product = db.get_product(product_id)
    
    if not product:
        await callback.answer("Товар не найден", show_alert=True)
        return
    
    category = db.get_category(product["category_id"])
    
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text='✅ Да, удалить', callback_data=f'confirm_delete_product_{product_id}'),
        InlineKeyboardButton(text='❌ Нет, отмена', callback_data='admin_products')
    )
    
    await callback.message.edit_text(
        f"⚠️ Подтверждение удаления товара\n\n"
        f"📦 Товар: {product['name']}\n"
        f"💰 Цена: {product['price']}₽\n"
        f"📁 Категория: {category['name'] if category else 'Не указана'}\n\n"
        f"Вы уверены, что хотите удалить этот товар?",
        reply_markup=builder.as_markup()
    )
    await callback.answer()

@dp.callback_query(F.data.startswith('confirm_delete_product_'))
async def confirm_delete_product_direct_callback(callback: CallbackQuery):
    """Подтверждение прямого удаления товара"""
    if callback.from_user.id not in config.ADMIN_IDS:
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    
    product_id = int(callback.data.split('_')[3])
    product = db.get_product(product_id)
    
    if not product:
        await callback.answer("Товар не найден", show_alert=True)
        return
    
    success = db.delete_product(product_id)
    
    if success:
        await callback.message.edit_text(
            f"✅ Товар удален!\n\n"
            f"📦 Название: {product['name']}\n"
            f"🆔 ID: {product_id}",
            reply_markup=admin_products_inline_kb()
        )
    else:
        await callback.message.edit_text(
            "❌ Ошибка при удалении товара",
            reply_markup=admin_products_inline_kb()
        )
    await callback.answer()

# ==================== ОБРАБОТЧИК ВОЗВРАТА В МЕНЮ ====================

@dp.callback_query(F.data.in_(['admin_products', 'admin_categories', 'admin_filters', 'admin_panel', 'main_menu']))
async def clear_state_on_menu_change(callback: CallbackQuery, state: FSMContext):
    """Очищаем состояние при переходе в другое меню"""
    current_state = await state.get_state()
    if current_state:
        await state.clear()
    
    # Перенаправляем на соответствующий обработчик
    if callback.data == 'admin_products':
        await admin_products_callback(callback)
    elif callback.data == 'admin_categories':
        await admin_categories_callback(callback)
    elif callback.data == 'admin_filters':
        await admin_filters_callback(callback)
    elif callback.data == 'admin_panel':
        await admin_panel_callback(callback)
    elif callback.data == 'main_menu':
        await main_menu_callback(callback)

# ==================== ОБРАБОТЧИК ДЛЯ НЕИЗВЕСТНЫХ КОЛБЭКОВ ====================

@dp.callback_query()
async def unknown_callback(callback: CallbackQuery):
    """Обработчик для неизвестных колбэков"""
    print(f"⚠️ Неизвестный колбэк: {callback.data}")
    await callback.answer("⚠️ Эта кнопка еще не настроена или произошла ошибка", show_alert=True)

# ==================== ГЛОБАЛЬНЫЙ ОБРАБОТЧИК ОШИБОК ====================

@dp.errors()
async def errors_handler(update, exception):
    """Глобальный обработчик ошибок"""
    print(f"❌ Ошибка: {exception}")
    return True

# ==================== ЗАПУСК БОТА ====================

async def main():
    print("=" * 50)
    print("✅ Бот запущен...")
    print(f"👨‍💼 Администраторы: {config.ADMIN_IDS}")
    print(f"💰 Продавец: {config.ADMIN_USERNAME}")
    print(f"📊 Загружено товаров: {len(db.products)}")
    print(f"📁 Загружено категорий: {len(db.categories)}")
    print(f"🏷️ Загружено фильтров: {len(db.filters)}")
    print(f"👥 Загружено пользователей: {len(db.users)}")
    print(f"⏳ Ожидающих платежей: {len(db.pending_payments)}")
    print(f"💳 Скидка при оплате балансом: {db.settings.get('balance_discount', 10)}%")
    print(f"📈 Минимальное пополнение: {db.settings.get('min_deposit', 100)}₽")
    print(f"📉 Максимальное пополнение: {db.settings.get('max_deposit', 50000)}₽")
    print(f"⏰ Таймаут скриншота: {config.SCREENSHOT_TIMEOUT} сек.")
    print("\n📊 Каналы для заявок:")
    print(f"• Оплата: {config.PAYMENT_CHANNEL_ID}")
    print(f"• Заказы: {config.ORDER_CHANNEL_ID}")
    print(f"• Поддержка: {config.SUPPORT_CHANNEL_ID}")
    print("=" * 50)
    print("\n🎯 СИСТЕМА ФИЛЬТРОВ АКТИВИРОВАНА")
    print("🏷️ Теперь можно создавать фильтры для категорий")
    print("📦 Назначать фильтры товарам вручную")
    print("🔍 Пользователи смогут фильтровать товары")
    print("\n📸 Система подтверждения скриншотом АКТИВИРОВАНА")
    print("⏰ Таймер 10 минут включен для всех платежей")
    print("🎯 Реальные кнопки + инлайн кнопки включены")
    print("✨ Красивый и опрятный интерфейс готов!")
    print("=" * 50)
    
    try:
        await dp.start_polling(bot)
    except Exception as e:
        print(f"❌ Ошибка запуска бота: {e}")
    finally:
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())
