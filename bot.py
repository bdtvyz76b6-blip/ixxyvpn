import os
import asyncio
import logging
import threading
import base64
import uuid
from datetime import datetime, timedelta, timezone

import requests
import psycopg2
from psycopg2.extras import RealDictCursor
from flask import Flask, request, jsonify

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    LabeledPrice,
    PreCheckoutQuery,
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramRetryAfter,
)


# =========================================================
# IXYY VPN — ONE FILE BOT
# =========================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

log = logging.getLogger("ixxy")


# =========================================================
# CONFIG
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

ADMIN_IDS = {
    int(x.strip())
    for x in os.getenv("ADMIN_IDS", "").split(",")
    if x.strip().isdigit()
}

# CasheRa
CASHERA_API_KEY = os.getenv("CASHERA_API_KEY", "").strip()
CASHERA_API_SECRET = os.getenv("CASHERA_API_SECRET", "").strip()
CASHERA_MERCHANT_ID = os.getenv("CASHERA_MERCHANT_ID", "").strip()

CASHERA_URL = "https://api.cashera.cash/api/v1"

PORT = int(os.getenv("PORT", "8080"))

PUBLIC_SITE_URL = os.getenv(
    "PUBLIC_SITE_URL",
    "https://ixxyweb.onrender.com"
).rstrip("/")

SUBSCRIPTION_PREFIX = os.getenv(
    "SUBSCRIPTION_PREFIX",
    "2ix847xy"
)

# GitHub
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "").strip()

GITHUB_OWNER = os.getenv(
    "GITHUB_OWNER",
    "bdtvyz76b6-blip"
)

GITHUB_REPO = os.getenv(
    "GITHUB_REPO",
    "vpn-sub"
)

GITHUB_BRANCH = os.getenv(
    "GITHUB_BRANCH",
    "main"
)

SERVERS_FILE = os.getenv(
    "SERVERS_FILE",
    "servers.txt"
)

NO_SERVERS_FILE = os.getenv(
    "NO_SERVERS_FILE",
    "no_servers.txt"
)

CASHERA_CALLBACK_URL = os.getenv(
    "CASHERA_CALLBACK_URL",
    f"{PUBLIC_SITE_URL}/webhook/cashera"
)

CASHERA_SUCCESS_URL = os.getenv(
    "CASHERA_SUCCESS_URL",
    "https://t.me"
)

CASHERA_FAIL_URL = os.getenv(
    "CASHERA_FAIL_URL",
    "https://t.me"
)

IXXY_API_SECRET = os.getenv(
    "IXXY_API_SECRET",
    ""
).strip()


if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN не задан")

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL не задан")


# =========================================================
# TIME
# =========================================================

def utc_now():
    return datetime.now(timezone.utc)


def normalize_dt(value):
    if value is None:
        return None

    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)

    return value


def fmt_date(value):
    value = normalize_dt(value)

    if not value:
        return "—"

    return value.strftime("%d.%m.%Y %H:%M")


# =========================================================
# DATABASE
# =========================================================

def db():
    return psycopg2.connect(
        DATABASE_URL,
        cursor_factory=RealDictCursor
    )


def init_db():
    with db() as conn:
        with conn.cursor() as cur:

            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id BIGINT PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    subscription BOOLEAN DEFAULT FALSE,
                    subscription_until TIMESTAMPTZ,
                    subscription_link TEXT,
                    uuid TEXT,
                    trial_used BOOLEAN DEFAULT FALSE,
                    pending_days INTEGER DEFAULT 0,
                    notify BOOLEAN DEFAULT TRUE,
                    accepted_terms BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    subscription_content TEXT
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS payments (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    payment_id TEXT,
                    external_id TEXT,
                    amount INTEGER NOT NULL,
                    days INTEGER NOT NULL,
                    status TEXT DEFAULT 'pending',
                    provider TEXT,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    paid_at TIMESTAMPTZ
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS promocodes (
                    id SERIAL PRIMARY KEY,
                    code TEXT UNIQUE NOT NULL,
                    days INTEGER NOT NULL,
                    active BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS promocode_uses (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    promo_id INTEGER NOT NULL,
                    used_at TIMESTAMPTZ DEFAULT NOW(),
                    UNIQUE(user_id, promo_id)
                )
            """)

            cur.execute("""
                ALTER TABLE users
                ADD COLUMN IF NOT EXISTS subscription BOOLEAN DEFAULT FALSE
            """)

            cur.execute("""
                ALTER TABLE users
                ADD COLUMN IF NOT EXISTS subscription_until TIMESTAMPTZ
            """)

            cur.execute("""
                ALTER TABLE users
                ADD COLUMN IF NOT EXISTS subscription_link TEXT
            """)

            cur.execute("""
                ALTER TABLE users
                ADD COLUMN IF NOT EXISTS subscription_content TEXT
            """)

            cur.execute("""
                ALTER TABLE users
                ADD COLUMN IF NOT EXISTS uuid TEXT
            """)

            cur.execute("""
                ALTER TABLE users
                ADD COLUMN IF NOT EXISTS trial_used BOOLEAN DEFAULT FALSE
            """)

            cur.execute("""
                ALTER TABLE users
                ADD COLUMN IF NOT EXISTS pending_days INTEGER DEFAULT 0
            """)

            cur.execute("""
                ALTER TABLE users
                ADD COLUMN IF NOT EXISTS notify BOOLEAN DEFAULT TRUE
            """)

            cur.execute("""
                ALTER TABLE users
                ADD COLUMN IF NOT EXISTS accepted_terms BOOLEAN DEFAULT FALSE
            """)

            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_users_username
                ON users(username)
            """)

            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_payments_user
                ON payments(user_id)
            """)

            cur.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_payment_id_unique
                ON payments(payment_id)
                WHERE payment_id IS NOT NULL
            """)

        conn.commit()

    log.info("Database initialized")


def ensure_user(tg_user):
    with db() as conn:
        with conn.cursor() as cur:

            cur.execute(
                "SELECT user_id FROM users WHERE user_id=%s",
                (tg_user.id,)
            )

            exists = cur.fetchone()

            if exists:
                cur.execute("""
                    UPDATE users
                    SET username=%s,
                        first_name=%s
                    WHERE user_id=%s
                """, (
                    tg_user.username,
                    tg_user.first_name,
                    tg_user.id
                ))

            else:
                cur.execute("""
                    INSERT INTO users (
                        user_id,
                        username,
                        first_name,
                        subscription,
                        trial_used,
                        pending_days,
                        notify,
                        accepted_terms
                    )
                    VALUES (
                        %s,%s,%s,FALSE,FALSE,0,TRUE,FALSE
                    )
                """, (
                    tg_user.id,
                    tg_user.username,
                    tg_user.first_name
                ))

        conn.commit()


def get_user(user_id):
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM users WHERE user_id=%s",
                (user_id,)
            )
            return cur.fetchone()


def get_all_users():
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT *
                FROM users
                ORDER BY created_at DESC
            """)
            return cur.fetchall()


def count_users():
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) AS count FROM users"
            )
            return cur.fetchone()["count"]


def subscription_active(user):
    if not user:
        return False

    if not user.get("subscription"):
        return False

    until = normalize_dt(
        user.get("subscription_until")
    )

    if not until:
        return False

    return until > utc_now()


def expire_old_subscriptions():
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE users
                SET subscription=FALSE
                WHERE subscription=TRUE
                  AND subscription_until IS NOT NULL
                  AND subscription_until <= NOW()
            """)

        conn.commit()


def extend_subscription(user_id, days):
    days = int(days)

    if days <= 0:
        return None

    with db() as conn:
        with conn.cursor() as cur:

            cur.execute("""
                SELECT subscription_until
                FROM users
                WHERE user_id=%s
                FOR UPDATE
            """, (user_id,))

            user = cur.fetchone()

            if not user:
                return None

            current = normalize_dt(
                user["subscription_until"]
            )

            current_now = utc_now()

            if current and current > current_now:
                new_until = current + timedelta(days=days)
            else:
                new_until = current_now + timedelta(days=days)

            cur.execute("""
                UPDATE users
                SET subscription=TRUE,
                    subscription_until=%s
                WHERE user_id=%s
            """, (
                new_until,
                user_id
            ))

        conn.commit()

    return new_until


def disable_subscription(user_id):
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE users
                SET subscription=FALSE
                WHERE user_id=%s
            """, (user_id,))
        conn.commit()


# =========================================================
# PAYMENTS
# =========================================================

def create_payment(
    user_id,
    payment_id,
    amount,
    days,
    provider,
    external_id=None
):
    with db() as conn:
        with conn.cursor() as cur:

            cur.execute("""
                INSERT INTO payments (
                    user_id,
                    payment_id,
                    external_id,
                    amount,
                    days,
                    status,
                    provider
                )
                VALUES (%s,%s,%s,%s,%s,'pending',%s)
                ON CONFLICT (payment_id)
                DO NOTHING
                RETURNING id
            """, (
                user_id,
                payment_id,
                external_id,
                amount,
                days,
                provider
            ))

            result = cur.fetchone()

        conn.commit()

    return result


def get_payment(payment_id):
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT *
                FROM payments
                WHERE payment_id=%s
                LIMIT 1
            """, (payment_id,))
            return cur.fetchone()


def process_paid_payment(payment_id):
    with db() as conn:
        with conn.cursor() as cur:

            cur.execute("""
                SELECT *
                FROM payments
                WHERE payment_id=%s
                FOR UPDATE
            """, (payment_id,))

            payment = cur.fetchone()

            if not payment:
                return None

            if payment["status"] == "paid":
                return {
                    "already_paid": True,
                    "user_id": payment["user_id"],
                    "days": payment["days"],
                    "payment_id": payment_id,
                    "subscription_until": None
                }

            user_id = payment["user_id"]
            days = payment["days"]

            cur.execute("""
                SELECT subscription_until
                FROM users
                WHERE user_id=%s
                FOR UPDATE
            """, (user_id,))

            user = cur.fetchone()

            if not user:
                return None

            current = normalize_dt(
                user["subscription_until"]
            )

            current_now = utc_now()

            if current and current > current_now:
                new_until = current + timedelta(days=days)
            else:
                new_until = current_now + timedelta(days=days)

            cur.execute("""
                UPDATE users
                SET subscription=TRUE,
                    subscription_until=%s
                WHERE user_id=%s
            """, (
                new_until,
                user_id
            ))

            cur.execute("""
                UPDATE payments
                SET status='paid',
                    paid_at=NOW()
                WHERE payment_id=%s
            """, (payment_id,))

        conn.commit()

    return {
        "already_paid": False,
        "user_id": user_id,
        "days": days,
        "payment_id": payment_id,
        "subscription_until": new_until
    }


# =========================================================
# SUBSCRIPTION
# =========================================================

def subscription_link(user_id):
    return (
        f"{PUBLIC_SITE_URL}/sub/"
        f"{SUBSCRIPTION_PREFIX}{user_id}"
    )


def github_headers():
    headers = {
        "Accept": "application/vnd.github+json"
    }

    if GITHUB_TOKEN:
        headers["Authorization"] = (
            f"Bearer {GITHUB_TOKEN}"
        )

    return headers


def github_get_file(filename):
    url = (
        "https://api.github.com/repos/"
        f"{GITHUB_OWNER}/{GITHUB_REPO}"
        f"/contents/{filename}"
    )

    response = requests.get(
        url,
        headers=github_headers(),
        params={"ref": GITHUB_BRANCH},
        timeout=30
    )

    if response.status_code != 200:
        log.error(
            "GitHub GET %s -> %s: %s",
            filename,
            response.status_code,
            response.text[:300]
        )
        return None, None

    data = response.json()

    try:
        content = base64.b64decode(
            data["content"]
        ).decode("utf-8")
    except Exception:
        log.exception(
            "GitHub decode error: %s",
            filename
        )
        return None, None

    return content, data.get("sha")


def github_put_file(
    filename,
    content,
    sha=None,
    message="Update ixxy subscription"
):
    if not GITHUB_TOKEN:
        log.error("GITHUB_TOKEN не задан")
        return False

    url = (
        "https://api.github.com/repos/"
        f"{GITHUB_OWNER}/{GITHUB_REPO}"
        f"/contents/{filename}"
    )

    encoded = base64.b64encode(
        content.encode("utf-8")
    ).decode("ascii")

    payload = {
        "message": message,
        "content": encoded,
        "branch": GITHUB_BRANCH
    }

    if sha:
        payload["sha"] = sha

    response = requests.put(
        url,
        headers=github_headers(),
        json=payload,
        timeout=30
    )

    if response.status_code not in (200, 201):
        log.error(
            "GitHub PUT %s -> %s: %s",
            filename,
            response.status_code,
            response.text[:500]
        )
        return False

    return True


def get_servers_content():
    content, _ = github_get_file(
        SERVERS_FILE
    )

    return (content or "").strip()


def get_no_servers_content():
    content, _ = github_get_file(
        NO_SERVERS_FILE
    )

    return (content or "").strip()


def save_user_subscription(
    user_id,
    content
):
    filename = f"users/{user_id}.txt"

    old_content, sha = github_get_file(
        filename
    )

    if sha:
        return github_put_file(
            filename,
            content,
            sha,
            f"Update ixxy subscription {user_id}"
        )

    return github_put_file(
        filename,
        content,
        None,
        f"Create ixxy subscription {user_id}"
    )


def update_subscription_file(user_id):
    user = get_user(user_id)

    if not user:
        return False

    active = subscription_active(user)

    if active:
        content = get_servers_content()
    else:
        content = get_no_servers_content()

    if not content:
        log.warning(
            "GitHub: пустой контент для user=%s active=%s",
            user_id,
            active
        )
        return False

    ok = save_user_subscription(
        user_id,
        content
    )

    if not ok:
        return False

    link = subscription_link(user_id)

    with db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE users
                SET subscription_content=%s,
                    subscription_link=%s
                WHERE user_id=%s
            """, (
                content,
                link,
                user_id
            ))

        conn.commit()

    return True


def sync_all_users():
    users = get_all_users()

    result = {
        "total": len(users),
        "updated": 0,
        "expired": 0,
        "errors": 0
    }

    for user in users:
        try:
            user_id = user["user_id"]

            update_subscription_file(
                user_id
            )

            if subscription_active(user):
                result["updated"] += 1
            else:
                result["expired"] += 1

        except Exception:
            result["errors"] += 1
            log.exception(
                "Sync error user=%s",
                user.get("user_id")
            )

    return result


# =========================================================
# CASHERA
# =========================================================

def create_cashera_payment(
    user_id,
    amount,
    days
):
    if not CASHERA_API_KEY:
        raise RuntimeError(
            "CASHERA_API_KEY не задан"
        )

    if not CASHERA_MERCHANT_ID:
        raise RuntimeError(
            "CASHERA_MERCHANT_ID не задан"
        )

    external_id = (
        f"{user_id}_{uuid.uuid4().hex}"
    )

    payload = {
        "amount": int(amount * 100),
        "currency": "RUB",
        "payment_method": "sbp",
        "external_id": external_id,
        "merchant_id": CASHERA_MERCHANT_ID,
        "description": (
            f"ixxy VPN — {days} дней"
        ),
        "callback_url": CASHERA_CALLBACK_URL,
        "success_url": CASHERA_SUCCESS_URL,
        "fail_url": CASHERA_FAIL_URL
    }

    headers = {
        "X-Api-Key": CASHERA_API_KEY,
        "Content-Type": "application/json",
        "Accept": "application/json"
    }

    response = requests.post(
        f"{CASHERA_URL}/integration/transactions",
        headers=headers,
        json=payload,
        timeout=30
    )

    if response.status_code not in (200, 201):
        raise RuntimeError(
            "CasheRa HTTP "
            f"{response.status_code}: "
            f"{response.text[:500]}"
        )

    data = response.json()

    transaction = data.get(
        "transaction"
    )

    if isinstance(transaction, dict):
        merged = dict(data)
        merged.update(transaction)
        data = merged

    payment_id = (
        data.get("uuid")
        or data.get("id")
        or data.get("transaction_id")
    )

    payment_url = (
        data.get("payment_url")
        or data.get("paymentUrl")
        or data.get("url")
        or data.get("pay_url")
    )

    if not payment_id:
        raise RuntimeError(
            f"CasheRa не вернул ID платежа: {data}"
        )

    if not payment_url:
        raise RuntimeError(
            f"CasheRa не вернул ссылку оплаты: {data}"
        )

    return {
        "uuid": str(payment_id),
        "payment_url": str(payment_url),
        "external_id": external_id
    }


# =========================================================
# TARIFFS
# =========================================================

SBP_PLANS = {
    "sbp_30": {
        "amount": 129,
        "days": 30
    },
    "sbp_90": {
        "amount": 379,
        "days": 90
    },
    "sbp_180": {
        "amount": 659,
        "days": 180
    },
    "sbp_365": {
        "amount": 1089,
        "days": 365
    }
}

STARS_PLANS = {
    "stars_30": {
        "stars": 70,
        "days": 30
    },
    "stars_90": {
        "stars": 190,
        "days": 90
    },
    "stars_180": {
        "stars": 350,
        "days": 180
    },
    "stars_365": {
        "stars": 700,
        "days": 365
    }
}


# =========================================================
# BOT
# =========================================================

bot = Bot(BOT_TOKEN)
dp = Dispatcher()


# =========================================================
# KEYBOARDS
# =========================================================

def home_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="☂️ Моя подписка",
                    callback_data="cabinet"
                )
            ],
            [
                InlineKeyboardButton(
                    text="💳 Купить подписку",
                    callback_data="buy"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🎁 Промокод",
                    callback_data="promo"
                )
            ]
        ]
    )


def buy_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🇷🇺 СБП • 30 дней • 129₽",
                    callback_data="sbp_30"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🇷🇺 СБП • 90 дней • 379₽",
                    callback_data="sbp_90"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🇷🇺 СБП • 180 дней • 659₽",
                    callback_data="sbp_180"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🇷🇺 СБП • 365 дней • 1089₽",
                    callback_data="sbp_365"
                )
            ],
            [
                InlineKeyboardButton(
                    text="⭐ Stars • 30 дней • 70",
                    callback_data="stars_30"
                )
            ],
            [
                InlineKeyboardButton(
                    text="⭐ Stars • 90 дней • 190",
                    callback_data="stars_90"
                )
            ],
            [
                InlineKeyboardButton(
                    text="⭐ Stars • 180 дней • 350",
                    callback_data="stars_180"
                )
            ],
            [
                InlineKeyboardButton(
                    text="⭐ Stars • 365 дней • 700",
                    callback_data="stars_365"
                )
            ],
            [
                InlineKeyboardButton(
                    text="◀️ Назад",
                    callback_data="home"
                )
            ]
        ]
    )


def admin_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="👥 Пользователи",
                    callback_data="admin_users"
                ),
                InlineKeyboardButton(
                    text="📊 Статистика",
                    callback_data="admin_stats"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔎 Поиск",
                    callback_data="admin_search"
                ),
                InlineKeyboardButton(
                    text="💳 Платежи",
                    callback_data="admin_payments"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🎟 Промокоды",
                    callback_data="admin_promos"
                )
            ],
            [
                InlineKeyboardButton(
                    text="📢 Рассылка",
                    callback_data="admin_broadcast"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔄 Синхронизация",
                    callback_data="admin_sync"
                )
            ],
            [
                InlineKeyboardButton(
                    text="◀️ Главное меню",
                    callback_data="home"
                )
            ]
        ]
    )


# =========================================================
# FSM
# =========================================================

class PromoState(StatesGroup):
    waiting_code = State()


class AdminSearchState(StatesGroup):
    waiting_query = State()


class BroadcastState(StatesGroup):
    waiting_message = State()
    waiting_confirm = State()


# =========================================================
# TEXT
# =========================================================

def cabinet_text(user):
    if not user:
        return "Пользователь не найден."

    active = subscription_active(user)

    status = (
        "🟢 Активна"
        if active
        else "🔴 Неактивна"
    )

    return (
        "☂️ <b>Личный кабинет ixxy</b>\n\n"
        f"🆔 ID: <code>{user['user_id']}</code>\n"
        f"📌 Статус: {status}\n"
        f"📅 До: <b>"
        f"{fmt_date(user.get('subscription_until'))}"
        f"</b>\n\n"
        f"🔗 <code>"
        f"{subscription_link(user['user_id'])}"
        f"</code>"
    )


# =========================================================
# START
# =========================================================

@dp.message(Command("start"))
async def cmd_start(message: Message):
    ensure_user(message.from_user)

    await message.answer(
        "☂️ <b>ixxy VPN</b>\n\n"
        "Добро пожаловать!\n\n"
        "Выберите действие:",
        reply_markup=home_keyboard()
    )


# =========================================================
# HOME
# =========================================================

@dp.callback_query(F.data == "home")
async def cb_home(callback: CallbackQuery):
    ensure_user(callback.from_user)

    await callback.message.edit_text(
        "☂️ <b>ixxy VPN</b>\n\n"
        "Выберите действие:",
        reply_markup=home_keyboard()
    )

    await callback.answer()


# =========================================================
# CABINET
# =========================================================

@dp.callback_query(F.data == "cabinet")
async def cb_cabinet(
    callback: CallbackQuery
):
    ensure_user(callback.from_user)

    user = get_user(
        callback.from_user.id
    )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📋 Ссылка",
                    callback_data="sub_link"
                )
            ],
            [
                InlineKeyboardButton(
                    text="💳 Продлить",
                    callback_data="buy"
                )
            ],
            [
                InlineKeyboardButton(
                    text="◀️ Назад",
                    callback_data="home"
                )
            ]
        ]
    )

    await callback.message.edit_text(
        cabinet_text(user),
        reply_markup=keyboard
    )

    await callback.answer()


@dp.callback_query(F.data == "sub_link")
async def cb_sub_link(
    callback: CallbackQuery
):
    link = subscription_link(
        callback.from_user.id
    )

    await callback.message.answer(
        "🔗 <b>Твоя ссылка на подписку:</b>\n\n"
        f"<code>{link}</code>"
    )

    await callback.answer()


# =========================================================
# BUY
# =========================================================

@dp.callback_query(F.data == "buy")
async def cb_buy(
    callback: CallbackQuery
):
    await callback.message.edit_text(
        "💳 <b>Выберите тариф</b>",
        reply_markup=buy_keyboard()
    )

    await callback.answer()


# =========================================================
# SBP / CASHERA
# =========================================================

@dp.callback_query(F.data.startswith("sbp_"))
async def cb_sbp(
    callback: CallbackQuery
):
    ensure_user(callback.from_user)

    plan = SBP_PLANS.get(
        callback.data
    )

    if not plan:
        await callback.answer(
            "Тариф не найден",
            show_alert=True
        )
        return

    try:
        payment = await asyncio.to_thread(
            create_cashera_payment,
            callback.from_user.id,
            plan["amount"],
            plan["days"]
        )

        payment_id = payment["uuid"]

        await asyncio.to_thread(
            create_payment,
            callback.from_user.id,
            payment_id,
            plan["amount"] * 100,
            plan["days"],
            "cashera",
            payment["external_id"]
        )

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="💳 Оплатить",
                        url=payment["payment_url"]
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="◀️ Назад",
                        callback_data="buy"
                    )
                ]
            ]
        )

        await callback.message.edit_text(
            "💳 <b>Счёт создан</b>\n\n"
            f"📅 Срок: <b>{plan['days']} дней</b>\n"
            f"💰 Сумма: <b>{plan['amount']} ₽</b>\n\n"
            "После оплаты подписка "
            "активируется автоматически.",
            reply_markup=keyboard
        )

    except Exception as e:
        log.exception("CasheRa create payment error")

        await callback.message.edit_text(
            "❌ Не удалось создать платёж.\n\n"
            f"<code>{str(e)[:300]}</code>",
            reply_markup=buy_keyboard()
        )

    await callback.answer()


# =========================================================
# STARS
# =========================================================

@dp.callback_query(F.data.startswith("stars_"))
async def cb_stars(
    callback: CallbackQuery
):
    ensure_user(callback.from_user)

    plan = STARS_PLANS.get(
        callback.data
    )

    if not plan:
        await callback.answer(
            "Тариф не найден",
            show_alert=True
        )
        return

    payload = (
        f"ixxy_stars_"
        f"{callback.from_user.id}_"
        f"{plan['days']}"
    )

    await bot.send_invoice(
        chat_id=callback.from_user.id,
        title=f"ixxy VPN — {plan['days']} дней",
        description=(
            f"Подписка ixxy VPN "
            f"на {plan['days']} дней"
        ),
        payload=payload,
        currency="XTR",
        prices=[
            LabeledPrice(
                label=f"{plan['days']} дней",
                amount=plan["stars"]
            )
        ]
    )

    await callback.answer()


@dp.pre_checkout_query()
async def pre_checkout(
    query: PreCheckoutQuery
):
    try:
        parts = query.invoice_payload.split("_")

        if len(parts) != 4:
            await query.answer(
                ok=False,
                error_message="Некорректный платёж"
            )
            return

        _, _, user_id, days = parts

        user_id = int(user_id)
        days = int(days)

        if user_id != query.from_user.id:
            await query.answer(
                ok=False,
                error_message="Пользователь не совпадает"
            )
            return

        plan = next(
            (
                p for p in STARS_PLANS.values()
                if p["days"] == days
            ),
            None
        )

        if not plan:
            await query.answer(
                ok=False,
                error_message="Тариф не найден"
            )
            return

        if query.total_amount != plan["stars"]:
            await query.answer(
                ok=False,
                error_message="Неверная сумма"
            )
            return

        await query.answer(ok=True)

    except Exception:
        await query.answer(
            ok=False,
            error_message="Ошибка проверки платежа"
        )


@dp.message(F.successful_payment)
async def successful_stars(
    message: Message
):
    payment = message.successful_payment

    try:
        parts = payment.invoice_payload.split("_")

        if len(parts) != 4:
            return

        _, _, user_id, days = parts

        user_id = int(user_id)
        days = int(days)

        if user_id != message.from_user.id:
            return

        plan = next(
            (
                p for p in STARS_PLANS.values()
                if p["days"] == days
            ),
            None
        )

        if not plan:
            return

        if payment.total_amount != plan["stars"]:
            return

        payment_id = (
            payment.telegram_payment_charge_id
        )

        await asyncio.to_thread(
            create_payment,
            user_id,
            payment_id,
            payment.total_amount,
            days,
            "stars"
        )

        result = await asyncio.to_thread(
            process_paid_payment,
            payment_id
        )

        if not result:
            return

        if not result["already_paid"]:
            await asyncio.to_thread(
                update_subscription_file,
                user_id
            )

        until = result.get(
            "subscription_until"
        )

        if until:
            date_text = fmt_date(until)
        else:
            current = get_user(user_id)
            date_text = fmt_date(
                current.get("subscription_until")
            )

        await message.answer(
            "✅ <b>Оплата получена!</b>\n\n"
            f"📅 Добавлено: <b>{days} дней</b>\n"
            f"📆 До: <b>{date_text}</b>\n\n"
            f"🔗 <code>"
            f"{subscription_link(user_id)}"
            f"</code>",
            reply_markup=home_keyboard()
        )

    except Exception:
        log.exception(
            "Stars payment error"
        )


# =========================================================
# PROMOCODE
# =========================================================

@dp.callback_query(F.data == "promo")
async def promo_start(
    callback: CallbackQuery,
    state: FSMContext
):
    await state.set_state(
        PromoState.waiting_code
    )

    await callback.message.edit_text(
        "🎁 <b>Промокод</b>\n\n"
        "Отправь промокод сообщением.\n\n"
        "/cancel — отмена"
    )

    await callback.answer()


@dp.message(PromoState.waiting_code)
async def promo_apply(
    message: Message,
    state: FSMContext
):
    code = (
        message.text or ""
    ).strip().upper()

    if code == "/CANCEL":
        await state.clear()

        await message.answer(
            "Отменено.",
            reply_markup=home_keyboard()
        )
        return

    with db() as conn:
        with conn.cursor() as cur:

            cur.execute("""
                SELECT *
                FROM promocodes
                WHERE UPPER(code)=UPPER(%s)
                  AND active=TRUE
                LIMIT 1
            """, (code,))

            promo = cur.fetchone()

            if not promo:
                await state.clear()

                await message.answer(
                    "❌ Промокод не найден.",
                    reply_markup=home_keyboard()
                )
                return

            cur.execute("""
                SELECT 1
                FROM promocode_uses
                WHERE user_id=%s
                  AND promo_id=%s
            """, (
                message.from_user.id,
                promo["id"]
            ))

            if cur.fetchone():
                await state.clear()

                await message.answer(
                    "❌ Этот промокод уже использован.",
                    reply_markup=home_keyboard()
                )
                return

            cur.execute("""
                INSERT INTO promocode_uses (
                    user_id,
                    promo_id
                )
                VALUES (%s,%s)
            """, (
                message.from_user.id,
                promo["id"]
            ))

        conn.commit()

    days = promo["days"]

    until = await asyncio.to_thread(
        extend_subscription,
        message.from_user.id,
        days
    )

    await asyncio.to_thread(
        update_subscription_file,
        message.from_user.id
    )

    await state.clear()

    await message.answer(
        "🎉 <b>Промокод активирован!</b>\n\n"
        f"📅 Добавлено: <b>{days} дней</b>\n"
        f"📆 До: <b>{fmt_date(until)}</b>",
        reply_markup=home_keyboard()
    )


# =========================================================
# ADMIN
# =========================================================

def is_admin(user_id):
    return user_id in ADMIN_IDS


@dp.message(Command("admin"))
async def cmd_admin(
    message: Message
):
    if not is_admin(message.from_user.id):
        return

    await message.answer(
        "🛠 <b>Админ-панель ixxy</b>",
        reply_markup=admin_keyboard()
    )


# =========================================================
# ADMIN STATS
# =========================================================

@dp.callback_query(F.data == "admin_stats")
async def admin_stats(
    callback: CallbackQuery
):
    if not is_admin(callback.from_user.id):
        await callback.answer()
        return

    users = get_all_users()

    active = sum(
        1 for user in users
        if subscription_active(user)
    )

    with db() as conn:
        with conn.cursor() as cur:

            cur.execute("""
                SELECT
                    COUNT(*) AS count
                FROM payments
            """)

            total_payments = cur.fetchone()["count"]

            cur.execute("""
                SELECT
                    COUNT(*) AS count,
                    COALESCE(SUM(amount),0) AS amount
                FROM payments
                WHERE status='paid'
            """)

            row = cur.fetchone()

    await callback.message.edit_text(
        "📊 <b>Статистика ixxy</b>\n\n"
        f"👥 Пользователей: <b>{len(users)}</b>\n"
        f"🟢 Активных: <b>{active}</b>\n"
        f"💳 Платежей: <b>{total_payments}</b>\n"
        f"✅ Оплачено: <b>{row['count']}</b>\n"
        f"💰 Оборот: <b>{row['amount'] / 100:.2f} ₽</b>",
        reply_markup=admin_keyboard()
    )

    await callback.answer()


# =========================================================
# ADMIN USERS
# =========================================================

@dp.callback_query(F.data == "admin_users")
async def admin_users(
    callback: CallbackQuery
):
    if not is_admin(callback.from_user.id):
        return

    users = get_all_users()

    text = "👥 <b>Пользователи</b>\n\n"

    for user in users[:30]:
        status = (
            "🟢"
            if subscription_active(user)
            else "🔴"
        )

        name = (
            user.get("first_name")
            or user.get("username")
            or "—"
        )

        text += (
            f"{status} "
            f"<code>{user['user_id']}</code> "
            f"{name}\n"
        )

    if not users:
        text += "Пользователей нет."

    await callback.message.edit_text(
        text,
        reply_markup=admin_keyboard()
    )

    await callback.answer()


# =========================================================
# ADMIN PAYMENTS
# =========================================================

@dp.callback_query(F.data == "admin_payments")
async def admin_payments(
    callback: CallbackQuery
):
    if not is_admin(callback.from_user.id):
        return

    with db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT *
                FROM payments
                ORDER BY created_at DESC
                LIMIT 30
            """)

            payments = cur.fetchall()

    text = "💳 <b>Последние платежи</b>\n\n"

    for payment in payments:
        status = (
            "✅"
            if payment["status"] == "paid"
            else "⏳"
        )

        text += (
            f"{status} "
            f"<code>{payment['user_id']}</code> — "
            f"{payment['days']} дн. — "
            f"{payment['provider']}\n"
        )

    if not payments:
        text += "Платежей нет."

    await callback.message.edit_text(
        text,
        reply_markup=admin_keyboard()
    )

    await callback.answer()


# =========================================================
# ADMIN SEARCH
# =========================================================

@dp.callback_query(F.data == "admin_search")
async def admin_search_start(
    callback: CallbackQuery,
    state: FSMContext
):
    if not is_admin(callback.from_user.id):
        return

    await state.set_state(
        AdminSearchState.waiting_query
    )

    await callback.message.edit_text(
        "🔎 <b>Поиск пользователя</b>\n\n"
        "Отправь Telegram ID или username."
    )

    await callback.answer()


@dp.message(AdminSearchState.waiting_query)
async def admin_search_result(
    message: Message,
    state: FSMContext
):
    if not is_admin(message.from_user.id):
        return

    query = (
        message.text or ""
    ).strip().lower()

    users = get_all_users()

    found = []

    for user in users:
        user_id = str(
            user["user_id"]
        )

        username = (
            user.get("username")
            or ""
        ).lower()

        if query == user_id:
            found.append(user)

        elif query.lstrip("@") in username:
            found.append(user)

    await state.clear()

    if not found:
        await message.answer(
            "❌ Пользователь не найден.",
            reply_markup=admin_keyboard()
        )
        return

    text = "🔎 <b>Результат</b>\n\n"

    for user in found[:20]:
        text += (
            f"🆔 <code>{user['user_id']}</code>\n"
            f"👤 @{user.get('username') or '—'}\n"
            f"📅 До: "
            f"{fmt_date(user.get('subscription_until'))}\n\n"
        )

    await message.answer(
        text,
        reply_markup=admin_keyboard()
    )


# =========================================================
# ADMIN PROMOCODES
# =========================================================

@dp.callback_query(F.data == "admin_promos")
async def admin_promos(
    callback: CallbackQuery
):
    if not is_admin(callback.from_user.id):
        return

    with db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT *
                FROM promocodes
                ORDER BY created_at DESC
                LIMIT 50
            """)

            promos = cur.fetchall()

    text = "🎟 <b>Промокоды</b>\n\n"

    for promo in promos:
        status = (
            "🟢"
            if promo["active"]
            else "🔴"
        )

        text += (
            f"{status} "
            f"<code>{promo['code']}</code> — "
            f"{promo['days']} дн.\n"
        )

    if not promos:
        text += (
            "Промокодов нет.\n\n"
            "Создать:\n"
            "<code>/promo CODE DAYS</code>"
        )
    else:
        text += (
            "\nСоздать новый:\n"
            "<code>/promo CODE DAYS</code>"
        )

    await callback.message.edit_text(
        text,
        reply_markup=admin_keyboard()
    )

    await callback.answer()


# =========================================================
# ADMIN SYNC
# =========================================================

@dp.callback_query(F.data == "admin_sync")
async def admin_sync(
    callback: CallbackQuery
):
    if not is_admin(callback.from_user.id):
        return

    await callback.message.edit_text(
        "🔄 <b>Синхронизация...</b>"
    )

    result = await asyncio.to_thread(
        sync_all_users
    )

    await callback.message.edit_text(
        "✅ <b>Синхронизация завершена</b>\n\n"
        f"👥 Всего: {result['total']}\n"
        f"🔄 Обновлено: {result['updated']}\n"
        f"🔴 Неактивных: {result['expired']}\n"
        f"❌ Ошибок: {result['errors']}",
        reply_markup=admin_keyboard()
    )

    await callback.answer()


# =========================================================
# 📢 BROADCAST
# =========================================================

def get_user_ids_for_broadcast():
    users = get_all_users() or []

    result = []

    for user in users:

        if not isinstance(user, dict):
            continue

        user_id = user.get("user_id")

        if user_id is None:
            continue

        try:
            result.append(int(user_id))
        except (TypeError, ValueError):
            continue

    return list(dict.fromkeys(result))


def broadcast_confirm_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Начать рассылку",
                    callback_data="broadcast_confirm"
                ),
                InlineKeyboardButton(
                    text="❌ Отмена",
                    callback_data="broadcast_cancel"
                )
            ]
        ]
    )


# =========================================================
# НАЧАЛО РАССЫЛКИ
# =========================================================

@dp.callback_query(F.data == "admin_broadcast")
async def start_broadcast(
    callback: CallbackQuery,
    state: FSMContext
):
    if not callback.from_user:
        return

    if not is_admin(callback.from_user.id):
        await callback.answer(
            "❌ Нет доступа.",
            show_alert=True
        )
        return

    await state.clear()

    await state.set_state(
        BroadcastState.waiting_message
    )

    await callback.answer()

    if not callback.message:
        return

    await callback.message.answer(
        "📢 <b>Создание рассылки</b>\n\n"
        "Отправь сообщение, которое нужно "
        "разослать пользователям.\n\n"
        "Поддерживается:\n"
        "• текст\n"
        "• фото с подписью\n"
        "• видео с подписью\n"
        "• документ с подписью\n\n"
        "После отправки будет предпросмотр "
        "и подтверждение.\n\n"
        "Для отмены отправь /cancel.",
        parse_mode="HTML"
    )


# =========================================================
# ПОЛУЧЕНИЕ СООБЩЕНИЯ ДЛЯ РАССЫЛКИ
# =========================================================

@dp.message(BroadcastState.waiting_message)
async def prepare_broadcast(
    message: Message,
    state: FSMContext
):
    if not message.from_user:
        return

    if not is_admin(message.from_user.id):
        await state.clear()
        return

    if message.text == "/cancel":
        await state.clear()

        await message.answer(
            "❌ Рассылка отменена."
        )

        return

    supported = any([
        bool(message.text),
        bool(message.photo),
        bool(message.video),
        bool(message.document)
    ])

    if not supported:
        await message.answer(
            "⚠️ Этот тип сообщения "
            "не поддерживается.\n\n"
            "Отправь текст, фото, видео "
            "или документ."
        )
        return

    try:
        users = await asyncio.to_thread(
            get_user_ids_for_broadcast
        )

    except Exception as e:
        log.exception(
            "Broadcast get users error"
        )

        await message.answer(
            "❌ Не удалось получить "
            "список пользователей.\n\n"
            f"<code>{type(e).__name__}</code>",
            parse_mode="HTML"
        )

        return

    await state.update_data(
        message_id=message.message_id,
        chat_id=message.chat.id,
        users_count=len(users)
    )

    await state.set_state(
        BroadcastState.waiting_confirm
    )

    await message.answer(
        "📢 <b>Предпросмотр рассылки</b>\n\n"
        f"👥 Получателей: <b>{len(users)}</b>\n\n"
        "Сообщение выше будет отправлено "
        "всем пользователям.\n\n"
        "Начать рассылку?",
        parse_mode="HTML",
        reply_markup=broadcast_confirm_keyboard()
    )


# =========================================================
# ПОДТВЕРЖДЕНИЕ РАССЫЛКИ
# =========================================================

@dp.callback_query(F.data == "broadcast_confirm")
async def confirm_broadcast(
    callback: CallbackQuery,
    state: FSMContext
):
    if not callback.from_user:
        return

    if not is_admin(callback.from_user.id):
        await callback.answer(
            "❌ Нет доступа.",
            show_alert=True
        )
        return

    data = await state.get_data()

    message_id = data.get("message_id")
    chat_id = data.get("chat_id")

    if message_id is None or chat_id is None:
        await state.clear()

        await callback.answer(
            "❌ Сообщение для рассылки "
            "не найдено.",
            show_alert=True
        )

        return

    try:
        users = await asyncio.to_thread(
            get_user_ids_for_broadcast
        )

    except Exception as e:
        await state.clear()

        await callback.answer(
            "❌ Ошибка.",
            show_alert=True
        )

        if callback.message:
            await callback.message.answer(
                "❌ Не удалось получить "
                "список пользователей.\n\n"
                f"<code>{type(e).__name__}</code>",
                parse_mode="HTML"
            )

        return

    total = len(users)

    if total == 0:
        await state.clear()

        await callback.answer(
            "Пользователей нет.",
            show_alert=True
        )

        if callback.message:
            await callback.message.answer(
                "⚠️ Пользователей для "
                "рассылки нет."
            )

        return

    await callback.answer(
        "📢 Рассылка запущена"
    )

    if not callback.message:
        await state.clear()
        return

    status_message = await callback.message.answer(
        "📢 <b>Рассылка запущена</b>\n\n"
        f"👥 Получателей: <b>{total}</b>\n"
        "⏳ Обработка...",
        parse_mode="HTML"
    )

    # Используем основной экземпляр бота.
    current_bot = callback.bot

    sent = 0
    failed = 0
    blocked = 0

    try:

        for index, user_id in enumerate(
            users,
            start=1
        ):

            success = False

            try:

                await current_bot.copy_message(
                    chat_id=user_id,
                    from_chat_id=chat_id,
                    message_id=message_id
                )

                sent += 1
                success = True

            except TelegramRetryAfter as e:

                retry_after = (
                    int(e.retry_after) + 1
                )

                log.warning(
                    "Broadcast rate limit: "
                    "sleep %ss",
                    retry_after
                )

                await asyncio.sleep(
                    retry_after
                )

                try:

                    await current_bot.copy_message(
                        chat_id=user_id,
                        from_chat_id=chat_id,
                        message_id=message_id
                    )

                    sent += 1
                    success = True

                except TelegramForbiddenError:

                    failed += 1
                    blocked += 1

                except TelegramBadRequest as retry_error:

                    failed += 1

                    log.warning(
                        "Broadcast retry bad request: %r",
                        retry_error
                    )

                except Exception as retry_error:

                    failed += 1

                    log.warning(
                        "Broadcast retry error: %r",
                        retry_error
                    )

            except TelegramForbiddenError:

                failed += 1
                blocked += 1

            except TelegramBadRequest as e:

                failed += 1

                log.warning(
                    "Broadcast bad request: %r",
                    e
                )

            except Exception as e:

                failed += 1

                log.exception(
                    "Broadcast send error "
                    "user=%s",
                    user_id
                )

            # -------------------------------------------------
            # ПРОГРЕСС
            # -------------------------------------------------

            if (
                index % 20 == 0
                or index == total
            ):

                try:

                    progress = (
                        index * 100 // total
                    )

                    await status_message.edit_text(
                        "📢 <b>Рассылка выполняется</b>\n\n"
                        f"📨 Обработано: "
                        f"<b>{index}/{total}</b>\n"
                        f"📊 Прогресс: "
                        f"<b>{progress}%</b>\n\n"
                        f"✅ Отправлено: "
                        f"<b>{sent}</b>\n"
                        f"❌ Ошибок: "
                        f"<b>{failed}</b>\n"
                        f"🚫 Заблокировали: "
                        f"<b>{blocked}</b>",
                        parse_mode="HTML"
                    )

                except Exception:
                    pass

            # Небольшая пауза,
            # чтобы не упираться в лимиты Telegram.
            if success:
                await asyncio.sleep(0.08)
            else:
                await asyncio.sleep(0.05)

    finally:
        pass

    await state.clear()

    # ---------------------------------------------------------
    # ИТОГ
    # ---------------------------------------------------------

    try:

        await status_message.edit_text(
            "📢 <b>Рассылка завершена</b>\n\n"
            f"👥 Всего пользователей: "
            f"<b>{total}</b>\n\n"
            f"✅ Отправлено: "
            f"<b>{sent}</b>\n"
            f"❌ Ошибок: "
            f"<b>{failed}</b>\n"
            f"🚫 Заблокировали бота: "
            f"<b>{blocked}</b>",
            parse_mode="HTML"
        )

    except Exception:

        try:

            await callback.message.answer(
                "📢 <b>Рассылка завершена</b>\n\n"
                f"👥 Всего пользователей: "
                f"<b>{total}</b>\n\n"
                f"✅ Отправлено: "
                f"<b>{sent}</b>\n"
                f"❌ Ошибок: "
                f"<b>{failed}</b>\n"
                f"🚫 Заблокировали бота: "
                f"<b>{blocked}</b>",
                parse_mode="HTML"
            )

        except Exception:
            pass


# =========================================================
# ОТМЕНА РАССЫЛКИ КНОПКОЙ
# =========================================================

@dp.callback_query(F.data == "broadcast_cancel")
async def cancel_broadcast(
    callback: CallbackQuery,
    state: FSMContext
):
    if not callback.from_user:
        return

    if not is_admin(callback.from_user.id):
        await callback.answer(
            "❌ Нет доступа.",
            show_alert=True
        )
        return

    await state.clear()

    await callback.answer(
        "Рассылка отменена."
    )

    if callback.message:

        try:

            await callback.message.edit_text(
                "❌ <b>Рассылка отменена.</b>",
                parse_mode="HTML"
            )

        except TelegramBadRequest:
            pass


# =========================================================
# ОТМЕНА /cancel ВО ВРЕМЯ РАССЫЛКИ
# =========================================================

@dp.message(
    BroadcastState.waiting_message,
    F.text == "/cancel"
)
async def cancel_broadcast_message(
    message: Message,
    state: FSMContext
):
    if not message.from_user:
        return

    if not is_admin(message.from_user.id):
        await state.clear()
        return

    await state.clear()

    await message.answer(
        "❌ Рассылка отменена."
    )


@dp.message(
    BroadcastState.waiting_confirm,
    F.text == "/cancel"
)
async def cancel_broadcast_confirm(
    message: Message,
    state: FSMContext
):
    if not message.from_user:
        return

    if not is_admin(message.from_user.id):
        await state.clear()
        return

    await state.clear()

    await message.answer(
        "❌ Рассылка отменена."
    )


# =========================================================
# ADMIN COMMANDS
# =========================================================

@dp.message(Command("adddays"))
async def cmd_adddays(
    message: Message
):
    if not is_admin(message.from_user.id):
        return

    parts = (
        message.text or ""
    ).split()

    if len(parts) != 3:
        await message.answer(
            "Использование:\n"
            "<code>/adddays USER_ID DAYS</code>"
        )
        return

    try:
        user_id = int(parts[1])
        days = int(parts[2])

        until = await asyncio.to_thread(
            extend_subscription,
            user_id,
            days
        )

        if not until:
            await message.answer(
                "❌ Пользователь не найден."
            )
            return

        await asyncio.to_thread(
            update_subscription_file,
            user_id
        )

        await message.answer(
            "✅ <b>Подписка продлена</b>\n\n"
            f"🆔 <code>{user_id}</code>\n"
            f"➕ {days} дней\n"
            f"📅 До: <b>{fmt_date(until)}</b>\n\n"
            f"🔗 <code>"
            f"{subscription_link(user_id)}"
            f"</code>"
        )

    except Exception as e:
        await message.answer(
            f"❌ Ошибка: "
            f"<code>{str(e)[:300]}</code>"
        )


@dp.message(Command("disable"))
async def cmd_disable(
    message: Message
):
    if not is_admin(message.from_user.id):
        return

    parts = (
        message.text or ""
    ).split()

    if len(parts) != 2:
        await message.answer(
            "Использование:\n"
            "<code>/disable USER_ID</code>"
        )
        return

    try:
        user_id = int(parts[1])

        disable_subscription(
            user_id
        )

        await asyncio.to_thread(
            update_subscription_file,
            user_id
        )

        await message.answer(
            "🔴 Подписка отключена:\n"
            f"<code>{user_id}</code>"
        )

    except Exception as e:
        await message.answer(
            f"❌ Ошибка: "
            f"<code>{str(e)[:300]}</code>"
        )


@dp.message(Command("promo"))
async def cmd_promo(
    message: Message
):
    if not is_admin(message.from_user.id):
        return

    parts = (
        message.text or ""
    ).split()

    if len(parts) != 3:
        await message.answer(
            "Использование:\n"
            "<code>/promo CODE DAYS</code>\n\n"
            "Пример:\n"
            "<code>/promo IXXY30 30</code>"
        )
        return

    code = parts[1].upper()

    try:
        days = int(parts[2])
    except ValueError:
        await message.answer(
            "❌ DAYS должен быть числом."
        )
        return

    if days <= 0:
        await message.answer(
            "❌ Количество дней должно быть "
            "больше 0."
        )
        return

    try:
        with db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO promocodes (
                        code,
                        days,
                        active
                    )
                    VALUES (%s,%s,TRUE)
                """, (
                    code,
                    days
                ))

            conn.commit()

        await message.answer(
            "🎟 <b>Промокод создан</b>\n\n"
            f"Код: <code>{code}</code>\n"
            f"Дней: <b>{days}</b>"
        )

    except Exception as e:
        await message.answer(
            f"❌ Ошибка: "
            f"<code>{str(e)[:300]}</code>"
        )


# =========================================================
# CASHERA WEBHOOK
# =========================================================

app = Flask(__name__)

BOT_LOOP = None


def verify_cashera_webhook():

    if CASHERA_API_SECRET:

        secret = (
            request.headers.get("X-Secret")
            or request.headers.get("X-Api-Secret")
            or request.headers.get("X-Cashera-Secret")
        )

        if secret != CASHERA_API_SECRET:
            return False

    return True


@app.route("/")
def root():
    return "ixxy VPN OK"


@app.route("/health")
def health():
    return jsonify({
        "status": "ok",
        "service": "ixxy",
        "bot": True
    })


@app.route(
    "/webhook/cashera",
    methods=["POST"]
)
def cashera_webhook():

    if not verify_cashera_webhook():
        return "Forbidden", 403

    try:

        data = request.get_json(
            silent=True
        ) or {}

        transaction = data.get(
            "transaction"
        )

        if not isinstance(transaction, dict):
            transaction = data.get("data")

        if not isinstance(transaction, dict):
            transaction = data

        nested = transaction.get(
            "transaction"
        )

        if isinstance(nested, dict):
            merged = dict(transaction)
            merged.update(nested)
            transaction = merged

        status = str(
            transaction.get(
                "status",
                ""
            )
        ).lower()

        if status != "paid":
            return "OK", 200

        payment_id = (
            transaction.get("uuid")
            or transaction.get("id")
            or transaction.get("transaction_id")
        )

        if not payment_id:
            return "OK", 200

        payment_id = str(payment_id)

        payment = get_payment(
            payment_id
        )

        if not payment:
            log.warning(
                "CasheRa unknown payment: %s",
                payment_id
            )
            return "OK", 200

        if payment["provider"] != "cashera":
            return "OK", 200

        if payment["status"] == "paid":
            return "OK", 200

        currency = str(
            transaction.get(
                "currency",
                "RUB"
            )
        ).upper()

        if currency != "RUB":
            return "OK", 200

        amount = transaction.get(
            "amount"
        )

        if amount is not None:

            try:
                amount = int(
                    float(amount)
                )
            except Exception:
                amount = None

        expected = {
            30: 12900,
            90: 37900,
            180: 65900,
            365: 108900
        }

        expected_amount = expected.get(
            payment["days"]
        )

        if (
            amount is not None
            and expected_amount is not None
            and amount != expected_amount
        ):
            log.warning(
                "CasheRa amount mismatch "
                "payment=%s got=%s expected=%s",
                payment_id,
                amount,
                expected_amount
            )
            return "OK", 200

        result = process_paid_payment(
            payment_id
        )

        if not result:
            return "OK", 200

        user_id = result["user_id"]
        days = result["days"]

        update_subscription_file(
            user_id
        )

        until = result.get(
            "subscription_until"
        )

        log.info(
            "CasheRa payment paid "
            "payment=%s user=%s days=%s until=%s",
            payment_id,
            user_id,
            days,
            until
        )

        if (
            BOT_LOOP
            and not result["already_paid"]
        ):

            try:

                asyncio.run_coroutine_threadsafe(
                    bot.send_message(
                        user_id,
                        "✅ <b>Оплата получена!</b>\n\n"
                        f"📅 Добавлено: "
                        f"<b>{days} дней</b>\n"
                        f"📆 До: "
                        f"<b>{fmt_date(until)}</b>\n\n"
                        f"🔗 <code>"
                        f"{subscription_link(user_id)}"
                        f"</code>"
                    ),
                    BOT_LOOP
                )

            except Exception:
                log.exception(
                    "Payment notification error"
                )

        return "OK", 200

    except Exception:

        log.exception(
            "CasheRa webhook error"
        )

        return "OK", 200


# =========================================================
# SUBSCRIPTION CHECKER
# =========================================================

async def subscription_checker():

    while True:

        try:

            await asyncio.to_thread(
                expire_old_subscriptions
            )

            users = await asyncio.to_thread(
                get_all_users
            )

            for user in users:

                try:

                    await asyncio.to_thread(
                        update_subscription_file,
                        user["user_id"]
                    )

                except Exception:

                    log.exception(
                        "Subscription update "
                        "failed user=%s",
                        user["user_id"]
                    )

        except Exception:

            log.exception(
                "Subscription checker error"
            )

        await asyncio.sleep(600)


# =========================================================
# FLASK
# =========================================================

def run_flask():

    app.run(
        host="0.0.0.0",
        port=PORT,
        threaded=True,
        use_reloader=False
    )


# =========================================================
# MAIN
# =========================================================

async def main():

    global BOT_LOOP

    BOT_LOOP = asyncio.get_running_loop()

    log.info(
        "☂️ ixxy VPN starting"
    )

    await asyncio.to_thread(
        init_db
    )

    await asyncio.to_thread(
        expire_old_subscriptions
    )

    checker = asyncio.create_task(
        subscription_checker()
    )

    try:

        await dp.start_polling(
            bot
        )

    finally:

        checker.cancel()

        try:
            await checker
        except asyncio.CancelledError:
            pass

        await bot.session.close()


# =========================================================
# START
# =========================================================

if __name__ == "__main__":

    flask_thread = threading.Thread(
        target=run_flask,
        daemon=True
    )

    flask_thread.start()

    asyncio.run(main())