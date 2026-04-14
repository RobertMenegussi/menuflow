import os
import sqlite3
import secrets
import string
import json
import base64
import io
import re
import urllib.request
import urllib.error
from datetime import datetime, timedelta

from flask import (
    Flask, g, jsonify, redirect, render_template, request, session, url_for, abort, make_response
)
from reportlab.lib.utils import ImageReader

# Optional deps installed via requirements
import qrcode
from reportlab.pdfgen import canvas
from reportlab.lib.units import mm
from reportlab.lib import colors

APP_SYSTEM_BRAND = "MenuFlow"
APP_SYSTEM_BYLINE = "Operação inteligente para restaurantes"
APP_AUTHOR = "Robert Castilho Menegussi • PARADevs"
APP_VERSION = "20260401-asaas-auto-pix"

ADMIN_USER = os.environ.get("ADMIN_USER", "admin")
ADMIN_PASS = os.environ.get("ADMIN_PASS", "admin123")

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "app.db")

app = Flask(__name__, static_folder=os.path.join(BASE_DIR, "static"), template_folder=os.path.join(BASE_DIR, "templates"))
app.secret_key = os.environ.get("SECRET_KEY", "dev-" + secrets.token_hex(16))

@app.context_processor
def inject_global_template_vars():
    return {"app_version": APP_VERSION, "system_brand": APP_SYSTEM_BRAND, "author": APP_AUTHOR, "byline": APP_SYSTEM_BYLINE}

@app.after_request
def add_no_cache_headers(resp):
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    resp.headers["X-MenuFlow-Version"] = APP_VERSION
    return resp

DEFAULT_SETTINGS = {
    "restaurant_name": "MenuFlow Demo",
    "restaurant_tagline": "Menu digital • pedido por mesa",
    "pix_key": "",  # chave pix (email/telefone/cpf/cnpj/aleatória)
    "pix_merchant_name": "PARADevs",
    "pix_merchant_city": "RIBEIRAO PRETO",
    "pix_txid_prefix": "MF",
    "client_primary": "#5B5AF7",
    "logo_data_uri": "",
    "logo_file_name": "",
    "asaas_enabled": "0",
    "asaas_env": "sandbox",
    "asaas_api_key": "",
    "asaas_webhook_url": "",
    "asaas_webhook_token": "",
    "asaas_webhook_email": "",
}


# ------------------------
# Helpers / DB
# ------------------------

def utcnow_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def now_br_str() -> str:
    return datetime.now().strftime("%d/%m/%Y %H:%M")


def get_db():
    if "db" not in g:
        os.makedirs(DATA_DIR, exist_ok=True)
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        g.db = conn
    return g.db


@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = get_db()
    db.executescript(
        """
        PRAGMA journal_mode=WAL;

        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            table_no TEXT NOT NULL,
            customer_name TEXT,
            status TEXT NOT NULL DEFAULT 'Novo',
            eta_minutes INTEGER,
            admin_message TEXT,
            note TEXT,
            total_cents INTEGER NOT NULL DEFAULT 0,
            is_closed INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS order_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL,
            item_id TEXT NOT NULL,
            name TEXT NOT NULL,
            qty INTEGER NOT NULL,
            price_cents INTEGER NOT NULL,
            FOREIGN KEY(order_id) REFERENCES orders(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS table_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            table_no TEXT NOT NULL,
            req_type TEXT NOT NULL,
            note TEXT,
            status TEXT NOT NULL DEFAULT 'Novo'
        );

        CREATE TABLE IF NOT EXISTS reservations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            code TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            phone TEXT NOT NULL,
            cpf TEXT,
            party_size INTEGER NOT NULL,
            starts_at TEXT NOT NULL,  -- "YYYY-MM-DDTHH:MM"
            duration_minutes INTEGER NOT NULL DEFAULT 90,
            notes TEXT,
            status TEXT NOT NULL DEFAULT 'Pendente'
        );

        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            table_no TEXT NOT NULL,
            order_id INTEGER,
            method TEXT NOT NULL, -- PIX_QR, DINHEIRO, CARTAO, OUTRO
            amount_cents INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'PENDENTE', -- PENDENTE, INFORMADO, CONFIRMADO, REJEITADO
            pix_txid TEXT,
            pix_payload TEXT,
            customer_name TEXT,
            customer_phone TEXT,
            customer_marked_at TEXT,
            admin_marked_at TEXT,
            admin_note TEXT
        );

        CREATE TABLE IF NOT EXISTS closed_tabs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            table_no TEXT NOT NULL,
            closed_at TEXT NOT NULL,
            total_cents INTEGER NOT NULL,
            items_json TEXT NOT NULL,
            orders_json TEXT NOT NULL,
            paid_cents INTEGER NOT NULL DEFAULT 0,
            pay_status TEXT NOT NULL DEFAULT 'PENDENTE' -- PENDENTE, PARCIAL, PAGO
        );

        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            type TEXT NOT NULL,
            payload TEXT
        );
        """
    )

    now = utcnow_iso()
    for k, v in DEFAULT_SETTINGS.items():
        row = db.execute("SELECT value FROM settings WHERE key=?", (k,)).fetchone()
        if not row:
            db.execute("INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)", (k, str(v), now))
    ensure_column(db, "payments", "ref_code", "TEXT")
    ensure_column(db, "payments", "closed_tab_id", "INTEGER")
    ensure_column(db, "payments", "provider", "TEXT")
    ensure_column(db, "payments", "provider_payment_id", "TEXT")
    ensure_column(db, "payments", "provider_customer_id", "TEXT")
    ensure_column(db, "payments", "provider_status", "TEXT")
    ensure_column(db, "payments", "provider_payload_json", "TEXT")
    ensure_column(db, "payments", "provider_event_id", "TEXT")
    ensure_column(db, "payments", "provider_last_check_at", "TEXT")
    ensure_column(db, "payments", "auto_confirmed_at", "TEXT")
    ensure_column(db, "payments", "pix_expires_at", "TEXT")
    ensure_column(db, "payments", "customer_tax_id", "TEXT")
    ensure_column(db, "closed_tabs", "receipt_code", "TEXT")
    ensure_column(db, "closed_tabs", "payments_json", "TEXT")

    db.execute(
        """
        CREATE TABLE IF NOT EXISTS provider_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            provider TEXT NOT NULL,
            event_id TEXT NOT NULL UNIQUE,
            event_type TEXT NOT NULL,
            created_at TEXT NOT NULL,
            payload_json TEXT
        )
        """
    )

    pay_rows = db.execute("SELECT id, created_at FROM payments WHERE ref_code IS NULL OR ref_code='' ").fetchall()
    for row in pay_rows:
        db.execute("UPDATE payments SET ref_code=? WHERE id=?", (payment_ref_for_row(int(row["id"]), row["created_at"]), int(row["id"])))

    tab_rows = db.execute("SELECT id, closed_at FROM closed_tabs WHERE receipt_code IS NULL OR receipt_code='' ").fetchall()
    for row in tab_rows:
        db.execute("UPDATE closed_tabs SET receipt_code=? WHERE id=?", (receipt_code_for_row(int(row["id"]), row["closed_at"]), int(row["id"])))

    db.commit()


def get_setting(key: str, default: str | None = None) -> str:
    db = get_db()
    row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    if not row:
        return default if default is not None else ""
    return str(row["value"])


def set_setting(key: str, value: str):
    db = get_db()
    now = utcnow_iso()
    db.execute(
        "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
        (key, value, now),
    )
    db.commit()


def setting_bool(key: str, default: bool = False) -> bool:
    raw = (get_setting(key, "1" if default else "0") or "").strip().lower()
    return raw in {"1", "true", "yes", "sim", "on"}


def digits_only(value: str | None) -> str:
    return re.sub(r"\D+", "", value or "")


def normalize_cpf_cnpj(value: str | None) -> str:
    return digits_only(value)[:14]


def cpf_cnpj_is_valid_shape(value: str | None) -> bool:
    d = normalize_cpf_cnpj(value)
    return len(d) in (11, 14)


def normalize_asaas_webhook_url(raw_url: str | None, fallback_root: str | None = None) -> str:
    url = (raw_url or "").strip()
    if not url and fallback_root:
        url = str(fallback_root).strip().rstrip("/")
    if not url:
        return ""
    if not url.startswith("http://") and not url.startswith("https://"):
        return url
    url = url.rstrip("/")
    if url.endswith("/webhooks/asaas"):
        return url
    return url + "/webhooks/asaas"


def asaas_api_base() -> str:
    env = (get_setting("asaas_env", "sandbox") or "sandbox").strip().lower()
    return "https://api.asaas.com" if env == "production" else "https://api-sandbox.asaas.com"


def asaas_is_enabled() -> bool:
    return setting_bool("asaas_enabled", False) and bool((get_setting("asaas_api_key", "") or "").strip())


class ExternalAPIError(Exception):
    pass


def http_json_request(method: str, url: str, payload=None, headers: dict | None = None, timeout: int = 20):
    body = None
    final_headers = {"Accept": "application/json"}
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        final_headers["Content-Type"] = "application/json"
    if headers:
        final_headers.update(headers)
    req = urllib.request.Request(url, data=body, headers=final_headers, method=method.upper())
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
            data = json.loads(raw) if raw else {}
            return resp.status, data
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        data = {}
        if raw:
            try:
                data = json.loads(raw)
            except Exception:
                data = {"raw": raw}
        raise ExternalAPIError(asaas_error_message(data) or f"Falha HTTP {exc.code} ao falar com o provedor.")
    except Exception as exc:
        raise ExternalAPIError(f"Falha ao falar com o provedor: {exc}")


def asaas_error_message(data) -> str:
    if isinstance(data, dict):
        errs = data.get("errors")
        if isinstance(errs, list) and errs:
            parts = []
            for item in errs:
                if isinstance(item, dict):
                    parts.append((item.get("description") or item.get("code") or "Erro").strip())
                elif item:
                    parts.append(str(item).strip())
            parts = [x for x in parts if x]
            if parts:
                return " | ".join(parts)
        for key in ("message", "error", "raw"):
            if data.get(key):
                return str(data.get(key))
    return ""


def asaas_request(method: str, path: str, payload=None):
    api_key = (get_setting("asaas_api_key", "") or "").strip()
    if not api_key:
        raise ExternalAPIError("Preencha a API Key do Asaas nas configurações do admin.")
    return http_json_request(method, asaas_api_base() + path, payload=payload, headers={"access_token": api_key})


def get_settings_dict():
    return {k: get_setting(k, str(v)) for k, v in DEFAULT_SETTINGS.items()}


def get_logo_data_uri() -> str:
    return get_setting("logo_data_uri", "").strip()


def parse_data_uri_image(data_uri: str | None):
    raw = (data_uri or "").strip()
    m = re.match(r"^data:(image/(?:png|jpeg|jpg|webp));base64,(.+)$", raw, re.I | re.S)
    if not m:
        return None
    try:
        data = base64.b64decode(m.group(2), validate=True)
    except Exception:
        return None
    return m.group(1).lower(), data


def draw_logo(c, center_x=None, top_y=None, max_w_mm: float = 28, max_h_mm: float = 16):
    parsed = parse_data_uri_image(get_logo_data_uri())
    if not parsed:
        return 0
    _, data = parsed
    try:
        reader = ImageReader(io.BytesIO(data))
        iw, ih = reader.getSize()
        if not iw or not ih:
            return 0
        max_w = max_w_mm * mm
        max_h = max_h_mm * mm
        scale = min(max_w / float(iw), max_h / float(ih))
        draw_w = float(iw) * scale
        draw_h = float(ih) * scale
        x = (center_x - draw_w / 2.0) if center_x is not None else 6 * mm
        y = (top_y - draw_h) if top_y is not None else 6 * mm
        c.drawImage(reader, x, y, width=draw_w, height=draw_h, preserveAspectRatio=True, mask='auto')
        return draw_h
    except Exception:
        return 0

def add_event(ev_type: str, payload: str | None = None):
    db = get_db()
    db.execute(
        "INSERT INTO events (created_at, type, payload) VALUES (?, ?, ?)",
        (utcnow_iso(), ev_type, payload),
    )
    db.commit()


def require_admin():
    if not session.get("admin"):
        return redirect(url_for("admin_login", next=request.path))
    return None


def safe_admin_next(raw: str | None) -> str:
    raw = (raw or "").strip()
    if raw.startswith("/admin") and not raw.startswith("//"):
        return raw
    return url_for("admin_orders")


def money_br(cents: int) -> str:
    v = (cents or 0) / 100.0
    s = f"{v:,.2f}"
    s = s.replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {s}"


def gen_code(prefix="R") -> str:
    alphabet = string.ascii_uppercase + string.digits
    return f"{prefix}-{''.join(secrets.choice(alphabet) for _ in range(5))}"


def parse_local_dt(starts_at: str) -> datetime:
    return datetime.strptime(starts_at, "%Y-%m-%dT%H:%M")


def ensure_column(db, table: str, column: str, ddl: str):
    cols = {row["name"] for row in db.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in cols:
        db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def parse_iso_safe(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        try:
            return datetime.fromisoformat(str(value).replace("Z", ""))
        except Exception:
            return None


def payment_ref_for_row(payment_id: int, created_at: str | None = None) -> str:
    dt = parse_iso_safe(created_at) or datetime.now()
    return f"PAG-{dt.strftime('%y%m%d')}-{payment_id:06d}"


def receipt_code_for_row(tab_id: int, closed_at: str | None = None) -> str:
    dt = parse_iso_safe(closed_at) or datetime.now()
    return f"CMD-{dt.strftime('%y%m%d')}-{tab_id:06d}"


def iso_to_br(iso_value: str | None, with_seconds: bool = False) -> str:
    dt = parse_iso_safe(iso_value)
    if not dt:
        return "-"
    fmt = "%d/%m/%Y %H:%M:%S" if with_seconds else "%d/%m/%Y %H:%M"
    return dt.strftime(fmt)


def wait_minutes_from(created_at: str | None) -> int:
    dt = parse_iso_safe(created_at)
    if not dt:
        return 0
    return max(0, int((datetime.utcnow() - dt.replace(tzinfo=None)).total_seconds() // 60))


# ------------------------
# PIX helpers (BR Code)
# ------------------------

def _tlv(tag: str, value: str) -> str:
    value = value or ""
    return f"{tag}{len(value):02d}{value}"


def _crc16(payload: str) -> str:
    crc = 0xFFFF
    for ch in payload.encode("utf-8"):
        crc ^= ch << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = (crc << 1) ^ 0x1021
            else:
                crc <<= 1
            crc &= 0xFFFF
    return f"{crc:04X}"


def build_pix_payload(pix_key: str, merchant_name: str, merchant_city: str, amount_reais: str, txid: str) -> str:
    pix_key = (pix_key or "").strip()
    merchant_name = (merchant_name or "").strip()[:25]
    merchant_city = (merchant_city or "").strip()[:15]
    txid = (txid or "").strip()[:25] or "***"

    mai = "".join([
        _tlv("00", "br.gov.bcb.pix"),
        _tlv("01", pix_key),
    ])

    add = _tlv("05", txid)
    add = _tlv("62", add)

    payload = "".join([
        _tlv("00", "01"),
        _tlv("26", mai),
        _tlv("52", "0000"),
        _tlv("53", "986"),
        _tlv("54", amount_reais),
        _tlv("58", "BR"),
        _tlv("59", merchant_name),
        _tlv("60", merchant_city),
        add,
        "6304",
    ])

    return payload + _crc16(payload)


def qr_png_data_uri(payload: str) -> str:
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=10, border=2)
    qr.add_data(payload)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/png;base64,{b64}"


def valid_pix_key(pix_key: str) -> bool:
    return len((pix_key or "").strip()) >= 8


def should_check_provider(payment_row, min_seconds: int = 8) -> bool:
    last = parse_iso_safe(payment_row["provider_last_check_at"]) if payment_row and payment_row["provider_last_check_at"] else None
    if not last:
        return True
    return (datetime.utcnow() - last.replace(tzinfo=None)).total_seconds() >= min_seconds


def map_asaas_to_local_status(provider_status: str | None = None, event_type: str | None = None, current_status: str | None = None) -> str:
    provider_status = (provider_status or "").strip().upper()
    event_type = (event_type or "").strip().upper()
    current_status = (current_status or "PENDENTE").strip().upper()
    if event_type in {"PAYMENT_RECEIVED", "PAYMENT_CONFIRMED"}:
        return "CONFIRMADO"
    if event_type in {"PAYMENT_REFUNDED", "PAYMENT_DELETED", "PAYMENT_RECEIVED_IN_CASH_UNDONE"}:
        return "REJEITADO"
    if provider_status in {"RECEIVED", "CONFIRMED", "RECEIVED_IN_CASH"}:
        return "CONFIRMADO"
    if provider_status in {"REFUNDED", "RECEIVED_IN_CASH_UNDONE", "DELETED"}:
        return "REJEITADO"
    if current_status in {"CONFIRMADO", "REJEITADO"}:
        return current_status
    return "PENDENTE"


def update_payment_from_provider(db, payment_row, provider_payment: dict | None = None, event_type: str | None = None, event_id: str | None = None):
    if not payment_row:
        return None
    provider_payment = provider_payment or {}
    provider_status = (provider_payment.get("status") or payment_row["provider_status"] or "").strip()
    local_status = map_asaas_to_local_status(provider_status, event_type, payment_row["status"])
    now = utcnow_iso()
    payload_json = json.dumps(provider_payment, ensure_ascii=False) if provider_payment else payment_row["provider_payload_json"]
    admin_marked_at = payment_row["admin_marked_at"]
    auto_confirmed_at = payment_row["auto_confirmed_at"]
    admin_note = payment_row["admin_note"]
    newly_confirmed = local_status == "CONFIRMADO" and payment_row["status"] != "CONFIRMADO"
    newly_rejected = local_status == "REJEITADO" and payment_row["status"] != "REJEITADO"

    if newly_confirmed:
        admin_marked_at = admin_marked_at or now
        auto_confirmed_at = auto_confirmed_at or now
        if not admin_note:
            admin_note = "Confirmado automaticamente via Asaas"
    elif newly_rejected and not admin_note:
        admin_note = "Atualizado automaticamente via Asaas"

    db.execute(
        """
        UPDATE payments
        SET status=?, provider_status=?, provider_payload_json=?, provider_event_id=?, provider_last_check_at=?, updated_at=?, admin_marked_at=?, auto_confirmed_at=?, admin_note=?, pix_expires_at=?
        WHERE id=?
        """,
        (
            local_status,
            provider_status or None,
            payload_json,
            (event_id or payment_row["provider_event_id"] or None),
            now,
            now,
            admin_marked_at,
            auto_confirmed_at,
            admin_note,
            provider_payment.get("expirationDate") or payment_row["pix_expires_at"],
            int(payment_row["id"]),
        ),
    )

    linked_tab_id = payment_row["closed_tab_id"] or link_payment_to_relevant_tab(db, int(payment_row["id"]))
    if linked_tab_id:
        sync_closed_tab(db, int(linked_tab_id))

    if newly_confirmed:
        add_event("pix_auto_confirmed", str(payment_row["id"]))
    elif newly_rejected:
        add_event("pix_auto_rejected", str(payment_row["id"]))
    return local_status


def find_payment_by_provider_reference(db, provider_payment: dict | None):
    provider_payment = provider_payment or {}
    provider_payment_id = (provider_payment.get("id") or "").strip()
    if provider_payment_id:
        row = db.execute("SELECT * FROM payments WHERE provider='ASAAS' AND provider_payment_id=? ORDER BY id DESC LIMIT 1", (provider_payment_id,)).fetchone()
        if row:
            return row
    external_reference = (provider_payment.get("externalReference") or "").strip()
    if external_reference:
        row = db.execute("SELECT * FROM payments WHERE ref_code=? ORDER BY id DESC LIMIT 1", (external_reference,)).fetchone()
        if row:
            return row
    return None


def sync_asaas_payment_status(payment_id: int):
    db = get_db()
    payment = db.execute("SELECT * FROM payments WHERE id=?", (payment_id,)).fetchone()
    if not payment or payment["provider"] != "ASAAS" or not payment["provider_payment_id"] or not asaas_is_enabled():
        return payment
    if payment["status"] in {"CONFIRMADO", "REJEITADO"} and not should_check_provider(payment, 30):
        return payment
    if payment["status"] in {"PENDENTE", "INFORMADO"} and not should_check_provider(payment, 8):
        return payment
    try:
        _, provider_payment = asaas_request("GET", f"/v3/payments/{payment['provider_payment_id']}")
        update_payment_from_provider(db, payment, provider_payment=provider_payment)
        db.commit()
        payment = db.execute("SELECT * FROM payments WHERE id=?", (payment_id,)).fetchone()
    except Exception:
        db.execute("UPDATE payments SET provider_last_check_at=? WHERE id=?", (utcnow_iso(), payment_id))
        db.commit()
    return payment


def log_provider_event(db, provider: str, event_id: str, event_type: str, payload: dict | None = None) -> bool:
    if not event_id:
        return False
    try:
        db.execute(
            "INSERT INTO provider_events (provider, event_id, event_type, created_at, payload_json) VALUES (?, ?, ?, ?, ?)",
            (provider, event_id, event_type, utcnow_iso(), json.dumps(payload or {}, ensure_ascii=False)),
        )
        db.commit()
        return True
    except sqlite3.IntegrityError:
        return False


def create_asaas_pix_charge(table_no: str, order_id, amount_cents: int, customer_name: str = "", customer_phone: str = "", customer_tax_id: str = ""):
    db = get_db()
    now = utcnow_iso()
    local_customer_name = (customer_name or f"Mesa {table_no}").strip()[:60] or f"Mesa {table_no}"
    local_customer_phone = (customer_phone or "").strip()[:30]
    local_customer_tax_id = normalize_cpf_cnpj(customer_tax_id)
    if not cpf_cnpj_is_valid_shape(local_customer_tax_id):
        raise ExternalAPIError("Para pagar via Pix, informe um CPF ou CNPJ válido do pagador.")
    cur = db.execute(
        """
        INSERT INTO payments (created_at, updated_at, table_no, order_id, method, amount_cents, status, customer_name, customer_phone, customer_tax_id, provider)
        VALUES (?, ?, ?, ?, 'PIX_QR', ?, 'PENDENTE', ?, ?, ?, 'ASAAS')
        """,
        (now, now, table_no, int(order_id) if str(order_id).isdigit() else None, amount_cents, local_customer_name or None, local_customer_phone or None, local_customer_tax_id),
    )
    payment_id = cur.lastrowid
    ref_code = payment_ref_for_row(payment_id, now)
    db.execute("UPDATE payments SET ref_code=? WHERE id=?", (ref_code, payment_id))
    db.commit()

    try:
        customer_payload = {"name": local_customer_name, "cpfCnpj": local_customer_tax_id}
        phone_digits = digits_only(local_customer_phone)
        if len(phone_digits) >= 10:
            customer_payload["mobilePhone"] = phone_digits[-11:]
        _, customer_obj = asaas_request("POST", "/v3/customers", payload=customer_payload)
        _, payment_obj = asaas_request(
            "POST",
            "/v3/payments",
            payload={
                "customer": customer_obj.get("id"),
                "billingType": "PIX",
                "value": round(amount_cents / 100.0, 2),
                "dueDate": datetime.now().strftime("%Y-%m-%d"),
                "description": f"MenuFlow mesa {table_no} • {ref_code}",
                "externalReference": ref_code,
            },
        )
        provider_payment_id = payment_obj.get("id")
        if not provider_payment_id:
            raise ExternalAPIError("O Asaas não retornou o identificador da cobrança.")
        _, qr_obj = asaas_request("GET", f"/v3/payments/{provider_payment_id}/pixQrCode")
        payload = (qr_obj.get("payload") or "").strip()
        encoded_image = (qr_obj.get("encodedImage") or "").strip()
        if encoded_image and not encoded_image.startswith("data:image"):
            qr_uri = f"data:image/png;base64,{encoded_image}"
        else:
            qr_uri = encoded_image or (qr_png_data_uri(payload) if payload else "")

        db.execute(
            """
            UPDATE payments
            SET pix_txid=?, pix_payload=?, provider_payment_id=?, provider_customer_id=?, provider_status=?, provider_payload_json=?, pix_expires_at=?, updated_at=?
            WHERE id=?
            """,
            (
                (payment_obj.get("pixTransaction") or {}).get("endToEndIdentifier") or provider_payment_id,
                payload or None,
                provider_payment_id,
                customer_obj.get("id"),
                payment_obj.get("status") or "PENDING",
                json.dumps({"payment": payment_obj, "pixQrCode": qr_obj}, ensure_ascii=False),
                qr_obj.get("expirationDate"),
                utcnow_iso(),
                payment_id,
            ),
        )
        db.commit()
        add_event("pix_created", str(payment_id))
        return {
            "ok": True,
            "payment_id": payment_id,
            "txid": provider_payment_id,
            "payload": payload,
            "qr": qr_uri,
            "amount": money_br(amount_cents),
            "ref_code": ref_code,
            "provider": "ASAAS",
            "provider_payment_id": provider_payment_id,
            "auto_confirm": True,
            "expires_at": qr_obj.get("expirationDate"),
        }
    except Exception as exc:
        db.execute("DELETE FROM payments WHERE id=?", (payment_id,))
        db.commit()
        raise ExternalAPIError(str(exc))


def payments_for_table_since(db, table_no: str, since_iso: str):
    rows = db.execute(
        """
        SELECT id, ref_code, method, amount_cents, status, pix_txid, admin_marked_at, customer_marked_at, admin_note, closed_tab_id
        FROM payments
        WHERE table_no=? AND created_at >= ?
        ORDER BY created_at ASC
        """,
        (table_no, since_iso),
    ).fetchall()
    return [dict(r) for r in rows]


def has_open_orders(db, table_no: str) -> bool:
    row = db.execute(
        "SELECT 1 FROM orders WHERE table_no=? AND is_closed=0 LIMIT 1",
        (table_no,),
    ).fetchone()
    return row is not None


def get_latest_open_closed_tab(db, table_no: str):
    return db.execute(
        """
        SELECT * FROM closed_tabs
        WHERE table_no=? AND pay_status != 'PAGO'
        ORDER BY closed_at DESC, id DESC
        LIMIT 1
        """,
        (table_no,),
    ).fetchone()


def link_payment_to_relevant_tab(db, payment_id: int):
    payment = db.execute("SELECT * FROM payments WHERE id=?", (payment_id,)).fetchone()
    if not payment:
        return None
    if payment["closed_tab_id"]:
        return int(payment["closed_tab_id"])
    table_no = str(payment["table_no"] or "").strip()
    if not table_no or has_open_orders(db, table_no):
        return None
    tab = get_latest_open_closed_tab(db, table_no)
    if not tab:
        return None
    db.execute("UPDATE payments SET closed_tab_id=?, updated_at=? WHERE id=?", (int(tab["id"]), utcnow_iso(), payment_id))
    return int(tab["id"])


def sync_closed_tab(db, tab_id: int):
    tab = db.execute("SELECT * FROM closed_tabs WHERE id=?", (tab_id,)).fetchone()
    if not tab:
        return None
    payments = db.execute(
        "SELECT * FROM payments WHERE closed_tab_id=? ORDER BY created_at ASC, id ASC",
        (tab_id,),
    ).fetchall()
    paid_cents = sum(int(p["amount_cents"] or 0) for p in payments if p["status"] == "CONFIRMADO")
    total_cents = int(tab["total_cents"] or 0)
    if paid_cents <= 0:
        pay_status = "PENDENTE"
    elif paid_cents < total_cents:
        pay_status = "PARCIAL"
    else:
        pay_status = "PAGO"
    payments_json = json.dumps([dict(p) for p in payments], ensure_ascii=False)
    db.execute(
        "UPDATE closed_tabs SET paid_cents=?, pay_status=?, payments_json=? WHERE id=?",
        (paid_cents, pay_status, payments_json, tab_id),
    )
    return {"tab_id": tab_id, "paid_cents": paid_cents, "pay_status": pay_status}


def sync_all_closed_tabs(db):
    tabs = db.execute("SELECT id FROM closed_tabs ORDER BY id ASC").fetchall()
    for row in tabs:
        sync_closed_tab(db, int(row["id"]))


def create_pending_cashier_payment(db, table_no: str, amount_cents: int, closed_tab_id: int | None = None, note: str | None = None):
    amount_cents = int(amount_cents or 0)
    if amount_cents <= 0:
        return None
    now = utcnow_iso()
    cur = db.execute(
        """
        INSERT INTO payments (created_at, updated_at, table_no, order_id, method, amount_cents, status, admin_note, closed_tab_id)
        VALUES (?, ?, ?, NULL, 'CAIXA', ?, 'PENDENTE', ?, ?)
        """,
        (now, now, table_no, amount_cents, note or 'Gerado automaticamente ao fechar comanda', closed_tab_id),
    )
    payment_id = cur.lastrowid
    db.execute("UPDATE payments SET ref_code=? WHERE id=?", (payment_ref_for_row(payment_id, now), payment_id))
    return payment_id


def ensure_settlement_payment_for_tab(db, tab_id: int):
    tab = db.execute("SELECT * FROM closed_tabs WHERE id=?", (tab_id,)).fetchone()
    if not tab:
        return None
    confirmed_cents = db.execute(
        "SELECT COALESCE(SUM(amount_cents),0) AS cents FROM payments WHERE closed_tab_id=? AND status='CONFIRMADO'",
        (tab_id,),
    ).fetchone()["cents"]
    unsettled_cents = db.execute(
        "SELECT COALESCE(SUM(amount_cents),0) AS cents FROM payments WHERE closed_tab_id=? AND status IN ('PENDENTE','INFORMADO')",
        (tab_id,),
    ).fetchone()["cents"]
    remaining = max(int(tab["total_cents"] or 0) - int(confirmed_cents or 0) - int(unsettled_cents or 0), 0)
    if remaining <= 0:
        return None
    return create_pending_cashier_payment(db, str(tab["table_no"]), remaining, int(tab_id), 'Gerado automaticamente ao fechar comanda')


# ------------------------
# Demo menu data
# ------------------------

MENU = [
  {
    "id": "principais",
    "label": "Principais",
    "items": [
      {
        "id": "pasta-alfredo",
        "name": "Fettuccine Alfredo",
        "description": "Molho Alfredo cremoso, frango grelhado e parmesão.",
        "details": "Massa fresca ao ponto, Alfredo cremoso, frango grelhado, parmesão e toque de limão siciliano.",
        "price_cents": 5890,
        "tag": "Assinatura",
        "image": "pasta_sm.jpg"
      },
      {
        "id": "burger-house",
        "name": "House Burger",
        "description": "Blend da casa, cheddar, bacon e molho especial.",
        "details": "Pão brioche, 160g blend bovino, cheddar, bacon crocante, alface, tomate e molho da casa.",
        "price_cents": 4490,
        "tag": "Top",
        "image": "burger_sm.jpg"
      },
      {
        "id": "pizza-marg",
        "name": "Margherita (fatia)",
        "description": "Mussarela, tomate, manjericão e azeite extra virgem.",
        "details": "Uma fatia generosa. Clássica, perfumada e leve.",
        "price_cents": 2190,
        "tag": "Clássico",
        "image": "pizza_sm.jpg"
      }
    ]
  },
  {
    "id": "entradas",
    "label": "Entradas",
    "items": [
      {
        "id": "salad-fresh",
        "name": "Salada da Horta",
        "description": "Folhas, tomatinhos, brotos e vinagrete cítrico.",
        "details": "Salada fresca com brotos, pepino e tomate. Molho cítrico separado.",
        "price_cents": 2890,
        "tag": "Fresh",
        "image": "salad_sm.jpg"
      }
    ]
  },
  {
    "id": "bebidas",
    "label": "Bebidas",
    "items": [
      {
        "id": "agua-test",
        "name": "Água (teste Pix)",
        "description": "Item baratinho pra testar a transação e o fluxo de pagamento.",
        "details": "Use esse item para testar Pix na apresentação (valor bem baixo).",
        "price_cents": 10,
        "tag": "Teste",
        "image": "drink_sm.jpg"
      },
      {
        "id": "drink-house",
        "name": "Soda Italiana",
        "description": "Refrescante, cítrica e bem gelada.",
        "details": "Base cítrica + xarope artesanal. Pode pedir sem álcool (mocktail).",
        "price_cents": 1890,
        "tag": "Gelado",
        "image": "drink_sm.jpg"
      }
    ]
  },
  {
    "id": "sobremesas",
    "label": "Sobremesas",
    "items": [
      {
        "id": "donut-mochi",
        "name": "Donut Mochi",
        "description": "Macia por dentro, crocante por fora, cobertura delicada.",
        "details": "Doce leve com textura macia. Perfeito pra fechar a refeição.",
        "price_cents": 1590,
        "tag": "Doce",
        "image": "donut_sm.jpg"
      }
    ]
  }
]


def table_open_snapshot(db, table_no: str):
    orders = db.execute(
        "SELECT * FROM orders WHERE table_no=? AND is_closed=0 ORDER BY created_at ASC",
        (table_no,),
    ).fetchall()
    if not orders:
        return None

    items = db.execute(
        """
        SELECT name, SUM(qty) AS qty, price_cents
        FROM order_items oi
        JOIN orders o ON o.id=oi.order_id
        WHERE o.table_no=? AND o.is_closed=0
        GROUP BY name, price_cents
        ORDER BY name
        """,
        (table_no,),
    ).fetchall()

    total = sum(int(i["qty"] or 0) * int(i["price_cents"] or 0) for i in items)
    session_start = str(orders[0]["created_at"])
    session_payments = payments_for_table_since(db, table_no, session_start)
    paid = sum(int(p.get("amount_cents") or 0) for p in session_payments if p.get("status") == "CONFIRMADO")
    reported = sum(int(p.get("amount_cents") or 0) for p in session_payments if p.get("status") == "INFORMADO")
    due = max(total - paid, 0)
    oldest = orders[0]["created_at"]
    return {
        "table_no": table_no,
        "orders": [dict(o) for o in orders],
        "items": [{"name": i["name"], "qty": int(i["qty"] or 0), "price_cents": int(i["price_cents"] or 0)} for i in items],
        "payments": session_payments,
        "total_cents": total,
        "paid_cents": paid,
        "reported_cents": reported,
        "due_cents": due,
        "oldest_at": oldest,
        "oldest_br": iso_to_br(oldest),
        "orders_count": len(orders),
        "money_total": money_br(total),
        "money_paid": money_br(paid),
        "money_due": money_br(due),
    }

# ------------------------
# Routes: client
# ------------------------

@app.route("/")
def root():
    return redirect(url_for("client"))


@app.route("/health")
def health():
    return jsonify({"ok": True, "time": utcnow_iso()})


@app.route("/client")
def client():
    init_db()
    return render_template(
        "client.html",
        system_brand=APP_SYSTEM_BRAND,
        app_name=get_setting("restaurant_name", DEFAULT_SETTINGS["restaurant_name"]),
        tagline=get_setting("restaurant_tagline", DEFAULT_SETTINGS["restaurant_tagline"]),
        client_primary=get_setting("client_primary", DEFAULT_SETTINGS["client_primary"]),
        menu=MENU,
    )


@app.route("/reserve")
def reserve():
    init_db()
    return render_template("reserve.html", system_brand=APP_SYSTEM_BRAND, app_name=get_setting("restaurant_name"))


@app.route("/reserve/consulta")
def reserve_consulta():
    init_db()
    return render_template("reserve_consulta.html", system_brand=APP_SYSTEM_BRAND, app_name=get_setting("restaurant_name"))


# ------------------------
# Admin pages
# ------------------------

@app.route("/admin")
def admin_home():
    if not session.get("admin"):
        return redirect(url_for("admin_login"))
    return redirect(url_for("admin_orders"))


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    init_db()
    if request.method == "POST":
        u = request.form.get("username", "")
        p = request.form.get("password", "")
        if u == ADMIN_USER and p == ADMIN_PASS:
            session["admin"] = True
            nxt = safe_admin_next(request.args.get("next"))
            return redirect(nxt)
        return render_template("admin_login.html", system_brand=APP_SYSTEM_BRAND, app_name=get_setting("restaurant_name"), error="Usuário ou senha inválidos.")
    return render_template("admin_login.html", system_brand=APP_SYSTEM_BRAND, app_name=get_setting("restaurant_name"), error=None)


@app.route("/admin/logout")
def admin_logout():
    session.clear()
    return redirect(url_for("admin_login"))


@app.route("/admin/pedidos")
def admin_orders():
    r = require_admin()
    if r:
        return r
    init_db()
    return render_template("admin_orders.html", system_brand=APP_SYSTEM_BRAND, app_name=get_setting("restaurant_name"))


@app.route("/admin/comandas")
def admin_comandas():
    r = require_admin()
    if r:
        return r
    init_db()
    return render_template("admin_comandas.html", system_brand=APP_SYSTEM_BRAND, app_name=get_setting("restaurant_name"))


@app.route("/admin/pagamentos")
def admin_payments():
    r = require_admin()
    if r:
        return r
    init_db()
    return render_template("admin_payments.html", system_brand=APP_SYSTEM_BRAND, app_name=get_setting("restaurant_name"))


@app.route("/admin/historico")
def admin_history():
    r = require_admin()
    if r:
        return r
    init_db()
    return render_template("admin_history.html", system_brand=APP_SYSTEM_BRAND, app_name=get_setting("restaurant_name"))


@app.route("/admin/relatorios")
def admin_reports():
    r = require_admin()
    if r:
        return r
    init_db()
    return render_template("admin_reports.html", system_brand=APP_SYSTEM_BRAND, app_name=get_setting("restaurant_name"))


@app.route("/admin/config")
def admin_settings():
    r = require_admin()
    if r:
        return r
    init_db()
    return render_template(
        "admin_settings.html",
        system_brand=APP_SYSTEM_BRAND,
        app_name=get_setting("restaurant_name"),
        settings=get_settings_dict(),
        author=APP_AUTHOR,
    )


@app.route("/admin/reservas")
def admin_reservas():
    r = require_admin()
    if r:
        return r
    init_db()
    return render_template("admin_reservas.html", system_brand=APP_SYSTEM_BRAND, app_name=get_setting("restaurant_name"))


@app.route("/admin/solicitacoes")
def admin_requests():
    r = require_admin()
    if r:
        return r
    init_db()
    return render_template("admin_requests.html", system_brand=APP_SYSTEM_BRAND, app_name=get_setting("restaurant_name"))


@app.route("/admin/print/table/<table_no>")
def print_table(table_no):
    r = require_admin()
    if r:
        return r
    init_db()
    db = get_db()
    orders = db.execute(
        "SELECT * FROM orders WHERE table_no=? AND is_closed=0 ORDER BY created_at ASC",
        (table_no,),
    ).fetchall()
    if not orders:
        abort(404)

    items = db.execute(
        """
        SELECT name, SUM(qty) AS qty, price_cents
        FROM order_items oi
        JOIN orders o ON o.id=oi.order_id
        WHERE o.table_no=? AND o.is_closed=0
        GROUP BY name, price_cents
        ORDER BY name
        """,
        (table_no,),
    ).fetchall()
    total = sum(int(i["qty"]) * int(i["price_cents"]) for i in items)
    return render_template(
        "print_receipt.html",
        system_brand=APP_SYSTEM_BRAND,
        app_name=get_setting("restaurant_name"),
        table_no=table_no,
        items=items,
        total_cents=total,
        now=now_br_str(),
        logo_data_uri=get_logo_data_uri(),
        restaurant_tagline=get_setting("restaurant_tagline"),
    )


@app.route("/admin/print/order/<int:order_id>")
def print_order(order_id):
    r = require_admin()
    if r:
        return r
    init_db()
    db = get_db()
    o = db.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
    if not o:
        abort(404)
    items = db.execute("SELECT * FROM order_items WHERE order_id=?", (order_id,)).fetchall()
    return render_template(
        "print_kitchen.html",
        system_brand=APP_SYSTEM_BRAND,
        app_name=get_setting("restaurant_name"),
        order=o,
        items=items,
        now=now_br_str(),
        logo_data_uri=get_logo_data_uri(),
        restaurant_tagline=get_setting("restaurant_tagline"),
    )


# ------------------------
# PDF exports
# ------------------------


def _pdf_response(filename: str, draw_fn):
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    draw_fn(c)
    c.showPage()
    c.save()
    pdf = buf.getvalue()
    resp = make_response(pdf)
    resp.headers["Content-Type"] = "application/pdf"
    resp.headers["Content-Disposition"] = f'inline; filename="{filename}"'
    return resp


def _pdf_header(c, title: str, subtitle: str = ""):
    w, h = c._pagesize
    draw_logo(c, center_x=w - 26 * mm, top_y=h - 10 * mm, max_w_mm=22, max_h_mm=14)
    c.setFillColor(colors.HexColor("#111827"))
    c.setFont("Helvetica-Bold", 16)
    c.drawString(18 * mm, h - 20 * mm, title)
    c.setFont("Helvetica", 10)
    c.setFillColor(colors.HexColor("#4B5563"))
    if subtitle:
        c.drawString(18 * mm, h - 26 * mm, subtitle)
    c.setFillColor(colors.HexColor("#5B5AF7"))
    c.rect(18 * mm, h - 28.5 * mm, 30 * mm, 1.6 * mm, stroke=0, fill=1)


@app.route("/admin/pdf/fechamento")
def pdf_daily():
    r = require_admin()
    if r:
        return r
    init_db()
    day = (request.args.get("date") or "").strip()
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", day):
        day = datetime.now().strftime("%Y-%m-%d")

    db = get_db()
    tabs = db.execute(
        "SELECT * FROM closed_tabs WHERE substr(closed_at,1,10)=? ORDER BY closed_at DESC",
        (day,),
    ).fetchall()

    total = sum(int(t["total_cents"]) for t in tabs)
    paid = sum(int(t["paid_cents"]) for t in tabs)

    def draw(c):
        c.setPageSize((210 * mm, 297 * mm))
        _pdf_header(c, "Fechamento do dia", f"{day} • {get_setting('restaurant_name')} • {APP_SYSTEM_BRAND}")

        y = 255 * mm
        c.setFont("Helvetica-Bold", 11)
        c.setFillColor(colors.HexColor("#111827"))
        c.drawString(18 * mm, y, "Resumo")
        y -= 8 * mm
        c.setFont("Helvetica", 10)
        c.setFillColor(colors.HexColor("#374151"))
        c.drawString(18 * mm, y, f"Total vendido: {money_br(total)}")
        y -= 6 * mm
        c.drawString(18 * mm, y, f"Total confirmado: {money_br(paid)}")
        y -= 6 * mm
        c.drawString(18 * mm, y, f"Diferença: {money_br(total - paid)}")

        y -= 12 * mm
        c.setFont("Helvetica-Bold", 11)
        c.setFillColor(colors.HexColor("#111827"))
        c.drawString(18 * mm, y, "Comandas fechadas")
        y -= 8 * mm

        c.setFont("Helvetica-Bold", 9)
        c.setFillColor(colors.HexColor("#6B7280"))
        c.drawString(18 * mm, y, "Mesa")
        c.drawString(40 * mm, y, "Hora")
        c.drawString(62 * mm, y, "Total")
        c.drawString(86 * mm, y, "Pago")
        c.drawString(108 * mm, y, "Status")
        y -= 4 * mm
        c.setStrokeColor(colors.HexColor("#E5E7EB"))
        c.line(18 * mm, y, 190 * mm, y)
        y -= 6 * mm

        c.setFont("Helvetica", 9)
        c.setFillColor(colors.HexColor("#111827"))
        for t in tabs[:28]:
            if y < 30 * mm:
                break
            try:
                hhmm = datetime.fromisoformat(str(t["closed_at"]).replace("Z", "")).strftime("%H:%M")
            except Exception:
                hhmm = str(t["closed_at"])[11:16]

            c.drawString(18 * mm, y, str(t["table_no"]))
            c.drawString(40 * mm, y, hhmm)
            c.drawRightString(82 * mm, y, money_br(int(t["total_cents"])))
            c.drawRightString(106 * mm, y, money_br(int(t["paid_cents"])))
            c.drawString(108 * mm, y, str(t["pay_status"]))
            y -= 6 * mm

        c.setFillColor(colors.HexColor("#9CA3AF"))
        c.setFont("Helvetica", 8)
        c.drawString(18 * mm, 16 * mm, f"Gerado por {APP_SYSTEM_BRAND} • {APP_AUTHOR} • {now_br_str()}")

    return _pdf_response(f"fechamento-{day}.pdf", draw)


@app.route("/admin/pdf/comanda/<int:tab_id>")
def pdf_comanda(tab_id: int):
    r = require_admin()
    if r:
        return r
    init_db()
    db = get_db()
    t = db.execute("SELECT * FROM closed_tabs WHERE id=?", (tab_id,)).fetchone()
    if not t:
        abort(404)

    items = json.loads(t["items_json"]) if t["items_json"] else []
    payments = json.loads(t["payments_json"] or "[]") if t["payments_json"] else []

    def draw(c):
        c.setPageSize((80 * mm, 200 * mm))
        w, h = c._pagesize
        logo_h = draw_logo(c, center_x=w/2, top_y=h - 6 * mm, max_w_mm=26, max_h_mm=14)
        title_y = h - (8 * mm + logo_h)
        c.setFillColor(colors.HexColor("#111827"))
        c.setFont("Helvetica-Bold", 12)
        c.drawCentredString(w / 2, title_y, get_setting("restaurant_name"))
        c.setFont("Helvetica", 8)
        c.setFillColor(colors.HexColor("#6B7280"))
        c.drawCentredString(w / 2, title_y - 5 * mm, APP_SYSTEM_BRAND)

        y = title_y - 12 * mm
        c.setFont("Helvetica-Bold", 9)
        c.setFillColor(colors.HexColor("#111827"))
        c.drawString(6 * mm, y, f"Mesa {t['table_no']}  •  #{t['id']}")
        y -= 5 * mm
        c.setFont("Helvetica", 8)
        c.setFillColor(colors.HexColor("#6B7280"))
        c.drawString(6 * mm, y, f"Recibo: {t['receipt_code'] or receipt_code_for_row(t['id'], t['closed_at'])}")
        y -= 4.5 * mm
        c.drawString(6 * mm, y, f"Fechado: {iso_to_br(t['closed_at'], with_seconds=True)}")
        y -= 6 * mm
        c.setStrokeColor(colors.HexColor("#E5E7EB"))
        c.line(6 * mm, y, w - 6 * mm, y)
        y -= 6 * mm

        c.setFont("Helvetica", 8)
        c.setFillColor(colors.HexColor("#111827"))
        for it in items:
            if y < 22 * mm:
                break
            line = f"{it['qty']}x {it['name']}"
            c.drawString(6 * mm, y, line[:28])
            c.drawRightString(w - 6 * mm, y, money_br(int(it["qty"]) * int(it["price_cents"])))
            y -= 5 * mm

        y -= 2 * mm
        c.line(6 * mm, y, w - 6 * mm, y)
        y -= 6 * mm
        c.setFont("Helvetica-Bold", 10)
        c.drawString(6 * mm, y, "Total")
        c.drawRightString(w - 6 * mm, y, money_br(int(t["total_cents"])))
        y -= 6 * mm
        c.setFont("Helvetica", 8)
        c.setFillColor(colors.HexColor("#6B7280"))
        c.drawString(6 * mm, y, f"Pago: {money_br(int(t['paid_cents']))} • {t['pay_status']}")
        if payments:
            y -= 5 * mm
            c.setFillColor(colors.HexColor("#111827"))
            c.setFont("Helvetica-Bold", 8)
            c.drawString(6 * mm, y, "Pagamentos")
            y -= 4.5 * mm
            c.setFont("Helvetica", 7.5)
            for p in payments[:5]:
                if y < 14 * mm:
                    break
                c.drawString(6 * mm, y, f"{p.get('ref_code') or ('#'+str(p.get('id')))} • {p.get('method')} • {money_br(int(p.get('amount_cents') or 0))}")
                y -= 4 * mm

    return _pdf_response(f"comanda-{tab_id}.pdf", draw)


@app.route("/admin/pdf/pagamentos")
def pdf_payments_day():
    r = require_admin()
    if r:
        return r
    init_db()
    day = (request.args.get("date") or "").strip()
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", day):
        day = datetime.now().strftime("%Y-%m-%d")
    db = get_db()
    rows = db.execute("SELECT * FROM payments WHERE substr(created_at,1,10)=? ORDER BY created_at DESC", (day,)).fetchall()

    def draw(c):
        c.setPageSize((210 * mm, 297 * mm))
        _pdf_header(c, "Pagamentos do dia", f"{day} • {get_setting('restaurant_name')} • {APP_SYSTEM_BRAND}")
        y = 255 * mm
        c.setFont("Helvetica-Bold", 9)
        c.setFillColor(colors.HexColor("#6B7280"))
        headers = [(18, "Ref"), (52, "Hora"), (72, "Mesa"), (92, "Método"), (120, "Valor"), (150, "Status")]
        for x, label in headers:
            c.drawString(x * mm, y, label)
        y -= 4 * mm
        c.line(18 * mm, y, 192 * mm, y)
        y -= 6 * mm
        c.setFont("Helvetica", 8.5)
        c.setFillColor(colors.HexColor("#111827"))
        for p in rows[:34]:
            if y < 25 * mm:
                break
            c.drawString(18 * mm, y, str(p["ref_code"] or f"#{p['id']}" )[:18])
            c.drawString(52 * mm, y, iso_to_br(p["created_at"])[11:16])
            c.drawString(72 * mm, y, str(p["table_no"]))
            c.drawString(92 * mm, y, str(p["method"])[:16])
            c.drawRightString(145 * mm, y, money_br(int(p["amount_cents"])))
            c.drawString(150 * mm, y, str(p["status"]))
            y -= 5.5 * mm
        total = sum(int(p["amount_cents"] or 0) for p in rows if p["status"] == "CONFIRMADO")
        c.setFont("Helvetica-Bold", 10)
        c.drawString(18 * mm, 18 * mm, f"Confirmado no dia: {money_br(total)}")

    return _pdf_response(f"pagamentos-{day}.pdf", draw)


@app.route("/admin/pdf/pagamento/<int:payment_id>")
def pdf_payment_receipt(payment_id: int):
    r = require_admin()
    if r:
        return r
    init_db()
    db = get_db()
    p = db.execute("SELECT * FROM payments WHERE id=?", (payment_id,)).fetchone()
    if not p:
        abort(404)
    if str(p["status"] or "") != "CONFIRMADO":
        abort(400)
    tab = db.execute("SELECT * FROM closed_tabs WHERE id=?", (p["closed_tab_id"],)).fetchone() if p["closed_tab_id"] else None

    def draw(c):
        c.setPageSize((80 * mm, 210 * mm))
        w, h = c._pagesize
        logo_h = draw_logo(c, center_x=w/2, top_y=h - 6 * mm, max_w_mm=26, max_h_mm=14)
        title_y = h - (8 * mm + logo_h)
        c.setFont("Helvetica-Bold", 12)
        c.setFillColor(colors.HexColor("#111827"))
        c.drawCentredString(w / 2, title_y, get_setting("restaurant_name"))
        c.setFont("Helvetica", 8)
        c.setFillColor(colors.HexColor("#6B7280"))
        c.drawCentredString(w / 2, title_y - 5 * mm, f"{APP_SYSTEM_BRAND} • Comprovante")
        y = title_y - 14 * mm
        lines = [
            f"Ref.: {p['ref_code'] or '#'+str(p['id'])}",
            f"Mesa: {p['table_no']}",
            f"Comanda: {tab['receipt_code']}" if tab else "Comanda: —",
            f"Pedido: #{p['order_id']}" if p['order_id'] else "Pedido: fechamento de mesa",
            f"Criado em: {iso_to_br(p['created_at'], with_seconds=True)}",
            f"Método: {p['method']}",
            f"Valor: {money_br(int(p['amount_cents']))}",
            f"Status: {p['status']}",
            f"Confirmado: {iso_to_br(p['admin_marked_at'], with_seconds=True)}" if p['admin_marked_at'] else "Confirmado: —",
            f"TXID: {p['pix_txid']}" if p['pix_txid'] else "TXID: —",
        ]
        c.setFillColor(colors.HexColor("#111827"))
        c.setFont("Helvetica", 8.5)
        for line in lines:
            c.drawString(6 * mm, y, line[:44])
            y -= 5.5 * mm
        if p['admin_note']:
            y -= 2 * mm
            c.setFont("Helvetica-Bold", 8.5)
            c.drawString(6 * mm, y, "Observação")
            y -= 5 * mm
            c.setFont("Helvetica", 8)
            for chunk in [str(p['admin_note'])[i:i+42] for i in range(0, len(str(p['admin_note'])), 42)]:
                c.drawString(6 * mm, y, chunk)
                y -= 4.5 * mm
        c.setFont("Helvetica", 7.5)
        c.setFillColor(colors.HexColor("#6B7280"))
        c.drawString(6 * mm, 12 * mm, f"Gerado em {now_br_str()}")

    return _pdf_response(f"comprovante-{payment_id}.pdf", draw)


# ------------------------
# API: settings (admin)
# ------------------------



# ------------------------
# Compatibility / exports
# ------------------------

def _csv_response(filename: str, headers: list[str], rows: list[list[str]]):
    import csv
    sio = io.StringIO()
    w = csv.writer(sio, delimiter=';')
    w.writerow(headers)
    for row in rows:
        w.writerow(row)
    resp = make_response(sio.getvalue())
    resp.headers["Content-Type"] = "text/csv; charset=utf-8"
    resp.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    return resp

@app.route("/api/admin/requests")
def api_admin_requests_alias():
    return api_admin_requests()

@app.route("/api/admin/requests/<int:req_id>/resolve", methods=["POST"])
def api_admin_requests_resolve_alias(req_id):
    return api_admin_resolve_request(req_id)

@app.route("/api/admin/comandas")
def api_admin_comandas_alias():
    r = require_admin()
    if r:
        return jsonify({"error": "unauthorized"}), 401
    init_db()
    db = get_db()
    rows = db.execute("SELECT * FROM orders WHERE is_closed=0 ORDER BY created_at ASC").fetchall()
    grouped = {}
    for row in rows:
        key = str(row["table_no"])
        grouped.setdefault(key, {"table_no": key, "orders": 0, "total_cents": 0, "first_at": row["created_at"], "last_at": row["updated_at"] or row["created_at"]})
        grouped[key]["orders"] += 1
        grouped[key]["total_cents"] += int(row["total_cents"] or 0)
        grouped[key]["first_at"] = min(grouped[key]["first_at"], row["created_at"])
        grouped[key]["last_at"] = max(grouped[key]["last_at"], row["updated_at"] or row["created_at"])
    tables = sorted(grouped.values(), key=lambda x: str(x["table_no"]))
    return jsonify({"tables": tables})

@app.route("/admin/csv/pedidos")
def admin_csv_orders():
    r = require_admin()
    if r:
        return r
    init_db()
    db = get_db()
    rows = db.execute("SELECT id, created_at, updated_at, table_no, customer_name, status, eta_minutes, total_cents, note FROM orders WHERE is_closed=0 ORDER BY created_at ASC").fetchall()
    csv_rows = []
    for row in rows:
        csv_rows.append([row["id"], iso_to_br(row["created_at"], True), iso_to_br(row["updated_at"], True), row["table_no"], row["customer_name"] or "", row["status"], row["eta_minutes"] or "", money_br(int(row["total_cents"] or 0)), row["note"] or ""])
    return _csv_response("menuflow-pedidos.csv", ["Pedido", "Criado em", "Atualizado em", "Mesa", "Cliente", "Status", "ETA min", "Total", "Obs"], csv_rows)

@app.route("/admin/csv/pagamentos")
def admin_csv_payments():
    r = require_admin()
    if r:
        return r
    init_db()
    date_str = (request.args.get("date") or "").strip()
    db = get_db()
    if date_str:
        rows = db.execute("SELECT * FROM payments WHERE substr(created_at,1,10)=? ORDER BY created_at DESC", (date_str,)).fetchall()
    else:
        rows = db.execute("SELECT * FROM payments ORDER BY created_at DESC LIMIT 1000").fetchall()
    csv_rows = []
    for row in rows:
        csv_rows.append([row["ref_code"] or payment_ref_for_row(int(row["id"]), row["created_at"]), iso_to_br(row["created_at"], True), row["table_no"], row["order_id"] or "", row["method"], money_br(int(row["amount_cents"] or 0)), row["status"], row["pix_txid"] or "", row["customer_name"] or "", row["customer_phone"] or "", iso_to_br(row["customer_marked_at"], True), iso_to_br(row["admin_marked_at"], True), row["admin_note"] or ""])
    return _csv_response("menuflow-pagamentos.csv", ["Referencia", "Criado em", "Mesa", "Pedido", "Metodo", "Valor", "Status", "TXID", "Cliente", "Telefone", "Informado pelo cliente", "Confirmado no admin", "Obs"], csv_rows)

@app.route("/admin/csv/historico")
def admin_csv_history():
    r = require_admin()
    if r:
        return r
    init_db()
    date_str = (request.args.get("date") or "").strip()
    db = get_db()
    if date_str:
        rows = db.execute("SELECT * FROM closed_tabs WHERE substr(closed_at,1,10)=? ORDER BY closed_at DESC", (date_str,)).fetchall()
    else:
        rows = db.execute("SELECT * FROM closed_tabs ORDER BY closed_at DESC LIMIT 1000").fetchall()
    csv_rows = []
    for row in rows:
        csv_rows.append([row["receipt_code"] or receipt_code_for_row(int(row["id"]), row["closed_at"]), iso_to_br(row["created_at"], True), iso_to_br(row["closed_at"], True), row["table_no"], money_br(int(row["total_cents"] or 0)), money_br(int(row["paid_cents"] or 0)), row["pay_status"]])
    return _csv_response("menuflow-historico.csv", ["Recibo", "Aberta em", "Fechada em", "Mesa", "Total", "Pago", "Status pagamento"], csv_rows)

@app.route("/api/admin/settings/logo", methods=["POST"])
def api_admin_settings_logo():
    r = require_admin()
    if r:
        return jsonify({"error": "unauthorized"}), 401
    init_db()
    if "logo" not in request.files:
        return jsonify({"error": "Envie um arquivo de imagem."}), 400
    f = request.files["logo"]
    raw = f.read()
    if not raw:
        return jsonify({"error": "Arquivo vazio."}), 400
    if len(raw) > 1024 * 1024 * 2:
        return jsonify({"error": "Logo muito grande. Use até 2 MB."}), 400
    mimetype = (f.mimetype or "").lower()
    if mimetype not in {"image/png", "image/jpeg", "image/jpg", "image/webp"}:
        return jsonify({"error": "Use PNG, JPG ou WEBP."}), 400
    encoded = base64.b64encode(raw).decode("ascii")
    data_uri = f"data:{mimetype};base64,{encoded}"
    set_setting("logo_data_uri", data_uri)
    set_setting("logo_file_name", (f.filename or "logo").strip())
    add_event("settings_updated", "logo")
    return jsonify({"ok": True, "logo_data_uri": data_uri, "logo_file_name": get_setting("logo_file_name")})


@app.route("/api/admin/settings/logo/delete", methods=["POST"])
def api_admin_settings_logo_delete():
    r = require_admin()
    if r:
        return jsonify({"error": "unauthorized"}), 401
    init_db()
    set_setting("logo_data_uri", "")
    set_setting("logo_file_name", "")
    add_event("settings_updated", "logo_delete")
    return jsonify({"ok": True})


@app.route("/api/admin/settings", methods=["GET", "POST"])
def api_admin_settings():
    r = require_admin()
    if r:
        return jsonify({"error": "unauthorized"}), 401
    init_db()
    if request.method == "GET":
        return jsonify({"settings": get_settings_dict()})

    data = request.get_json(force=True, silent=True) or {}
    for k in DEFAULT_SETTINGS.keys():
        if k in data:
            set_setting(k, str(data.get(k) or "").strip())
    add_event("settings_updated", "")
    return jsonify({"ok": True})


@app.route("/api/admin/asaas/webhook/register", methods=["POST"])
def api_admin_asaas_register_webhook():
    r = require_admin()
    if r:
        return jsonify({"error": "unauthorized"}), 401
    init_db()
    if not asaas_is_enabled():
        return jsonify({"error": "Ative o Asaas e preencha a API Key antes de cadastrar o webhook."}), 400

    webhook_url = normalize_asaas_webhook_url((get_setting("asaas_webhook_url", "") or "").strip(), request.url_root)
    if not webhook_url.startswith("http"):
        return jsonify({"error": "Informe uma URL pública válida para o webhook."}), 400

    payload = {
        "name": f"MenuFlow {get_setting('restaurant_name', 'Restaurante')}",
        "url": webhook_url,
        "enabled": True,
        "interrupted": False,
        "sendType": "SEQUENTIALLY",
        "events": [
            "PAYMENT_CREATED",
            "PAYMENT_UPDATED",
            "PAYMENT_OVERDUE",
            "PAYMENT_CONFIRMED",
            "PAYMENT_RECEIVED",
            "PAYMENT_REFUNDED",
            "PAYMENT_DELETED",
            "PAYMENT_RECEIVED_IN_CASH_UNDONE",
        ],
    }
    webhook_email = (get_setting("asaas_webhook_email", "") or "").strip()
    if webhook_email:
        payload["email"] = webhook_email
    auth_token = (get_setting("asaas_webhook_token", "") or "").strip()
    if auth_token:
        payload["authToken"] = auth_token

    try:
        _, data = asaas_request("POST", "/v3/webhooks", payload=payload)
        token = (data.get("authToken") or auth_token or "").strip()
        set_setting("asaas_webhook_url", webhook_url)
        if token:
            set_setting("asaas_webhook_token", token)
        add_event("settings_updated", "asaas_webhook")
        return jsonify({"ok": True, "webhook_url": webhook_url, "auth_token": token})
    except ExternalAPIError as exc:
        return jsonify({"error": str(exc)}), 400


# ------------------------
# API: events
# ------------------------

@app.route("/api/events")
def api_events():
    r = require_admin()
    if r:
        return jsonify({"error": "unauthorized"}), 401
    init_db()
    if (request.args.get("latest") or "").strip() == "1":
        db = get_db()
        row = db.execute("SELECT COALESCE(MAX(id), 0) AS max_id FROM events").fetchone()
        return jsonify({"events": [], "last_id": int(row["max_id"] or 0)})

    since = int(request.args.get("since", "0") or 0)
    db = get_db()
    rows = db.execute("SELECT * FROM events WHERE id > ? ORDER BY id ASC LIMIT 50", (since,)).fetchall()
    return jsonify({"events": [dict(x) for x in rows], "last_id": (rows[-1]["id"] if rows else since)})


# ------------------------
# API: orders
# ------------------------

@app.route("/api/orders", methods=["POST"])
def api_create_order():
    init_db()
    data = request.get_json(force=True, silent=True) or {}
    table_no = (data.get("table_no") or "").strip()
    if not table_no:
        return jsonify({"error": "Informe a mesa."}), 400

    items = data.get("items") or []
    if not isinstance(items, list) or not items:
        return jsonify({"error": "Seu pedido está vazio."}), 400

    customer_name = (data.get("customer_name") or "").strip()[:50]
    note = (data.get("note") or "").strip()[:300]

    menu_map = {}
    for section in MENU:
        for it in section["items"]:
            menu_map[it["id"]] = it

    total = 0
    normalized = []
    for it in items:
        item_id = (it.get("id") or "").strip()
        qty = int(it.get("qty") or 0)
        if item_id not in menu_map or qty <= 0 or qty > 30:
            continue
        mi = menu_map[item_id]
        price = int(mi["price_cents"])
        total += price * qty
        normalized.append({"id": item_id, "name": mi["name"], "qty": qty, "price_cents": price})

    if not normalized:
        return jsonify({"error": "Itens inválidos."}), 400

    now = utcnow_iso()
    db = get_db()
    cur = db.execute(
        """
        INSERT INTO orders (created_at, updated_at, table_no, customer_name, status, eta_minutes, admin_message, note, total_cents, is_closed)
        VALUES (?, ?, ?, ?, 'Novo', NULL, NULL, ?, ?, 0)
        """,
        (now, now, table_no, customer_name or None, note or None, total),
    )
    order_id = cur.lastrowid

    for it in normalized:
        db.execute(
            "INSERT INTO order_items (order_id, item_id, name, qty, price_cents) VALUES (?, ?, ?, ?, ?)",
            (order_id, it["id"], it["name"], it["qty"], it["price_cents"]),
        )
    db.commit()

    add_event("new_order", str(order_id))
    return jsonify({"ok": True, "order_id": order_id})


@app.route("/api/orders/<int:order_id>")
def api_get_order(order_id):
    init_db()
    db = get_db()
    o = db.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
    if not o:
        return jsonify({"error": "Pedido não encontrado."}), 404
    items = db.execute("SELECT name, qty, price_cents FROM order_items WHERE order_id=?", (order_id,)).fetchall()

    pays = db.execute(
        "SELECT COALESCE(SUM(amount_cents),0) AS cents FROM payments WHERE order_id=? AND status='CONFIRMADO'",
        (order_id,),
    ).fetchone()
    paid_cents = int(pays["cents"] or 0)

    return jsonify(
        {
            "order": dict(o),
            "items": [dict(x) for x in items],
            "money_total": money_br(int(o["total_cents"])),
            "paid_cents": paid_cents,
            "money_paid": money_br(paid_cents),
        }
    )


@app.route("/api/tables/<table_no>/summary")
def api_table_summary(table_no):
    init_db()
    db = get_db()
    summary = table_open_snapshot(db, str(table_no).strip())
    if not summary:
        return jsonify({"error": "Mesa sem comanda aberta."}), 404
    return jsonify({"summary": summary})


@app.route("/api/admin/tables/open")
def api_admin_tables_open():
    r = require_admin()
    if r:
        return jsonify({"error": "unauthorized"}), 401
    init_db()
    q = (request.args.get("q") or "").strip().lower()
    sort = (request.args.get("sort") or "oldest").strip().lower()
    db = get_db()
    rows = db.execute("SELECT DISTINCT table_no FROM orders WHERE is_closed=0 ORDER BY table_no ASC").fetchall()
    tables = []
    for row in rows:
        snap = table_open_snapshot(db, str(row["table_no"]))
        if not snap:
            continue
        hit = not q or q in str(snap["table_no"]).lower() or any(q in str(o["id"]).lower() for o in snap["orders"])
        if hit:
            tables.append(snap)
    if sort == "highest":
        tables.sort(key=lambda x: int(x.get("total_cents") or 0), reverse=True)
    elif sort == "newest":
        tables.sort(key=lambda x: x.get("oldest_at") or "", reverse=True)
    elif sort == "due":
        tables.sort(key=lambda x: int(x.get("due_cents") or 0), reverse=True)
    else:
        tables.sort(key=lambda x: x.get("oldest_at") or "")
    return jsonify({"tables": tables})


@app.route("/api/admin/orders")
def api_admin_orders():
    r = require_admin()
    if r:
        return jsonify({"error": "unauthorized"}), 401
    init_db()
    status = request.args.get("status")
    q = request.args.get("q", "").strip().lower()
    sort = (request.args.get("sort") or "oldest").strip().lower()
    db = get_db()

    where = "WHERE is_closed=0"
    params: list = []
    if status and status != "Todos":
        where += " AND status=?"
        params.append(status)
    if q:
        where += " AND (lower(table_no) LIKE ? OR CAST(id AS TEXT) LIKE ? OR lower(coalesce(customer_name,'')) LIKE ?)"
        params += [f"%{q}%", f"%{q}%", f"%{q}%"]

    rows = db.execute(
        f"SELECT * FROM orders {where} ORDER BY created_at ASC LIMIT 100",
        tuple(params),
    ).fetchall()

    order_ids = [r["id"] for r in rows]
    items_by = {oid: [] for oid in order_ids}
    if order_ids:
        ph = ",".join("?" for _ in order_ids)
        it_rows = db.execute(
            f"SELECT order_id, name, qty, price_cents FROM order_items WHERE order_id IN ({ph})",
            tuple(order_ids),
        ).fetchall()
        for it in it_rows:
            items_by[it["order_id"]].append(dict(it))

    out = []
    for o in rows:
        d = dict(o)
        d["money_total"] = money_br(int(d["total_cents"]))
        d["items"] = items_by.get(d["id"], [])
        d["wait_minutes"] = wait_minutes_from(d.get("created_at"))
        d["created_br"] = iso_to_br(d.get("created_at"))
        out.append(d)

    if sort == "newest":
        out.sort(key=lambda x: x.get("created_at") or "", reverse=True)
    elif sort == "table":
        out.sort(key=lambda x: str(x.get("table_no") or ""))
    elif sort == "highest":
        out.sort(key=lambda x: int(x.get("total_cents") or 0), reverse=True)
    else:
        out.sort(key=lambda x: x.get("created_at") or "")

    return jsonify({"orders": out})


@app.route("/api/admin/orders/<int:order_id>/update", methods=["POST"])
def api_admin_update_order(order_id):
    r = require_admin()
    if r:
        return jsonify({"error": "unauthorized"}), 401
    init_db()
    data = request.get_json(force=True, silent=True) or {}

    status = (data.get("status") or "").strip() or "Novo"
    eta = data.get("eta_minutes")
    msg = (data.get("admin_message") or "").strip()[:200]

    eta_val = None
    if eta not in (None, "", "null"):
        try:
            eta_val = int(eta)
            if eta_val < 0 or eta_val > 240:
                eta_val = None
        except Exception:
            eta_val = None

    db = get_db()
    o = db.execute("SELECT id FROM orders WHERE id=?", (order_id,)).fetchone()
    if not o:
        return jsonify({"error": "Pedido não encontrado."}), 404

    now = utcnow_iso()
    db.execute(
        "UPDATE orders SET status=?, eta_minutes=?, admin_message=?, updated_at=? WHERE id=?",
        (status, eta_val, msg or None, now, order_id),
    )
    db.commit()
    add_event("order_updated", str(order_id))
    return jsonify({"ok": True})


@app.route("/api/admin/orders/<int:order_id>/close", methods=["POST"])
def api_admin_close_order(order_id):
    r = require_admin()
    if r:
        return jsonify({"error": "unauthorized"}), 401
    init_db()
    db = get_db()
    now = utcnow_iso()
    db.execute("UPDATE orders SET is_closed=1, updated_at=? WHERE id=?", (now, order_id))
    db.commit()
    add_event("order_closed", str(order_id))
    return jsonify({"ok": True})


# ------------------------
# API: table requests
# ------------------------

@app.route("/api/table_requests", methods=["POST"])
def api_create_request():
    init_db()
    data = request.get_json(force=True, silent=True) or {}
    table_no = (data.get("table_no") or "").strip()
    req_type = (data.get("req_type") or "").strip()
    note = (data.get("note") or "").strip()[:200]
    if not table_no or not req_type:
        return jsonify({"error": "Dados incompletos."}), 400

    now = utcnow_iso()
    db = get_db()
    cur = db.execute(
        "INSERT INTO table_requests (created_at, updated_at, table_no, req_type, note, status) VALUES (?, ?, ?, ?, ?, 'Novo')",
        (now, now, table_no, req_type, note or None),
    )
    db.commit()
    add_event("table_request", str(cur.lastrowid))
    return jsonify({"ok": True})


@app.route("/api/admin/table_requests")
def api_admin_requests():
    r = require_admin()
    if r:
        return jsonify({"error": "unauthorized"}), 401
    init_db()
    db = get_db()
    rows = db.execute("SELECT * FROM table_requests WHERE status!='Resolvido' ORDER BY created_at DESC LIMIT 100").fetchall()
    return jsonify({"requests": [dict(x) for x in rows]})


@app.route("/api/admin/table_requests/<int:req_id>/resolve", methods=["POST"])
def api_admin_resolve_request(req_id):
    r = require_admin()
    if r:
        return jsonify({"error": "unauthorized"}), 401
    init_db()
    db = get_db()
    req = db.execute("SELECT * FROM table_requests WHERE id=?", (req_id,)).fetchone()
    if not req:
        return jsonify({"error": "notfound"}), 404
    now = utcnow_iso()
    db.execute("UPDATE table_requests SET status='Resolvido', updated_at=? WHERE id=?", (now, req_id))
    db.commit()
    add_event("table_request_resolved", str(req_id))
    req_type = str(req["req_type"] or "")
    lowered = req_type.lower()
    redirect_url = None
    if "conta" in lowered or "fechar" in lowered:
        redirect_url = url_for("admin_comandas", table=str(req["table_no"]))
    return jsonify({"ok": True, "request": dict(req), "redirect_url": redirect_url})


# ------------------------
# API: reservations
# ------------------------

@app.route("/api/reservations", methods=["POST"])
def api_create_reservation():
    init_db()
    data = request.get_json(force=True, silent=True) or {}
    name = (data.get("name") or "").strip()[:80]
    phone = (data.get("phone") or "").strip()[:30]
    cpf = (data.get("cpf") or "").strip()[:30] or None
    party_size = int(data.get("party_size") or 0)
    starts_at = (data.get("starts_at") or "").strip()
    notes = (data.get("notes") or "").strip()[:300] or None

    if not name or not phone or party_size <= 0:
        return jsonify({"error": "Preencha nome, telefone e quantidade de pessoas."}), 400
    try:
        parse_local_dt(starts_at)
    except Exception:
        return jsonify({"error": "Data/hora inválida."}), 400

    code = gen_code()
    now = utcnow_iso()
    db = get_db()
    db.execute(
        """
        INSERT INTO reservations (created_at, updated_at, code, name, phone, cpf, party_size, starts_at, duration_minutes, notes, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 90, ?, 'Pendente')
        """,
        (now, now, code, name, phone, cpf, party_size, starts_at, notes),
    )
    db.commit()
    add_event("reservation_created", code)
    return jsonify({"ok": True, "code": code})


@app.route("/api/reservations/lookup")
def api_lookup_reservation():
    init_db()
    phone = (request.args.get("phone") or "").strip()
    cpf = (request.args.get("cpf") or "").strip()
    code = (request.args.get("code") or "").strip()

    db = get_db()
    where = []
    params = []
    if code:
        where.append("code=?")
        params.append(code)
    if phone:
        where.append("phone=?")
        params.append(phone)
    if cpf:
        where.append("cpf=?")
        params.append(cpf)

    if not where:
        return jsonify({"error": "Informe pelo menos um dado."}), 400

    rows = db.execute(
        f"SELECT * FROM reservations WHERE {' AND '.join(where)} ORDER BY created_at DESC LIMIT 20",
        tuple(params),
    ).fetchall()
    return jsonify({"reservations": [dict(x) for x in rows]})


@app.route("/api/reservations/<code>/cancel", methods=["POST"])
def api_cancel_reservation(code):
    init_db()
    data = request.get_json(force=True, silent=True) or {}
    phone = (data.get("phone") or "").strip()
    cpf = (data.get("cpf") or "").strip()

    db = get_db()
    r = db.execute("SELECT * FROM reservations WHERE code=?", (code,)).fetchone()
    if not r:
        return jsonify({"error": "Reserva não encontrada."}), 404

    if not ((phone and phone == r["phone"]) or (cpf and (r["cpf"] or "") == cpf)):
        return jsonify({"error": "Não foi possível validar seus dados."}), 403

    now = utcnow_iso()
    db.execute("UPDATE reservations SET status='Cancelada', updated_at=? WHERE code=?", (now, code))
    db.commit()
    add_event("reservation_cancelled", code)
    return jsonify({"ok": True})


@app.route("/api/admin/reservations")
def api_admin_reservations():
    r = require_admin()
    if r:
        return jsonify({"error": "unauthorized"}), 401
    init_db()
    start = request.args.get("start")
    end = request.args.get("end")
    db = get_db()

    q = "SELECT * FROM reservations"
    params = []
    if start and end:
        q += " WHERE substr(starts_at,1,10) >= ? AND substr(starts_at,1,10) <= ?"
        params = [start, end]
    q += " ORDER BY starts_at ASC"
    rows = db.execute(q, tuple(params)).fetchall()
    return jsonify({"reservations": [dict(rr) for rr in rows]})


@app.route("/api/admin/reservations/<code>/set_status", methods=["POST"])
def api_admin_res_set_status(code):
    r = require_admin()
    if r:
        return jsonify({"error": "unauthorized"}), 401
    init_db()
    data = request.get_json(force=True, silent=True) or {}
    status = (data.get("status") or "").strip()
    if status not in ("Pendente", "Confirmada", "Cancelada"):
        return jsonify({"error": "Status inválido"}), 400

    db = get_db()
    now = utcnow_iso()
    db.execute("UPDATE reservations SET status=?, updated_at=? WHERE code=?", (status, now, code))
    db.commit()
    add_event("reservation_updated", code)
    return jsonify({"ok": True})


# ------------------------
# API: payments (Pix manual + Asaas)
# ------------------------

@app.route("/api/pix/create", methods=["POST"])
def api_pix_create():
    init_db()
    data = request.get_json(force=True, silent=True) or {}
    table_no = (data.get("table_no") or "").strip()
    order_id = data.get("order_id")
    amount_cents = int(data.get("amount_cents") or 0)
    customer_name = (data.get("customer_name") or "").strip()[:60]
    customer_phone = (data.get("customer_phone") or "").strip()[:30]
    customer_tax_id = normalize_cpf_cnpj(data.get("customer_tax_id") or "")

    if not table_no or amount_cents <= 0:
        return jsonify({"error": "Dados inválidos."}), 400

    if asaas_is_enabled():
        if not cpf_cnpj_is_valid_shape(customer_tax_id):
            return jsonify({"error": "Para gerar o Pix automático, informe um CPF ou CNPJ válido do pagador.", "code": "CPF_CNPJ_REQUIRED"}), 400
        try:
            result = create_asaas_pix_charge(table_no, order_id, amount_cents, customer_name, customer_phone, customer_tax_id)
            return jsonify(result)
        except ExternalAPIError as exc:
            return jsonify({"error": str(exc)}), 400

    pix_key = get_setting("pix_key", "")
    if not valid_pix_key(pix_key):
        return jsonify({"error": "Pix não configurado no restaurante. Configure uma chave Pix manual ou ative o Asaas no admin."}), 400

    amount_reais = f"{amount_cents/100:.2f}"
    prefix = (get_setting("pix_txid_prefix", "MF") or "MF").strip()[:8]
    suffix = secrets.token_hex(2).upper()
    txid = f"{prefix}{int(order_id) if str(order_id).isdigit() else ''}{suffix}"[:25]

    payload = build_pix_payload(
        pix_key=pix_key,
        merchant_name=get_setting("pix_merchant_name", "PARADevs"),
        merchant_city=get_setting("pix_merchant_city", "RIBEIRAO PRETO"),
        amount_reais=amount_reais,
        txid=txid,
    )

    qr_uri = qr_png_data_uri(payload)

    now = utcnow_iso()
    db = get_db()
    cur = db.execute(
        """
        INSERT INTO payments (created_at, updated_at, table_no, order_id, method, amount_cents, status, pix_txid, pix_payload, customer_name, customer_phone, customer_tax_id, provider, provider_status)
        VALUES (?, ?, ?, ?, 'PIX_QR', ?, 'PENDENTE', ?, ?, ?, ?, ?, 'LOCAL_PIX', 'PENDING')
        """,
        (now, now, table_no, int(order_id) if str(order_id).isdigit() else None, amount_cents, txid, payload, customer_name or None, customer_phone or None, customer_tax_id or None),
    )
    payment_id = cur.lastrowid
    db.execute("UPDATE payments SET ref_code=? WHERE id=?", (payment_ref_for_row(payment_id, now), payment_id))
    db.commit()

    add_event("pix_created", str(payment_id))

    return jsonify({
        "ok": True,
        "payment_id": payment_id,
        "txid": txid,
        "payload": payload,
        "qr": qr_uri,
        "amount": money_br(amount_cents),
        "ref_code": payment_ref_for_row(payment_id, now),
        "provider": "LOCAL_PIX",
        "auto_confirm": False,
    })


@app.route("/api/pix/mark_paid", methods=["POST"])
def api_pix_mark_paid():
    init_db()
    data = request.get_json(force=True, silent=True) or {}
    payment_id = int(data.get("payment_id") or 0)
    if payment_id <= 0:
        return jsonify({"error": "ID inválido"}), 400

    db = get_db()
    p = db.execute("SELECT * FROM payments WHERE id=?", (payment_id,)).fetchone()
    if not p:
        return jsonify({"error": "Pagamento não encontrado"}), 404

    if p["provider"] == "ASAAS":
        sync_asaas_payment_status(payment_id)
        p = db.execute("SELECT * FROM payments WHERE id=?", (payment_id,)).fetchone()
        if p and p["status"] == "CONFIRMADO":
            return jsonify({"ok": True, "auto_confirmed": True})

    now = utcnow_iso()
    db.execute(
        "UPDATE payments SET status='INFORMADO', customer_marked_at=?, updated_at=? WHERE id=? AND status IN ('PENDENTE','INFORMADO')",
        (now, now, payment_id),
    )
    db.commit()
    add_event("pix_reported", str(payment_id))
    return jsonify({"ok": True, "auto_confirmed": False})


@app.route("/api/pix/status/<int:payment_id>")
def api_pix_status(payment_id: int):
    init_db()
    db = get_db()
    p = db.execute("SELECT * FROM payments WHERE id=?", (payment_id,)).fetchone()
    if not p:
        return jsonify({"error": "notfound"}), 404
    if p["provider"] == "ASAAS":
        p = sync_asaas_payment_status(payment_id) or p
    slim = {
        "id": int(p["id"]),
        "status": p["status"],
        "admin_marked_at": p["admin_marked_at"],
        "customer_marked_at": p["customer_marked_at"],
        "provider": p["provider"],
        "provider_status": p["provider_status"],
        "auto_confirmed_at": p["auto_confirmed_at"],
        "pix_expires_at": p["pix_expires_at"],
    }
    return jsonify({"payment": slim})


@app.route("/webhooks/asaas", methods=["POST"])
def asaas_webhook():
    init_db()
    expected_token = (get_setting("asaas_webhook_token", "") or "").strip()
    received_token = (request.headers.get("asaas-access-token") or "").strip()
    if expected_token and expected_token != received_token:
        return jsonify({"error": "unauthorized"}), 401

    data = request.get_json(force=True, silent=True) or {}
    event_id = (data.get("id") or "").strip()
    event_type = (data.get("event") or "").strip().upper()
    payment_obj = data.get("payment") or {}

    db = get_db()
    if event_id and not log_provider_event(db, "ASAAS", event_id, event_type or "UNKNOWN", data):
        return jsonify({"ok": True, "duplicate": True})

    row = find_payment_by_provider_reference(db, payment_obj)
    if row:
        update_payment_from_provider(db, row, provider_payment=payment_obj, event_type=event_type, event_id=event_id)
        db.commit()
    return jsonify({"ok": True})


# ------------------------
# API: admin payments
# ------------------------

@app.route("/api/admin/payments")
def api_admin_payments():
    r = require_admin()
    if r:
        return jsonify({"error": "unauthorized"}), 401
    init_db()
    status = (request.args.get("status") or "").strip().upper()
    table = (request.args.get("table") or "").strip().lower()
    q = (request.args.get("q") or "").strip().lower()
    method = (request.args.get("method") or "TODOS").strip().upper()
    sort = (request.args.get("sort") or "newest").strip().lower()

    where = "WHERE 1=1"
    params: list = []
    if status and status != "TODOS":
        where += " AND status=?"
        params.append(status)
    if table:
        where += " AND lower(table_no) LIKE ?"
        params.append(f"%{table}%")
    if method and method != "TODOS":
        where += " AND method=?"
        params.append(method)
    if q:
        where += " AND (lower(coalesce(ref_code,'')) LIKE ? OR lower(coalesce(pix_txid,'')) LIKE ? OR CAST(id AS TEXT) LIKE ? OR lower(coalesce(customer_name,'')) LIKE ? OR lower(table_no) LIKE ? OR CAST(coalesce(closed_tab_id,'') AS TEXT) LIKE ?)"
        params += [f"%{q}%", f"%{q}%", f"%{q}%", f"%{q}%", f"%{q}%", f"%{q}%"]

    order_sql = "ORDER BY created_at DESC"
    if sort == "oldest":
        order_sql = "ORDER BY created_at ASC"
    elif sort == "highest":
        order_sql = "ORDER BY amount_cents DESC, created_at DESC"
    elif sort == "table":
        order_sql = "ORDER BY table_no ASC, created_at DESC"

    db = get_db()
    rows = db.execute(
        f"SELECT * FROM payments {where} {order_sql} LIMIT 300",
        tuple(params),
    ).fetchall()

    out = []
    for p in rows:
        d = dict(p)
        d["money_amount"] = money_br(int(d["amount_cents"]))
        d["created_br"] = iso_to_br(d.get("created_at"))
        d["confirmed_br"] = iso_to_br(d.get("admin_marked_at"))
        d["receipt_enabled"] = d.get("status") == "CONFIRMADO"
        d["receipt_code"] = None
        if d.get("closed_tab_id"):
            tab = db.execute("SELECT receipt_code FROM closed_tabs WHERE id=?", (int(d["closed_tab_id"]),)).fetchone()
            if tab:
                d["receipt_code"] = tab["receipt_code"]
        out.append(d)

    today = datetime.now().strftime("%Y-%m-%d")
    stats = db.execute(
        "SELECT status, COUNT(*) AS qty, COALESCE(SUM(amount_cents),0) AS cents FROM payments WHERE substr(created_at,1,10)=? GROUP BY status",
        (today,),
    ).fetchall()
    by_status = {row["status"]: {"qty": int(row["qty"] or 0), "cents": int(row["cents"] or 0)} for row in stats}

    return jsonify({
        "payments": out,
        "stats": {
            "today_confirmed": money_br(by_status.get("CONFIRMADO", {}).get("cents", 0)),
            "today_pending": by_status.get("PENDENTE", {}).get("qty", 0),
            "today_reported": by_status.get("INFORMADO", {}).get("qty", 0),
            "today_count": sum(v["qty"] for v in by_status.values()),
        }
    })


@app.route("/api/admin/payments/<int:payment_id>/confirm", methods=["POST"])
def api_admin_confirm_payment(payment_id: int):
    r = require_admin()
    if r:
        return jsonify({"error": "unauthorized"}), 401
    init_db()
    data = request.get_json(force=True, silent=True) or {}
    note = (data.get("note") or "").strip()[:120]
    method = (data.get("method") or "").strip().upper()
    allowed_methods = {"PIX_QR", "DINHEIRO", "CARTAO", "OUTRO", "CAIXA"}

    db = get_db()
    p = db.execute("SELECT * FROM payments WHERE id=?", (payment_id,)).fetchone()
    if not p:
        return jsonify({"error": "notfound"}), 404
    if method not in allowed_methods:
        method = str(p["method"] or "OUTRO")
    if method == "CAIXA":
        method = "DINHEIRO"

    now = utcnow_iso()
    db.execute(
        "UPDATE payments SET status='CONFIRMADO', method=?, admin_marked_at=?, admin_note=?, updated_at=? WHERE id=?",
        (method, now, note or None, now, payment_id),
    )
    linked_tab_id = link_payment_to_relevant_tab(db, payment_id)
    if linked_tab_id:
        sync_closed_tab(db, linked_tab_id)
    elif p["closed_tab_id"]:
        sync_closed_tab(db, int(p["closed_tab_id"]))
    db.commit()
    add_event("pix_confirmed", str(payment_id))
    return jsonify({"ok": True, "linked_tab_id": linked_tab_id, "receipt_url": url_for("pdf_payment_receipt", payment_id=payment_id)})


@app.route("/api/admin/payments/<int:payment_id>/reject", methods=["POST"])
def api_admin_reject_payment(payment_id: int):
    r = require_admin()
    if r:
        return jsonify({"error": "unauthorized"}), 401
    init_db()
    data = request.get_json(force=True, silent=True) or {}
    note = (data.get("note") or "").strip()[:120]

    db = get_db()
    p = db.execute("SELECT * FROM payments WHERE id=?", (payment_id,)).fetchone()
    if not p:
        return jsonify({"error": "notfound"}), 404

    now = utcnow_iso()
    db.execute(
        "UPDATE payments SET status='REJEITADO', admin_marked_at=?, admin_note=?, updated_at=? WHERE id=?",
        (now, note or None, now, payment_id),
    )
    linked_tab_id = p["closed_tab_id"] or link_payment_to_relevant_tab(db, payment_id)
    if linked_tab_id:
        sync_closed_tab(db, int(linked_tab_id))
    db.commit()
    add_event("pix_rejected", str(payment_id))
    return jsonify({"ok": True})


@app.route("/api/admin/payments/manual", methods=["POST"])
def api_admin_manual_payment():
    r = require_admin()
    if r:
        return jsonify({"error": "unauthorized"}), 401
    init_db()
    data = request.get_json(force=True, silent=True) or {}
    table_no = (data.get("table_no") or "").strip()
    method = (data.get("method") or "DINHEIRO").strip().upper()
    amount_cents = int(data.get("amount_cents") or 0)
    note = (data.get("note") or "").strip()[:120]
    if not table_no or amount_cents <= 0:
        return jsonify({"error": "Dados inválidos."}), 400
    if method not in ("DINHEIRO", "CARTAO", "OUTRO"):
        method = "OUTRO"

    now = utcnow_iso()
    db = get_db()
    cur = db.execute(
        """
        INSERT INTO payments (created_at, updated_at, table_no, order_id, method, amount_cents, status, admin_marked_at, admin_note)
        VALUES (?, ?, ?, NULL, ?, ?, 'CONFIRMADO', ?, ?)
        """,
        (now, now, table_no, method, amount_cents, now, note or None),
    )
    payment_id = cur.lastrowid
    db.execute("UPDATE payments SET ref_code=? WHERE id=?", (payment_ref_for_row(payment_id, now), payment_id))
    linked_tab_id = link_payment_to_relevant_tab(db, payment_id)
    if linked_tab_id:
        sync_closed_tab(db, linked_tab_id)
    db.commit()
    add_event("manual_payment", str(payment_id))
    return jsonify({"ok": True, "payment_id": payment_id, "ref_code": payment_ref_for_row(payment_id, now), "linked_tab_id": linked_tab_id})


# ------------------------
# API: history & reports
# ------------------------

@app.route("/api/admin/history")
def api_admin_history():
    r = require_admin()
    if r:
        return jsonify({"error": "unauthorized"}), 401
    init_db()
    day = (request.args.get("date") or "").strip()
    q = (request.args.get("q") or "").strip().lower()
    pay_status = (request.args.get("pay_status") or "TODOS").strip().upper()
    sort = (request.args.get("sort") or "newest").strip().lower()
    if day and not re.match(r"^\d{4}-\d{2}-\d{2}$", day):
        day = ""

    where = "WHERE 1=1"
    params = []
    if day:
        where += " AND substr(closed_at,1,10)=?"
        params.append(day)
    if pay_status and pay_status != "TODOS":
        where += " AND pay_status=?"
        params.append(pay_status)
    if q:
        where += " AND (lower(table_no) LIKE ? OR lower(coalesce(receipt_code,'')) LIKE ? OR CAST(id AS TEXT) LIKE ?)"
        params += [f"%{q}%", f"%{q}%", f"%{q}%"]

    order_sql = "ORDER BY closed_at DESC" if sort != "oldest" else "ORDER BY closed_at ASC"
    db = get_db()
    sync_all_closed_tabs(db)
    db.commit()
    rows = db.execute(f"SELECT * FROM closed_tabs {where} {order_sql} LIMIT 300", tuple(params)).fetchall()

    out = []
    for t in rows:
        d = dict(t)
        d["money_total"] = money_br(int(d["total_cents"]))
        d["money_paid"] = money_br(int(d["paid_cents"]))
        d["closed_br"] = iso_to_br(d.get("closed_at"))
        out.append(d)
    return jsonify({"tabs": out})


@app.route("/api/admin/reports/daily")
def api_admin_reports_daily():
    r = require_admin()
    if r:
        return jsonify({"error": "unauthorized"}), 401
    init_db()
    day = (request.args.get("date") or "").strip()
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", day):
        day = datetime.now().strftime("%Y-%m-%d")

    db = get_db()
    sync_all_closed_tabs(db)
    db.commit()
    tabs = db.execute("SELECT * FROM closed_tabs WHERE substr(closed_at,1,10)=?", (day,)).fetchall()
    total = sum(int(t["total_cents"]) for t in tabs)
    paid = sum(int(t["paid_cents"]) for t in tabs)

    pay_rows = db.execute(
        "SELECT method, COALESCE(SUM(amount_cents),0) AS cents FROM payments WHERE status='CONFIRMADO' AND substr(admin_marked_at,1,10)=? GROUP BY method",
        (day,),
    ).fetchall()
    breakdown = {r["method"]: int(r["cents"] or 0) for r in pay_rows}

    avg_ticket = int(total / len(tabs)) if tabs else 0
    return jsonify({
        "date": day,
        "total_cents": total,
        "paid_cents": paid,
        "money_total": money_br(total),
        "money_paid": money_br(paid),
        "breakdown": {k: money_br(v) for k, v in breakdown.items()},
        "tabs_count": len(tabs),
        "avg_ticket": money_br(avg_ticket),
        "pending_tabs": sum(1 for t in tabs if t["pay_status"] != "PAGO"),
    })


# ------------------------
# Admin: close table -> archive + compute paid
# ------------------------

@app.route("/api/admin/tables/<table_no>/close", methods=["POST"])
def api_admin_close_table(table_no):
    r = require_admin()
    if r:
        return jsonify({"error": "unauthorized"}), 401
    init_db()
    db = get_db()

    orders = db.execute(
        "SELECT * FROM orders WHERE table_no=? AND is_closed=0 ORDER BY created_at ASC",
        (table_no,),
    ).fetchall()
    if not orders:
        return jsonify({"error": "Sem pedidos abertos nesta mesa."}), 400

    items = db.execute(
        """
        SELECT name, SUM(qty) AS qty, price_cents
        FROM order_items oi
        JOIN orders o ON o.id=oi.order_id
        WHERE o.table_no=? AND o.is_closed=0
        GROUP BY name, price_cents
        ORDER BY name
        """,
        (table_no,),
    ).fetchall()

    total = sum(int(i["qty"]) * int(i["price_cents"]) for i in items)

    session_start = str(orders[0]["created_at"])  # earliest open order time
    session_payments = payments_for_table_since(db, table_no, session_start)
    paid = sum(int(p.get("amount_cents") or 0) for p in session_payments if p.get("status") == "CONFIRMADO")

    pay_status = "PENDENTE"
    if paid <= 0:
        pay_status = "PENDENTE"
    elif paid < total:
        pay_status = "PARCIAL"
    else:
        pay_status = "PAGO"

    now = utcnow_iso()
    items_json = json.dumps([{"name": i["name"], "qty": int(i["qty"]), "price_cents": int(i["price_cents"])} for i in items], ensure_ascii=False)
    orders_json = json.dumps([{"id": int(o["id"]), "created_at": o["created_at"], "total_cents": int(o["total_cents"])} for o in orders], ensure_ascii=False)
    payments_json = json.dumps(session_payments, ensure_ascii=False)

    cur = db.execute(
        "INSERT INTO closed_tabs (created_at, table_no, closed_at, total_cents, items_json, orders_json, paid_cents, pay_status, payments_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (now, table_no, now, total, items_json, orders_json, paid, pay_status, payments_json),
    )
    tab_id = cur.lastrowid
    receipt_code = receipt_code_for_row(tab_id, now)
    db.execute("UPDATE closed_tabs SET receipt_code=? WHERE id=?", (receipt_code, tab_id))
    db.execute(
        "UPDATE payments SET closed_tab_id=?, updated_at=? WHERE table_no=? AND created_at>=? AND closed_tab_id IS NULL",
        (tab_id, now, table_no, session_start),
    )
    sync_closed_tab(db, tab_id)
    auto_payment_id = ensure_settlement_payment_for_tab(db, tab_id)
    if auto_payment_id:
        sync_closed_tab(db, tab_id)

    db.execute("UPDATE orders SET is_closed=1, updated_at=? WHERE table_no=? AND is_closed=0", (now, table_no))
    db.commit()

    tab_row = db.execute("SELECT paid_cents, pay_status FROM closed_tabs WHERE id=?", (tab_id,)).fetchone()
    auto_ref = None
    if auto_payment_id:
        auto_ref = db.execute("SELECT ref_code FROM payments WHERE id=?", (auto_payment_id,)).fetchone()["ref_code"]
    add_event("table_closed", table_no)
    return jsonify({"ok": True, "table_no": table_no, "total": money_br(total), "paid": money_br(int(tab_row['paid_cents'] or 0)), "pay_status": tab_row['pay_status'], "receipt_code": receipt_code, "payment_ref": auto_ref, "auto_payment_id": auto_payment_id})


# ------------------------
# Static helpers for templates
# ------------------------

@app.context_processor
def inject_helpers():
    return {"money_br": money_br}


if __name__ == "__main__":
    with app.app_context():
        init_db()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")), debug=False)
