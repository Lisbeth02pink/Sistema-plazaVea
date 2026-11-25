from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from email.message import EmailMessage
from hashlib import scrypt, sha256
import csv
import io
import logging
from pathlib import Path
from secrets import token_urlsafe
from smtplib import SMTP, SMTP_SSL
from typing import Literal
import os
import re
import sqlite3
import ssl
import shutil
import subprocess
from sqlalchemy.exc import IntegrityError

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, model_validator
from fastapi.responses import FileResponse, StreamingResponse
import local_forecasting
import alerting
import replenishment
try:
    from .dashboard_data import dashboard_data
except ImportError:
    from dashboard_data import dashboard_data

ROOT = Path(__file__).parent
load_dotenv(ROOT / ".env")
DB = ROOT / "plaza_vea.db"
try:
    from .database import configured_url, connection, database_backend, initialize_database
    from .inventory_policy import inventory_status
except ImportError:
    from database import configured_url, connection, database_backend, initialize_database
    from inventory_policy import inventory_status
COOKIE = "plaza_session"
SESSION_HOURS = 8
RESET_MINUTES = 45
DEFAULT_FAILED_ATTEMPTS = 10
DEFAULT_LOCKOUT_MINUTES = 15
PERU_TIMEZONE = timezone(timedelta(hours=-5))
PASSWORD_SALT_BYTES = 16
PASSWORD_HASH_BYTES = 64
SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
MODULES = [
    ("dashboard", "Dashboard"),
    ("products", "Productos"),
    ("sales", "Ventas históricas"),
    ("inventory", "Inventarios"),
    ("forecast", "Predicción con IA"),
    ("alerts", "Alertas"),
    ("replenishment", "Reposición"),
    ("users", "Usuarios y roles"),
    ("audit", "Auditoría"),
    ("security", "Seguridad"),
]
SEED = [
    ("PV-1045", "Leche evaporada Gloria 400 g", "Lácteos", 4.20, 18),
    ("PV-2681", "Arroz Costeño superior 5 kg", "Abarrotes", 23.90, 8),
    ("PV-3390", "Aceite vegetal Primor 900 ml", "Abarrotes", 10.50, 126),
    ("PV-4212", "Yogurt Gloria fresa 1 kg", "Lácteos", 8.90, 14),
    ("PV-5504", "Detergente Bolívar 780 g", "Limpieza", 12.40, 220),
    ("PV-6607", "Pan molde integral", "Panadería", 7.50, 70),
]


def now() -> datetime:
    return datetime.now(timezone.utc)


def write_audit(actor_id: int | None, actor_email: str | None, actor_role: str | None, action: str, target_type: str | None = None, target_id: str | None = None, summary: str = ""):
    with connection() as db:
        db.execute("INSERT INTO audit_log(actor_id,actor_email,actor_role,action,target_type,target_id,summary,created_at) VALUES(?,?,?,?,?,?,?,?)", (actor_id, actor_email, actor_role, action, target_type, target_id, summary, now().isoformat()))


def refresh_alerts_after_change():
    try:
        alerting.refresh_alerts()
    except Exception:
        logger.exception("Could not refresh operational alerts after data change")


def make_password_hash(password: str) -> str:
    salt = os.urandom(PASSWORD_SALT_BYTES)
    digest = scrypt(password.encode("utf-8"), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=PASSWORD_HASH_BYTES)
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt_hex, digest_hex = stored.split("$")
        if scheme != "scrypt":
            return False
        digest = scrypt(password.encode("utf-8"), salt=bytes.fromhex(salt_hex), n=int(n), r=int(r), p=int(p), dklen=len(bytes.fromhex(digest_hex)))
        from hmac import compare_digest
        return compare_digest(digest.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


def row_product(row):
    result = dict(row)
    result["status"] = inventory_status(
        result["stock"], result.get("minimum_stock"), result.get("safety_stock"), result.get("maximum_stock"),
        bool(result.get("inventory_registered", 1))
    )
    result["policy_configured"] = result["status"] != "Política pendiente"
    return result


INBOUND_MOVEMENTS = {"Entrada por recepción", "Ajuste positivo", "Devolución"}


def apply_inventory_movement(db, product_id: int, movement_type: str, quantity: int, occurred_at: str, reason: str, reference: str, user_id: int | None):
    registered = db.execute("SELECT inventory_registered FROM products WHERE id=?", (product_id,)).fetchone()
    if not registered:
        raise HTTPException(404, "Producto no encontrado.")
    if not registered["inventory_registered"]:
        raise HTTPException(409, "Registra primero el conteo físico inicial de este producto.")
    if movement_type in INBOUND_MOVEMENTS:
        updated = db.execute("UPDATE products SET stock=stock+? WHERE id=? RETURNING stock", (quantity, product_id)).fetchone()
        if not updated:
            raise HTTPException(404, "Producto no encontrado.")
        stock_after = updated["stock"]
        stock_before = stock_after - quantity
    else:
        updated = db.execute("UPDATE products SET stock=stock-? WHERE id=? AND stock>=? RETURNING stock", (quantity, product_id, quantity)).fetchone()
        if not updated:
            current = db.execute("SELECT stock,name FROM products WHERE id=?", (product_id,)).fetchone()
            if not current:
                raise HTTPException(404, "Producto no encontrado.")
            raise HTTPException(409, f"Stock insuficiente para {current['name']}. Disponible: {current['stock']}.")
        stock_after = updated["stock"]
        stock_before = stock_after + quantity
    inserted = db.execute("INSERT INTO inventory_movements(product_id,movement_type,quantity,stock_before,stock_after,occurred_at,reason,reference,user_id) VALUES(?,?,?,?,?,?,?,?,?) RETURNING id", (product_id, movement_type, quantity, stock_before, stock_after, occurred_at, reason, reference, user_id)).fetchone()
    return {"id": inserted["id"], "product_id": product_id, "movement_type": movement_type, "quantity": quantity, "stock_before": stock_before, "stock_after": stock_after, "occurred_at": occurred_at, "reason": reason, "reference": reference, "user_id": user_id}


@asynccontextmanager
async def lifespan(_app):
    initialize_database()
    with connection() as db:
        defaults = [
            ("Administrador", "Administración de usuarios y acceso al sistema", 0),
            ("Responsable de abastecimiento", "Gestión de inventarios y reposición", 0),
            ("Personal de operaciones", "Operación de tienda", 0),
        ]
        deleted_role_names = {row[0] for row in db.execute("SELECT name FROM deleted_roles")}
        new_admin_role = db.execute("SELECT 1 FROM roles WHERE name='Administrador'").fetchone() is None and "Administrador" not in deleted_role_names
        db.executemany("INSERT OR IGNORE INTO roles(name,description,can_manage_users) VALUES(?,?,?)", [item for item in defaults if item[0] not in deleted_role_names])
        if new_admin_role:
            db.executemany("INSERT OR IGNORE INTO role_modules(role_name,module_key) VALUES('Administrador',?)", [(module_key,) for module_key, _ in MODULES])
        db.execute("UPDATE roles SET can_manage_users=0 WHERE name='Administrador'")
        if db.execute("SELECT COUNT(*) FROM users u JOIN roles r ON r.name=u.role WHERE u.is_active=1 AND (r.can_manage_users=1 OR EXISTS(SELECT 1 FROM role_modules rm WHERE rm.role_name=u.role AND rm.module_key='users'))").fetchone()[0] == 0 and db.execute("SELECT 1 FROM roles WHERE name='Administrador'").fetchone():
            db.execute("UPDATE users SET role='Administrador' WHERE id=(SELECT id FROM users WHERE is_active=1 ORDER BY id LIMIT 1)")
        db.execute("INSERT OR IGNORE INTO security_settings(id,session_hours,failed_attempts,lockout_minutes,updated_at) VALUES(1,8,10,15,?)", (now().isoformat(),))
        if db.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 0:
            seeded_at = now().astimezone(PERU_TIMEZONE).isoformat()
            for code, name, category, price, stock in SEED:
                created = db.execute("INSERT INTO products(code,name,category,price,stock) VALUES(?,?,?,?,?)", (code, name, category, price, stock))
                if stock > 0:
                    db.execute("INSERT INTO inventory_movements(product_id,movement_type,quantity,stock_before,stock_after,occurred_at,reason,reference,user_id) VALUES(?,?,?,?,?,?,?,?,NULL)", (created.lastrowid, "Ajuste positivo", stock, 0, stock, seeded_at, "Saldo inicial del producto", f"APERTURA-{code}"))
        db.execute("DELETE FROM sessions WHERE expires_at < ?", (now().isoformat(),))
        db.execute("DELETE FROM password_resets WHERE expires_at < ? OR used_at IS NOT NULL", (now().isoformat(),))
    yield


app = FastAPI(title="Plaza Vea BI API", version="1.0.0", lifespan=lifespan)
logger = logging.getLogger("plaza_vea.auth")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Content-Type"],
)


class LoginIn(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=1024)


class EmailIn(BaseModel):
    email: str = Field(min_length=3, max_length=254)


class PasswordResetIn(BaseModel):
    token: str = Field(min_length=20, max_length=256)
    password: str = Field(min_length=8, max_length=1024)


class AdminUserCreateIn(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    name: str = Field(min_length=1, max_length=180)
    password: str = Field(min_length=8, max_length=1024)
    role: str = Field(min_length=1, max_length=80)


class AdminUserUpdateIn(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    name: str = Field(min_length=1, max_length=180)
    role: str = Field(min_length=1, max_length=80)
    is_active: bool
    password: str | None = Field(default=None, min_length=8, max_length=1024)


class RoleModulesIn(BaseModel):
    modules: list[str] = Field(max_length=len(MODULES))


class SecuritySettingsIn(BaseModel):
    session_hours: int = Field(ge=1, le=24)
    failed_attempts: int = Field(ge=3, le=20)
    lockout_minutes: int = Field(ge=1, le=60)


class SecurityReviewIn(BaseModel):
    notes: str = Field(default="", max_length=500)


class AdminRoleCreateIn(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    description: str | None = Field(default=None, max_length=180)
    modules: list[str] = Field(default_factory=list, max_length=len(MODULES))


class AdminRoleUpdateIn(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    description: str | None = Field(default=None, max_length=180)


class ProductIn(BaseModel):
    code: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=180)
    category: str = Field(min_length=1, max_length=80)
    price: float = Field(ge=0)
    stock: int = Field(ge=0)
    unit_of_measure: str = Field(min_length=1, max_length=30)
    minimum_stock: int = Field(ge=0)
    safety_stock: int = Field(ge=0)
    maximum_stock: int | None = Field(default=None, ge=0)
    supplier: str = Field(min_length=1, max_length=180)
    lead_time_days: int = Field(ge=1, le=365)

    @model_validator(mode="after")
    def validate_inventory_policy(self):
        self.code = self.code.strip()
        self.name = self.name.strip()
        self.category = self.category.strip()
        self.unit_of_measure = self.unit_of_measure.strip()
        self.supplier = self.supplier.strip()
        if not self.code or not self.name or not self.category:
            raise ValueError("Ingresa el código, nombre y categoría del producto.")
        if not self.unit_of_measure:
            raise ValueError("Ingresa la unidad de medida.")
        if not self.supplier:
            raise ValueError("Ingresa el proveedor asociado.")
        if self.safety_stock > self.minimum_stock:
            raise ValueError("El stock de seguridad no puede superar el stock mínimo.")
        if self.maximum_stock is not None and self.maximum_stock <= self.minimum_stock:
            raise ValueError("El stock máximo debe ser mayor que el stock mínimo.")
        return self


class SaleItemIn(BaseModel):
    product_id: int = Field(gt=0)
    quantity: int = Field(gt=0, le=100000)
    unit_price: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)


class SaleIn(BaseModel):
    reference: str = Field(min_length=1, max_length=80)
    occurred_at: datetime | None = None
    items: list[SaleItemIn] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_sale(self):
        self.reference = self.reference.strip()
        if not self.reference:
            raise ValueError("Ingresa el número de comprobante o referencia.")
        product_ids = [item.product_id for item in self.items]
        if len(product_ids) != len(set(product_ids)):
            raise ValueError("Cada producto debe aparecer una sola vez en la venta.")
        return self


class InventoryMovementIn(BaseModel):
    product_id: int = Field(gt=0)
    movement_type: Literal["Entrada por recepción", "Ajuste positivo", "Devolución", "Salida por venta", "Ajuste negativo", "Merma"]
    quantity: int = Field(gt=0, le=1000000)
    reason: str = Field(min_length=1, max_length=240)
    reference: str = Field(min_length=1, max_length=80)
    occurred_at: datetime | None = None


class InitialStockIn(BaseModel):
    quantity: int = Field(ge=0, le=1000000)
    reference: str = Field(default="Conteo físico inicial", min_length=1, max_length=80)

    @model_validator(mode="after")
    def validate_movement(self):
        self.reason = self.reason.strip()
        self.reference = self.reference.strip()
        if not self.reason:
            raise ValueError("Ingresa el motivo del movimiento.")
        if not self.reference:
            raise ValueError("Ingresa la referencia del movimiento.")
        if self.movement_type == "Salida por venta":
            raise ValueError("Las salidas por venta se generan automáticamente al registrar una venta.")
        return self


class LocalForecastIn(BaseModel):
    product_id: int = Field(gt=0)
    horizon: Literal[7, 14, 30] = 7


class ReplenishmentRequestIn(BaseModel):
    product_id: int = Field(gt=0)
    note: str = Field(default="", max_length=500)


class ReplenishmentDecisionIn(BaseModel):
    decision: Literal["approve", "reject"]
    note: str = Field(default="", max_length=500)


class ReplenishmentReceiptIn(BaseModel):
    quantity: int = Field(gt=0, le=1000000)
    reference: str = Field(min_length=1, max_length=80)

    @model_validator(mode="after")
    def clean_reference(self):
        self.reference = self.reference.strip()
        if not self.reference:
            raise ValueError("Ingresa la referencia del comprobante de recepción.")
        return self


def user_payload(row):
    with connection() as db:
        role_data = db.execute("SELECT can_manage_users FROM roles WHERE name=?", (row["role"],)).fetchone()
        modules = [item[0] for item in db.execute("SELECT module_key FROM role_modules WHERE role_name=? ORDER BY module_key", (row["role"],))]
    is_administrator = bool(role_data and role_data["can_manage_users"])
    if is_administrator:
        modules = [key for key, _ in MODULES]
    return {"id": row["id"], "email": row["email"], "name": row["display_name"], "role": row["role"], "can_manage_users": is_administrator or "users" in modules, "modules": modules}


def get_current_user(request: Request):
    token = request.cookies.get(COOKIE)
    if not token:
        raise HTTPException(401, "La sesión venció o no está iniciada.")
    token_hash = sha256(token.encode("utf-8")).hexdigest()
    with connection() as db:
        row = db.execute(
            "SELECT u.id,u.email,u.display_name,u.role,CASE WHEN COALESCE(r.can_manage_users,0)=1 OR EXISTS(SELECT 1 FROM role_modules rm WHERE rm.role_name=u.role AND rm.module_key='users') THEN 1 ELSE 0 END AS can_manage_users FROM sessions s JOIN users u ON u.id=s.user_id LEFT JOIN roles r ON r.name=u.role WHERE s.token_hash=? AND s.expires_at>? AND u.is_active=1",
            (token_hash, now().isoformat()),
        ).fetchone()
    if not row:
        raise HTTPException(401, "La sesión venció o no está iniciada.")
    return user_payload(row)


def require_admin(user=Depends(get_current_user)):
    if not user.get("can_manage_users"):
        raise HTTPException(403, "No tienes permiso para administrar usuarios.")
    return user


def require_super_admin(user=Depends(get_current_user)):
    if user.get("role") != "Super Administrador":
        raise HTTPException(403, "Solo el Super Administrador puede administrar esta configuración.")
    return user


def require_module(module_key: str):
    def dependency(user=Depends(get_current_user)):
        if module_key not in user.get("modules", []):
            raise HTTPException(403, "Tu rol no tiene acceso a este módulo.")
        return user
    return dependency


def cookie_secure() -> bool:
    return os.getenv("COOKIE_SECURE", "false").lower() == "true"


def smtp_configured() -> bool:
    return all(os.getenv(key) for key in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM"))


def send_reset_email(email: str, token: str):
    port = int(os.getenv("SMTP_PORT", "587"))
    base = os.getenv("APP_BASE_URL", "http://127.0.0.1:5173").rstrip("/")
    link = f"{base}/reset-password#token={token}"
    message = EmailMessage()
    message["Subject"] = "Restablece tu contraseña | Plaza Vea BI"
    message["From"] = os.environ["SMTP_FROM"]
    message["To"] = email
    message.set_content(f"Solicitaste restablecer tu contraseña. Abre este enlace válido por {RESET_MINUTES} minutos:\n\n{link}\n\nSi no solicitaste el cambio, ignora este correo.")
    if port == 465:
        with SMTP_SSL(os.environ["SMTP_HOST"], port, context=ssl.create_default_context(), timeout=15) as smtp:
            smtp.login(os.environ["SMTP_USER"], os.environ["SMTP_PASSWORD"])
            smtp.send_message(message)
    else:
        with SMTP(os.environ["SMTP_HOST"], port, timeout=15) as smtp:
            smtp.starttls(context=ssl.create_default_context())
            smtp.login(os.environ["SMTP_USER"], os.environ["SMTP_PASSWORD"])
            smtp.send_message(message)


@app.get("/api/health")
def health():
    return {"status": "ok", "database": database_backend()}


@app.get("/api/simulation/status")
def simulation_status(_user=Depends(require_module("dashboard"))):
    with connection() as db:
        sales = db.execute(
            "SELECT COUNT(*) AS count,MIN(substr(occurred_at,1,10)) AS from_date,MAX(substr(occurred_at,1,10)) AS to_date "
            "FROM sales WHERE reference LIKE 'SIM-PLAZA-%'"
        ).fetchone()
        products_count = db.execute("SELECT COUNT(*) FROM products WHERE stock_simulated=1").fetchone()[0]
    return {"active": bool(sales["count"] or products_count), "products_count": int(products_count),
            "sales_count": int(sales["count"]), "from_date": sales["from_date"], "to_date": sales["to_date"]}


@app.post("/api/auth/login")
def login(payload: LoginIn, request: Request, response: Response):
    email = payload.email.strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise HTTPException(400, "Ingresa un correo electrónico válido.")
    remote = request.client.host if request.client else "unknown"
    fingerprint = sha256(f"{email}\0{remote}".encode("utf-8")).hexdigest()
    current_time = now()
    rejected = False
    row = None
    blocked = False
    with connection() as db:
        policy = db.execute("SELECT failed_attempts,lockout_minutes FROM security_settings WHERE id=1").fetchone()
        max_attempts = policy["failed_attempts"] if policy else DEFAULT_FAILED_ATTEMPTS
        lockout_minutes = policy["lockout_minutes"] if policy else DEFAULT_LOCKOUT_MINUTES
        cutoff = (current_time - timedelta(minutes=lockout_minutes)).isoformat()
        lock = db.execute("SELECT locked_until FROM login_locks WHERE fingerprint=?", (fingerprint,)).fetchone()
        if lock and lock["locked_until"] > current_time.isoformat():
            rejected = True
            blocked = True
        elif lock:
            db.execute("DELETE FROM login_locks WHERE fingerprint=?", (fingerprint,))
            db.execute("DELETE FROM login_attempts WHERE fingerprint=?", (fingerprint,))
        if rejected:
            recent_attempts = 0
        else:
            db.execute("DELETE FROM login_attempts WHERE attempted_at<?", (cutoff,))
            recent_attempts = db.execute("SELECT COUNT(*) FROM login_attempts WHERE fingerprint=? AND attempted_at>=?", (fingerprint, cutoff)).fetchone()[0]
        if recent_attempts >= max_attempts:
            rejected = True
            blocked = True
        elif not rejected:
            row = db.execute("SELECT * FROM users WHERE email=? AND is_active=1", (email,)).fetchone()
            if not row or not verify_password(payload.password, row["password_hash"]):
                attempted_at = now()
                db.execute("INSERT INTO login_attempts(fingerprint,attempted_at) VALUES(?,?)", (fingerprint, attempted_at.isoformat()))
                rejected = True
                attempt_count = db.execute("SELECT COUNT(*) FROM login_attempts WHERE fingerprint=? AND attempted_at>=?", (fingerprint, cutoff)).fetchone()[0]
                if attempt_count >= max_attempts:
                    blocked = True
                    db.execute("INSERT OR REPLACE INTO login_locks(fingerprint,locked_until) VALUES(?,?)", (fingerprint, (attempted_at + timedelta(minutes=lockout_minutes)).isoformat()))
            else:
                db.execute("DELETE FROM login_attempts WHERE fingerprint=?", (fingerprint,))
                db.execute("DELETE FROM login_locks WHERE fingerprint=?", (fingerprint,))
    if rejected:
        write_audit(row["id"] if row else None, row["email"] if row else email, row["role"] if row else None, "auth.login_blocked" if blocked else "auth.login_failed", "user" if row else None, str(row["id"]) if row else None, "Intento de inicio de sesión rechazado.")
        raise HTTPException(401, "No se pudo iniciar sesión. Verifica los datos o inténtalo más tarde.")
    with connection() as db:
        token = token_urlsafe(48)
        policy = db.execute("SELECT session_hours FROM security_settings WHERE id=1").fetchone()
        session_hours = policy["session_hours"] if policy else SESSION_HOURS
        expires = now() + timedelta(hours=session_hours)
        db.execute("INSERT INTO sessions(token_hash,user_id,expires_at,created_at) VALUES(?,?,?,?)", (sha256(token.encode()).hexdigest(), row["id"], expires.isoformat(), now().isoformat()))
        db.execute("UPDATE users SET last_login_at=? WHERE id=?", (now().isoformat(), row["id"]))
        row = db.execute("SELECT u.id,u.email,u.display_name,u.role,CASE WHEN COALESCE(r.can_manage_users,0)=1 OR EXISTS(SELECT 1 FROM role_modules rm WHERE rm.role_name=u.role AND rm.module_key='users') THEN 1 ELSE 0 END AS can_manage_users FROM users u LEFT JOIN roles r ON r.name=u.role WHERE u.id=?", (row["id"],)).fetchone()
    response.set_cookie(COOKIE, token, max_age=session_hours * 3600, httponly=True, secure=cookie_secure(), samesite="lax", path="/")
    write_audit(row["id"], row["email"], row["role"], "auth.login", "user", str(row["id"]), "Inicio de sesión exitoso.")
    return {"user": user_payload(row), "expires_at": expires.isoformat()}


@app.get("/api/auth/session")
def session(user=Depends(get_current_user)):
    return {"user": user}


@app.post("/api/auth/logout")
def logout(request: Request, response: Response):
    token = request.cookies.get(COOKIE)
    if token:
        with connection() as db:
            user = db.execute("SELECT u.id,u.email,u.role FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=?", (sha256(token.encode()).hexdigest(),)).fetchone()
            db.execute("DELETE FROM sessions WHERE token_hash=?", (sha256(token.encode()).hexdigest(),))
        if user:
            write_audit(user["id"], user["email"], user["role"], "auth.logout", "user", str(user["id"]), "Cierre de sesión.")
    response.delete_cookie(COOKIE, path="/", httponly=True, secure=cookie_secure(), samesite="lax")
    return {"ok": True}


@app.post("/api/auth/password-reset")
def request_password_reset(payload: EmailIn):
    generic = {"message": "Si el correo corresponde a una cuenta activa, recibirás instrucciones para restablecer la contraseña."}
    if not smtp_configured():
        raise HTTPException(503, "La recuperación no está disponible todavía. Contacta al administrador del sistema.")
    email = payload.email.strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        return generic
    with connection() as db:
        user = db.execute("SELECT id FROM users WHERE email=? AND is_active=1", (email,)).fetchone()
        if not user:
            return generic
        raw_token = token_urlsafe(48)
        db.execute("DELETE FROM password_resets WHERE user_id=?", (user["id"],))
        db.execute("INSERT INTO password_resets(token_hash,user_id,expires_at) VALUES(?,?,?)", (sha256(raw_token.encode()).hexdigest(), user["id"], (now() + timedelta(minutes=RESET_MINUTES)).isoformat()))
    try:
        send_reset_email(email, raw_token)
    except Exception:
        with connection() as db:
            db.execute("DELETE FROM password_resets WHERE token_hash=?", (sha256(raw_token.encode()).hexdigest(),))
        logger.warning("Password reset email delivery failed.")
    return generic


@app.post("/api/auth/password-reset/confirm")
def confirm_password_reset(payload: PasswordResetIn):
    token_hash = sha256(payload.token.encode()).hexdigest()
    changed_user = None
    with connection() as db:
        timestamp = now().isoformat()
        reset = db.execute("UPDATE password_resets SET used_at=? WHERE token_hash=? AND used_at IS NULL AND expires_at>? RETURNING user_id", (timestamp, token_hash, timestamp)).fetchone()
        if not reset:
            raise HTTPException(400, "El enlace no es válido, venció o ya se utilizó. Solicita uno nuevo.")
        changed_user = db.execute("SELECT id,email,role FROM users WHERE id=? AND is_active=1", (reset["user_id"],)).fetchone()
        if not changed_user:
            raise HTTPException(400, "La cuenta ya no está activa. Contacta al administrador del sistema.")
        db.execute("UPDATE users SET password_hash=? WHERE id=?", (make_password_hash(payload.password), reset["user_id"]))
        db.execute("DELETE FROM sessions WHERE user_id=?", (reset["user_id"],))
    if changed_user:
        write_audit(changed_user["id"], changed_user["email"], changed_user["role"], "auth.password_reset", "user", str(changed_user["id"]), "Contraseña restablecida mediante enlace de recuperación.")
    return {"message": "Contraseña actualizada. Ya puedes iniciar sesión."}


def validate_admin_user(email: str, name: str, role: str):
    normalized_email = email.strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", normalized_email):
        raise HTTPException(400, "Ingresa un correo electrónico válido.")
    if not name.strip():
        raise HTTPException(400, "Ingresa el nombre del usuario.")
    with connection() as db:
        if not db.execute("SELECT 1 FROM roles WHERE name=?", (role,)).fetchone():
            raise HTTPException(400, "Selecciona uno de los roles disponibles.")
    return normalized_email, name.strip()


@app.get("/api/admin/roles")
def list_roles(admin=Depends(require_admin)):
    with connection() as db:
        visibility_filter = " WHERE r.name<>'Super Administrador'" if admin["role"] != "Super Administrador" else ""
        roles = list(db.execute(f"SELECT r.name,r.description,r.can_manage_users,COUNT(u.id) AS user_count FROM roles r LEFT JOIN users u ON u.role=r.name{visibility_filter} GROUP BY r.name ORDER BY CASE r.name WHEN 'Administrador' THEN 1 WHEN 'Responsable de abastecimiento' THEN 2 ELSE 3 END"))
        result = []
        for row in roles:
            modules = [item[0] for item in db.execute("SELECT module_key FROM role_modules WHERE role_name=?", (row["name"],))]
            result.append({**dict(row), "modules": modules, "permissions": ["Acceso a " + label for key, label in MODULES if key in modules]})
        return result


@app.get("/api/admin/modules")
def list_modules(_admin=Depends(require_admin)):
    return [{"key": key, "label": label} for key, label in MODULES]


def validate_module_keys(modules: list[str]) -> list[str]:
    allowed = {key for key, _ in MODULES}
    if not set(modules).issubset(allowed) or len(set(modules)) != len(modules):
        raise HTTPException(400, "La lista contiene módulos inválidos o repetidos.")
    return sorted(set(modules))


def clean_role_fields(name: str, description: str | None):
    clean_name, clean_description = name.strip(), (description or "").strip()
    if len(clean_name) < 2:
        raise HTTPException(400, "Ingresa un nombre de rol válido.")
    if clean_name.casefold() == "administrador":
        raise HTTPException(409, "El nombre Administrador está reservado para el rol protegido.")
    return clean_name, clean_description


@app.post("/api/admin/roles", status_code=201)
def create_role(payload: AdminRoleCreateIn, _admin=Depends(require_admin)):
    name, description = clean_role_fields(payload.name, payload.description)
    modules = validate_module_keys(payload.modules)
    try:
        with connection() as db:
            db.execute("INSERT INTO roles(name,description,can_manage_users) VALUES(?,?,0)", (name, description))
            db.executemany("INSERT INTO role_modules(role_name,module_key) VALUES(?,?)", [(name, key) for key in modules])
    except IntegrityError:
        raise HTTPException(409, "Ya existe un rol con ese nombre.")
    write_audit(_admin["id"], _admin["email"], _admin["role"], "role.created", "role", name, f"Rol creado: {name}.")
    return {"name": name, "description": description, "modules": modules}


@app.put("/api/admin/roles/{role_name}")
def update_role(role_name: str, payload: AdminRoleUpdateIn, admin=Depends(require_admin)):
    name, description = clean_role_fields(payload.name, payload.description)
    with connection() as db:
        current = db.execute("SELECT can_manage_users FROM roles WHERE name=?", (role_name,)).fetchone()
        if not current:
            raise HTTPException(404, "Rol no encontrado.")
        if role_name == "Super Administrador" and admin["role"] != "Super Administrador":
            raise HTTPException(404, "Rol no encontrado.")
        if role_name == "Administrador" and admin["role"] != "Super Administrador":
            raise HTTPException(403, "Solo el Super Administrador puede editar los permisos del rol Administrador.")
        if role_name == "Administrador" and name != role_name:
            raise HTTPException(409, "El nombre del rol Administrador del sistema no se puede cambiar.")
        if role_name == "Super Administrador" and name != role_name:
            raise HTTPException(409, "El nombre del rol Super Administrador del sistema no se puede cambiar.")
        duplicate = db.execute("SELECT 1 FROM roles WHERE lower(name)=lower(?) AND name<>?", (name, role_name)).fetchone()
        if duplicate:
            raise HTTPException(409, "Ya existe un rol con ese nombre.")
        modules = [item[0] for item in db.execute("SELECT module_key FROM role_modules WHERE role_name=?", (role_name,))]
        db.execute("BEGIN IMMEDIATE")
        db.execute("DELETE FROM role_modules WHERE role_name=?", (role_name,))
        db.execute("UPDATE roles SET name=?,description=? WHERE name=?", (name, description, role_name))
        db.execute("UPDATE users SET role=? WHERE role=?", (name, role_name))
        db.executemany("INSERT INTO role_modules(role_name,module_key) VALUES(?,?)", [(name, key) for key in modules])
    write_audit(admin["id"], admin["email"], admin["role"], "role.updated", "role", name, f"Rol actualizado: {role_name}.")
    return {"name": name, "description": description, "modules": modules}


@app.delete("/api/admin/roles/{role_name}")
def delete_role(role_name: str, _admin=Depends(require_admin)):
    with connection() as db:
        role = db.execute("SELECT can_manage_users FROM roles WHERE name=?", (role_name,)).fetchone()
        if not role:
            raise HTTPException(404, "Rol no encontrado.")
        if role_name == "Super Administrador" and _admin["role"] != "Super Administrador":
            raise HTTPException(404, "Rol no encontrado.")
        if role["can_manage_users"] and not (role_name == "Administrador" and _admin["role"] == "Super Administrador"):
            raise HTTPException(409, "El rol Administrador no se puede eliminar.")
        if role_name == "Administrador" and _admin["role"] != "Super Administrador":
            raise HTTPException(403, "Solo el Super Administrador puede eliminar el rol Administrador.")
        count = db.execute("SELECT COUNT(*) FROM users WHERE role=?", (role_name,)).fetchone()[0]
        if count:
            raise HTTPException(409, f"No se puede eliminar: hay {count} usuario(s) con este rol. Reasígnalos primero.")
        db.execute("BEGIN IMMEDIATE")
        db.execute("DELETE FROM role_modules WHERE role_name=?", (role_name,))
        db.execute("DELETE FROM roles WHERE name=?", (role_name,))
        db.execute("INSERT OR REPLACE INTO deleted_roles(name,deleted_at) VALUES(?,?)", (role_name, now().isoformat()))
    write_audit(_admin["id"], _admin["email"], _admin["role"], "role.deleted", "role", role_name, f"Rol eliminado: {role_name}.")
    return {"deleted": True, "name": role_name}


@app.put("/api/admin/roles/{role_name}/modules")
def update_role_modules(role_name: str, payload: RoleModulesIn, admin=Depends(require_admin)):
    modules = validate_module_keys(payload.modules)
    requested = set(modules)
    with connection() as db:
        role = db.execute("SELECT can_manage_users FROM roles WHERE name=?", (role_name,)).fetchone()
        if not role:
            raise HTTPException(404, "Rol no encontrado.")
        previous_modules = [item[0] for item in db.execute("SELECT module_key FROM role_modules WHERE role_name=? ORDER BY module_key", (role_name,))]
        if role_name == "Super Administrador" and admin["role"] != "Super Administrador":
            raise HTTPException(404, "Rol no encontrado.")
        if role["can_manage_users"] and not (role_name == "Administrador" and admin["role"] == "Super Administrador"):
            raise HTTPException(409, "El rol Administrador conserva acceso a todos los módulos.")
        if role_name == "Administrador" and admin["role"] != "Super Administrador":
            raise HTTPException(403, "Solo el Super Administrador puede editar los permisos del rol Administrador.")
        if role_name == admin["role"]:
            if role_name.casefold() == "super administrador" and "users" not in requested:
                raise HTTPException(409, "El Super Administrador debe conservar el módulo Usuarios y roles para administrar el sistema.")
            if role_name.casefold() != "super administrador" and not set(admin["modules"]).issubset(requested):
                raise HTTPException(409, "No puedes quitarte módulos del rol con el que estás administrando.")
        db.execute("DELETE FROM role_modules WHERE role_name=?", (role_name,))
        db.executemany("INSERT INTO role_modules(role_name,module_key) VALUES(?,?)", [(role_name, key) for key in sorted(requested)])
    write_audit(admin["id"], admin["email"], admin["role"], "role.modules_updated", "role", role_name,
                f"Permisos por módulo actualizados para {role_name}, antes: {', '.join(previous_modules) or 'sin módulos'}, ahora: {', '.join(sorted(requested)) or 'sin módulos'}.")
    return {"name": role_name, "modules": modules}


@app.get("/api/admin/users")
def list_users(admin=Depends(require_admin)):
    with connection() as db:
        query = "SELECT id, email, display_name AS name, role, is_active, created_at, last_login_at FROM users"
        if admin["role"] != "Super Administrador":
            query += " WHERE role<>'Super Administrador'"
        return [dict(row) for row in db.execute(query + " ORDER BY lower(display_name)")]


@app.post("/api/admin/users", status_code=201)
def create_user(payload: AdminUserCreateIn, _admin=Depends(require_admin)):
    if payload.role == "Super Administrador" and _admin["role"] != "Super Administrador":
        raise HTTPException(403, "Solo el Super Administrador puede asignar ese rol.")
    email, name = validate_admin_user(payload.email, payload.name, payload.role)
    try:
        with connection() as db:
            cur = db.execute(
                "INSERT INTO users(email,display_name,password_hash,is_active,role,created_at) VALUES(?,?,?,1,?,?)",
                (email, name, make_password_hash(payload.password), payload.role, now().isoformat()),
            )
            created = dict(db.execute(
                "SELECT id,email,display_name AS name,role,is_active,created_at,last_login_at FROM users WHERE id=?",
                (cur.lastrowid,),
            ).fetchone())
    except IntegrityError:
        raise HTTPException(409, "Ya existe una cuenta con ese correo.")
    write_audit(_admin["id"], _admin["email"], _admin["role"], "user.created", "user", str(created["id"]), f"Usuario {created['name']} ({created['email']}) creado con rol {created['role']}.")
    return created


@app.put("/api/admin/users/{user_id}")
def update_user(user_id: int, payload: AdminUserUpdateIn, admin=Depends(require_admin)):
    email, name = validate_admin_user(payload.email, payload.name, payload.role)
    with connection() as db:
        current = db.execute("SELECT u.id,u.role,u.is_active,CASE WHEN COALESCE(r.can_manage_users,0)=1 OR EXISTS(SELECT 1 FROM role_modules rm WHERE rm.role_name=u.role AND rm.module_key='users') THEN 1 ELSE 0 END AS can_manage_users FROM users u LEFT JOIN roles r ON r.name=u.role WHERE u.id=?", (user_id,)).fetchone()
        if not current:
            raise HTTPException(404, "Usuario no encontrado.")
        if current["role"] == "Super Administrador" and admin["role"] != "Super Administrador":
            raise HTTPException(404, "Usuario no encontrado.")
        if payload.role == "Super Administrador" and admin["role"] != "Super Administrador":
            raise HTTPException(403, "Solo el Super Administrador puede asignar ese rol.")
        target_role = db.execute("SELECT can_manage_users FROM roles WHERE name=?", (payload.role,)).fetchone()
        will_have_admin_permission = bool(target_role["can_manage_users"] or db.execute("SELECT 1 FROM role_modules WHERE role_name=? AND module_key='users'", (payload.role,)).fetchone())
        if user_id == admin["id"]:
            target_modules = {key for key, _ in MODULES} if target_role["can_manage_users"] else {row[0] for row in db.execute("SELECT module_key FROM role_modules WHERE role_name=?", (payload.role,))}
            if not payload.is_active:
                raise HTTPException(409, "No puedes suspender tu propia cuenta.")
            if not admin["can_manage_users"] or not set(admin["modules"]).issubset(target_modules) or not will_have_admin_permission:
                raise HTTPException(409, "Solo puedes cambiarte a un rol que conserve todos tus permisos de administración.")
        removing_admin = current["can_manage_users"] and current["is_active"] and (not will_have_admin_permission or not payload.is_active)
        if removing_admin:
            active_admins = db.execute("SELECT COUNT(*) FROM users u JOIN roles r ON r.name=u.role WHERE u.is_active=1 AND (r.can_manage_users=1 OR EXISTS(SELECT 1 FROM role_modules rm WHERE rm.role_name=u.role AND rm.module_key='users'))").fetchone()[0]
            if active_admins <= 1:
                raise HTTPException(409, "Debe quedar al menos un administrador activo.")
        try:
            db.execute("UPDATE users SET email=?,display_name=?,role=?,is_active=? WHERE id=?", (email, name, payload.role, int(payload.is_active), user_id))
            if payload.password:
                db.execute("UPDATE users SET password_hash=? WHERE id=?", (make_password_hash(payload.password), user_id))
            if not payload.is_active or payload.password:
                db.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
            updated = db.execute("SELECT u.id,u.email,u.display_name AS name,u.role,u.is_active,u.created_at,u.last_login_at,CASE WHEN COALESCE(r.can_manage_users,0)=1 OR EXISTS(SELECT 1 FROM role_modules rm WHERE rm.role_name=u.role AND rm.module_key='users') THEN 1 ELSE 0 END AS can_manage_users FROM users u LEFT JOIN roles r ON r.name=u.role WHERE u.id=?", (user_id,)).fetchone()
            result = dict(updated)
        except IntegrityError:
            raise HTTPException(409, "Ya existe una cuenta con ese correo.")
    password_change = ", contraseña actualizada y sesiones revocadas" if payload.password else ""
    state_label = "activo" if payload.is_active else "suspendido"
    write_audit(admin["id"], admin["email"], admin["role"], "user.updated", "user", str(user_id), f"Usuario actualizado, rol asignado: {result['role']}, estado: {state_label}{password_change}.")
    if user_id == admin["id"] and (not payload.is_active or not result["can_manage_users"] or bool(payload.password)):
        return {**result, "session_revoked": True}
    return result


@app.delete("/api/admin/users/{user_id}")
def delete_user(user_id: int, admin=Depends(require_admin)):
    with connection() as db:
        target = db.execute("SELECT u.id,u.email,u.role,u.is_active,CASE WHEN COALESCE(r.can_manage_users,0)=1 OR EXISTS(SELECT 1 FROM role_modules rm WHERE rm.role_name=u.role AND rm.module_key='users') THEN 1 ELSE 0 END AS can_manage_users FROM users u LEFT JOIN roles r ON r.name=u.role WHERE u.id=?", (user_id,)).fetchone()
        if not target:
            raise HTTPException(404, "Usuario no encontrado.")
        if target["role"] == "Super Administrador" and admin["role"] != "Super Administrador":
            raise HTTPException(404, "Usuario no encontrado.")
        if user_id == admin["id"]:
            raise HTTPException(409, "No puedes eliminar tu propia cuenta.")
        if target["is_active"] and target["can_manage_users"]:
            remaining = db.execute("SELECT COUNT(*) FROM users u JOIN roles r ON r.name=u.role WHERE u.id<>? AND u.is_active=1 AND (r.can_manage_users=1 OR EXISTS(SELECT 1 FROM role_modules rm WHERE rm.role_name=u.role AND rm.module_key='users'))", (user_id,)).fetchone()[0]
            if remaining == 0:
                raise HTTPException(409, "No se puede eliminar al último administrador activo.")
        db.execute("BEGIN IMMEDIATE")
        db.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
        db.execute("DELETE FROM password_resets WHERE user_id=?", (user_id,))
        db.execute("DELETE FROM users WHERE id=?", (user_id,))
    write_audit(admin["id"], admin["email"], admin["role"], "user.deleted", "user", str(user_id), f"Usuario {target['email']} eliminado, rol anterior: {target['role']}.")
    return {"deleted": True, "id": user_id}


def list_security_backups():
    backup_dir = ROOT / "backups"
    backup_dir.mkdir(exist_ok=True)
    files = sorted((path for path in backup_dir.glob("plaza_vea_*") if path.suffix in {".sqlite3", ".dump"}), key=lambda path: path.stat().st_mtime, reverse=True)
    return [{"filename": path.name, "size_bytes": path.stat().st_size, "created_at": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()} for path in files[:20]]


def pg_dump_executable() -> str | None:
    executable = shutil.which("pg_dump")
    if executable:
        return executable
    program_files = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "PostgreSQL"
    candidates = sorted(program_files.glob("*/bin/pg_dump.exe"), reverse=True)
    return str(candidates[0]) if candidates else None


AUDIT_MODULE_SQL = "CASE WHEN action LIKE 'auth.%' THEN 'Autenticación' WHEN action LIKE 'user.%' OR action LIKE 'role.%' THEN 'Usuarios y roles' WHEN action LIKE 'product.%' THEN 'Productos' WHEN action LIKE 'sale.%' THEN 'Ventas' WHEN action LIKE 'inventory.%' THEN 'Inventarios' WHEN action LIKE 'replenishment.%' THEN 'Reposición' WHEN action LIKE 'forecast.%' THEN 'Predicción con IA' WHEN action LIKE 'alert.%' THEN 'Alertas' WHEN action LIKE 'security.%' THEN 'Seguridad' WHEN action LIKE 'audit.%' THEN 'Auditoría' ELSE 'Sistema' END"
AUDIT_RESULT_SQL = "CASE WHEN action IN ('auth.login_failed','auth.login_blocked') THEN 'Fallido' ELSE 'Exitoso' END"


def audit_filters(q: str, module: str | None, action: str | None, date_from: date | None, date_to: date | None, user):
    clauses = []
    values: list[str] = []
    if q.strip():
        pattern = f"%{q.strip()}%"
        clauses.append("(COALESCE(u.display_name,'') LIKE ? OR COALESCE(a.actor_email,'') LIKE ? OR a.action LIKE ? OR COALESCE(a.target_id,'') LIKE ? OR a.summary LIKE ?)")
        values.extend([pattern, pattern, pattern, pattern, pattern])
    if module:
        clauses.append(f"{AUDIT_MODULE_SQL}=?")
        values.append(module)
    if action:
        clauses.append("a.action=?")
        values.append(action)
    if date_from:
        clauses.append("a.created_at>=?")
        values.append(datetime.combine(date_from, datetime.min.time(), PERU_TIMEZONE).astimezone(timezone.utc).isoformat())
    if date_to:
        clauses.append("a.created_at<?")
        values.append(datetime.combine(date_to + timedelta(days=1), datetime.min.time(), PERU_TIMEZONE).astimezone(timezone.utc).isoformat())
    return (" WHERE " + " AND ".join(clauses) if clauses else ""), values


def audit_rows(where: str, values: list[str], limit: int | None = None, offset: int = 0):
    sql = f"SELECT a.id,CASE WHEN a.actor_id IS NULL THEN 'Usuario no identificado' ELSE COALESCE(u.display_name,a.actor_email,'Usuario eliminado') END AS actor,a.action,{AUDIT_MODULE_SQL} AS module,a.target_id AS related_record,a.summary,a.created_at,{AUDIT_RESULT_SQL} AS result FROM audit_log a LEFT JOIN users u ON u.id=a.actor_id{where} ORDER BY a.id DESC"
    if limit is not None:
        sql += " LIMIT ? OFFSET ?"
        values = [*values, str(limit), str(offset)]
    with connection() as db:
        return [dict(row) for row in db.execute(sql, values)]


@app.get("/api/admin/audit")
def get_audit_events(q: str = Query(default="", max_length=120), module: str | None = None, action: str | None = None, date_from: date | None = None, date_to: date | None = None, page: int = Query(default=1, ge=1), page_size: int = Query(default=10, ge=1, le=100), user=Depends(require_module("audit"))):
    if date_from and date_to and date_from > date_to:
        raise HTTPException(422, "La fecha inicial no puede ser posterior a la fecha final.")
    where, values = audit_filters(q, module, action, date_from, date_to, user)
    with connection() as db:
        total = db.execute("SELECT COUNT(*) FROM audit_log a LEFT JOIN users u ON u.id=a.actor_id" + where, values).fetchone()[0]
        actions = [row[0] for row in db.execute("SELECT DISTINCT a.action FROM audit_log a LEFT JOIN users u ON u.id=a.actor_id" + where + " ORDER BY a.action", values)]
    items = audit_rows(where, values, page_size, (page - 1) * page_size)
    return {"items": items, "total": total, "page": page, "page_size": page_size, "pages": max(1, (total + page_size - 1) // page_size), "actions": actions}


@app.get("/api/admin/audit/export")
def export_audit_events(q: str = Query(default="", max_length=120), module: str | None = None, action: str | None = None, date_from: date | None = None, date_to: date | None = None, user=Depends(require_module("audit"))):
    if date_from and date_to and date_from > date_to:
        raise HTTPException(422, "La fecha inicial no puede ser posterior a la fecha final.")
    write_audit(user["id"], user["email"], user["role"], "audit.exported", "audit_log", None, "Exportó la bitácora en CSV.")
    where, values = audit_filters(q, module, action, date_from, date_to, user)
    rows = audit_rows(where, values)
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["Usuario", "Fecha y hora", "Módulo", "Acción realizada", "Registro relacionado", "Resultado", "Detalle"])
    for row in rows:
        fields = [row["actor"], row["created_at"], row["module"], row["action"], row["related_record"] or "—", row["result"], row["summary"]]
        writer.writerow([("'" + value) if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r") else value for value in fields])
    output.seek(0)
    return StreamingResponse(iter(["\ufeff" + output.getvalue()]), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f"attachment; filename=bitacora_{now().strftime('%Y%m%d')}.csv"})


@app.get("/api/admin/security")
def security_overview(admin=Depends(require_module("security"))):
    with connection() as db:
        settings = db.execute("SELECT session_hours,failed_attempts,lockout_minutes,updated_at FROM security_settings WHERE id=1").fetchone()
        if admin["role"] == "Super Administrador":
            events = list(db.execute("SELECT id,actor_email AS actor,actor_role,action,target_type,target_id,summary,created_at FROM audit_log ORDER BY id DESC LIMIT 100"))
            review = db.execute("SELECT reviewer,reviewer_email,notes,reviewed_at FROM security_reviews ORDER BY id DESC LIMIT 1").fetchone()
        else:
            events = list(db.execute("SELECT id,actor_email AS actor,actor_role,action,target_type,target_id,summary,created_at FROM audit_log WHERE COALESCE(actor_role,'')<>'Super Administrador' AND NOT (target_type='user' AND target_id IN (SELECT CAST(id AS TEXT) FROM users WHERE role='Super Administrador')) ORDER BY id DESC LIMIT 100"))
            review = db.execute("SELECT reviewer,reviewer_email,notes,reviewed_at FROM security_reviews WHERE reviewer_role<>'Super Administrador' ORDER BY id DESC LIMIT 1").fetchone()
    backups = list_security_backups() if admin["role"] == "Super Administrador" else []
    return {"settings": dict(settings), "events": [dict(item) for item in events], "latest_review": dict(review) if review else None, "backups": backups, "can_manage": admin["role"] == "Super Administrador"}


@app.put("/api/admin/security/settings")
def update_security_settings(payload: SecuritySettingsIn, admin=Depends(require_super_admin)):
    updated_at = now().isoformat()
    with connection() as db:
        db.execute("UPDATE security_settings SET session_hours=?,failed_attempts=?,lockout_minutes=?,updated_at=?,updated_by=? WHERE id=1", (payload.session_hours, payload.failed_attempts, payload.lockout_minutes, updated_at, admin["email"]))
    write_audit(admin["id"], admin["email"], admin["role"], "security.settings_updated", "security_settings", "1", f"Duración de sesión: {payload.session_hours} h, bloqueo: {payload.failed_attempts} intentos en {payload.lockout_minutes} min.")
    return {**payload.model_dump(), "updated_at": updated_at}


@app.post("/api/admin/security/backups", status_code=201)
def create_security_backup(admin=Depends(require_super_admin)):
    backup_dir = ROOT / "backups"
    backup_dir.mkdir(exist_ok=True)
    backend = database_backend()
    suffix = ".dump" if backend == "postgresql" else ".sqlite3"
    filename = f"plaza_vea_{now().strftime('%Y%m%d_%H%M%S_%f')}{suffix}"
    path = backup_dir / filename
    if backend == "postgresql":
        try:
            dump = pg_dump_executable()
            if not dump:
                raise HTTPException(503, "No se encontró pg_dump para respaldar PostgreSQL.")
            url = configured_url()
            env = os.environ.copy()
            if url.password:
                env["PGPASSWORD"] = url.password
            args = [dump, "--format=custom", "--no-owner", "--no-acl", "--file", str(path),
                    "--host", url.host or "127.0.0.1", "--port", str(url.port or 5432),
                    "--username", url.username or "postgres", "--dbname", url.database]
            sslmode = (url.query or {}).get("sslmode")
            if sslmode:
                env["PGSSLMODE"] = str(sslmode)
            completed = subprocess.run(args, env=env, capture_output=True, text=True, timeout=120, check=False)
            if completed.returncode:
                logger.error("pg_dump failed: %s", completed.stderr[:1000])
                raise HTTPException(503, "No se pudo crear el respaldo PostgreSQL. Revisa la conexión y permisos.")
        except Exception:
            path.unlink(missing_ok=True)
            raise
    else:
        source = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True, timeout=10)
        destination = sqlite3.connect(path)
        try:
            source.backup(destination)
        except Exception:
            path.unlink(missing_ok=True)
            raise
        finally:
            destination.close()
            source.close()
    write_audit(admin["id"], admin["email"], admin["role"], "security.backup_created", "backup", filename, "Copia de seguridad creada.")
    return {"filename": filename, "size_bytes": path.stat().st_size, "created_at": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()}


@app.get("/api/admin/security/backups/{filename}")
def download_security_backup(filename: str, _admin=Depends(require_super_admin)):
    if not re.fullmatch(r"plaza_vea_\d{8}_\d{6}_\d{6}\.(sqlite3|dump)", filename):
        raise HTTPException(404, "Copia no encontrada.")
    path = ROOT / "backups" / filename
    if not path.is_file():
        raise HTTPException(404, "Copia no encontrada.")
    media_type = "application/vnd.sqlite3" if path.suffix == ".sqlite3" else "application/octet-stream"
    return FileResponse(path, media_type=media_type, filename=filename)


@app.post("/api/admin/security/reviews", status_code=201)
def create_security_review(payload: SecurityReviewIn, admin=Depends(require_super_admin)):
    reviewed_at = now().isoformat()
    notes = payload.notes.strip()
    with connection() as db:
        db.execute("INSERT INTO security_reviews(reviewer_id,reviewer,reviewer_email,reviewer_role,notes,reviewed_at) VALUES(?,?,?,?,?,?)", (admin["id"], admin["name"], admin["email"], admin["role"], notes, reviewed_at))
    write_audit(admin["id"], admin["email"], admin["role"], "security.review_recorded", "security_review", None, "Revisión de seguridad registrada." + (f" Nota: {notes}" if notes else ""))
    return {"reviewer": admin["name"], "reviewer_email": admin["email"], "notes": notes, "reviewed_at": reviewed_at}


@app.get("/api/products")
def list_products(_user=Depends(require_module("products"))):
    with connection() as db:
        return [row_product(r) for r in db.execute("SELECT * FROM products ORDER BY id")]


@app.get("/api/products/web-catalog")
def list_web_catalog_products(_user=Depends(require_module("products"))):
    with connection() as db:
        return [dict(row) for row in db.execute(
            "SELECT item_id,product_id,reference_code,ean,name,brand,category,unit,price_pen,list_price_pen,"
            "online_available,product_url,source_category_url,source,captured_at "
            "FROM web_catalog_products ORDER BY name"
        )]


@app.post("/api/products/{product_id}/initial-stock")
def register_initial_stock(product_id: int, payload: InitialStockIn, user=Depends(require_module("inventory"))):
    reference = payload.reference.strip()
    if not reference:
        raise HTTPException(422, "Ingresa la referencia del conteo físico.")
    timestamp = now().astimezone(PERU_TIMEZONE).isoformat()
    with connection() as db:
        product = db.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone()
        if not product:
            raise HTTPException(404, "Producto no encontrado.")
        if product["inventory_registered"]:
            raise HTTPException(409, "Este producto ya tiene un conteo de inventario registrado.")
        db.execute("UPDATE products SET stock=?,inventory_registered=1 WHERE id=?", (payload.quantity, product_id))
        if payload.quantity > 0:
            movement = db.execute(
                "INSERT INTO inventory_movements(product_id,movement_type,quantity,stock_before,stock_after,occurred_at,reason,reference,user_id) "
                "VALUES(?,'Ajuste positivo',?,0,?,?,?,?,?) RETURNING id",
                (product_id, payload.quantity, payload.quantity, timestamp, "Conteo físico inicial", reference, user["id"]),
            ).fetchone()
            target_id = str(movement["id"])
            target_type = "inventory_movement"
        else:
            target_id = str(product_id)
            target_type = "product"
        db.execute(
            "INSERT INTO audit_log(actor_id,actor_email,actor_role,action,target_type,target_id,summary,created_at) VALUES(?,?,?,?,?,?,?,?)",
            (user["id"], user["email"], user["role"], "inventory.initial_count_recorded", target_type, target_id,
             f"Conteo físico inicial de {product['name']}: {payload.quantity} unidades. Referencia: {reference}.", now().isoformat()),
        )
        updated = row_product(db.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone())
    refresh_alerts_after_change()
    return updated


@app.post("/api/products", status_code=201)
def create_product(payload: ProductIn, _user=Depends(require_module("products"))):
    try:
        with connection() as db:
            product_values = payload.model_dump()
            initial_stock = product_values["stock"]
            product_values["stock"] = 0
            cur = db.execute("INSERT INTO products(code,name,category,price,stock,unit_of_measure,minimum_stock,safety_stock,maximum_stock,supplier,lead_time_days) VALUES(?,?,?,?,?,?,?,?,?,?,?)", tuple(product_values.values()))
            if initial_stock > 0:
                apply_inventory_movement(db, cur.lastrowid, "Ajuste positivo", initial_stock, now().astimezone(PERU_TIMEZONE).isoformat(), "Saldo inicial del producto", f"APERTURA-{payload.code}", _user["id"])
            created = row_product(db.execute("SELECT * FROM products WHERE id=?", (cur.lastrowid,)).fetchone())
    except IntegrityError:
        raise HTTPException(409, "Ya existe un producto con ese código.")
    write_audit(_user["id"], _user["email"], _user["role"], "product.created", "product", str(created["id"]), f"Producto creado: {created['code']}.")
    refresh_alerts_after_change()
    return created


@app.put("/api/products/{product_id}")
def update_product(product_id: int, payload: ProductIn, _user=Depends(require_module("products"))):
    try:
        with connection() as db:
            current = db.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone()
            if not current:
                raise HTTPException(404, "Producto no encontrado.")
            if payload.stock != current["stock"]:
                raise HTTPException(422, "Las existencias solo se modifican con movimientos del inventario.")
            cur = db.execute("UPDATE products SET code=?,name=?,category=?,price=?,unit_of_measure=?,minimum_stock=?,safety_stock=?,maximum_stock=?,supplier=?,lead_time_days=? WHERE id=?", (payload.code, payload.name, payload.category, payload.price, payload.unit_of_measure, payload.minimum_stock, payload.safety_stock, payload.maximum_stock, payload.supplier, payload.lead_time_days, product_id))
            if not cur.rowcount:
                raise HTTPException(404, "Producto no encontrado.")
            updated = row_product(db.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone())
            timestamp = now().isoformat()
            db.execute("INSERT INTO audit_log(actor_id,actor_email,actor_role,action,target_type,target_id,summary,created_at) VALUES(?,?,?,?,?,?,?,?)",
                       (_user["id"], _user["email"], _user["role"], "product.updated", "product", str(product_id), f"Producto actualizado: {updated['code']}.", timestamp))
            policy_fields = {
                "minimum_stock": "stock mínimo", "safety_stock": "stock de seguridad",
                "maximum_stock": "stock máximo", "lead_time_days": "plazo de entrega",
                "supplier": "proveedor",
            }
            changes = [f"{label}: {current[key] if current[key] is not None else 'sin configurar'} → {updated[key] if updated[key] is not None else 'sin configurar'}"
                       for key, label in policy_fields.items() if current[key] != updated[key]]
            if changes:
                db.execute("INSERT INTO audit_log(actor_id,actor_email,actor_role,action,target_type,target_id,summary,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (_user["id"], _user["email"], _user["role"], "product.policy_updated", "product", str(product_id), f"Política de {updated['code']} modificada. " + ", ".join(changes), timestamp))
    except IntegrityError:
        raise HTTPException(409, "Ya existe un producto con ese código.")
    refresh_alerts_after_change()
    return updated


def _sales_filters(from_date: date | None, to_date: date | None, product_id: int | None, category: str | None):
    if from_date and to_date and from_date > to_date:
        raise HTTPException(422, "La fecha inicial no puede ser posterior a la fecha final.")
    clauses = []
    params = []
    if from_date:
        clauses.append("s.occurred_at >= ?")
        params.append(from_date.isoformat())
    if to_date:
        clauses.append("s.occurred_at < ?")
        params.append((to_date + timedelta(days=1)).isoformat())
    if product_id:
        clauses.append("EXISTS (SELECT 1 FROM sale_items si WHERE si.sale_id=s.id AND si.product_id=?)")
        params.append(product_id)
    if category:
        clauses.append("EXISTS (SELECT 1 FROM sale_items si JOIN products p ON p.id=si.product_id WHERE si.sale_id=s.id AND p.category=?)")
        params.append(category)
    return (" WHERE " + " AND ".join(clauses)) if clauses else "", params


def _sale_detail(db, sale_id: int):
    sale = db.execute("SELECT s.*,u.display_name AS cashier FROM sales s LEFT JOIN users u ON u.id=s.created_by WHERE s.id=?", (sale_id,)).fetchone()
    if not sale:
        return None
    items = db.execute("SELECT si.id,si.product_id,p.code,p.name,p.category,si.quantity,si.unit_price,si.subtotal FROM sale_items si JOIN products p ON p.id=si.product_id WHERE si.sale_id=? ORDER BY si.id", (sale_id,)).fetchall()
    return {
        "id": sale["id"], "occurred_at": sale["occurred_at"], "reference": sale["reference"],
        "subtotal": float(sale["subtotal"]), "total": float(sale["total"]), "cashier": sale["cashier"],
        "items": [{"id": item["id"], "product_id": item["product_id"], "code": item["code"], "name": item["name"], "category": item["category"], "quantity": item["quantity"], "unit_price": float(item["unit_price"]), "subtotal": float(item["subtotal"])} for item in items],
    }


@app.get("/api/sales/products")
def sales_products(_user=Depends(require_module("sales"))):
    with connection() as db:
        rows = db.execute("SELECT id,code,name,category,price,stock,unit_of_measure FROM products WHERE inventory_registered=1 ORDER BY name").fetchall()
    return [{"id": row["id"], "code": row["code"], "name": row["name"], "category": row["category"], "price": float(row["price"]), "stock": row["stock"], "unit_of_measure": row["unit_of_measure"]} for row in rows]


@app.post("/api/sales", status_code=201)
def create_sale(payload: SaleIn, _user=Depends(require_module("sales"))):
    occurred_at = payload.occurred_at or now()
    if occurred_at.tzinfo is None:
        occurred_at = occurred_at.replace(tzinfo=PERU_TIMEZONE)
    occurred_text = occurred_at.astimezone(PERU_TIMEZONE).isoformat()
    cents = Decimal("0.01")
    sale_lines = []
    total = Decimal("0.00")
    try:
        with connection() as db:
            for line in sorted(payload.items, key=lambda item: item.product_id):
                product = db.execute("SELECT id,code,name,price,inventory_registered FROM products WHERE id=?", (line.product_id,)).fetchone()
                if not product:
                    raise HTTPException(404, f"El producto {line.product_id} no existe.")
                if not product["inventory_registered"]:
                    raise HTTPException(409, f"Registra primero el conteo físico inicial de {product['name']}.")
                unit_price = line.unit_price if line.unit_price is not None else Decimal(str(product["price"]))
                line_total = (unit_price * line.quantity).quantize(cents, rounding=ROUND_HALF_UP)
                updated = db.execute("UPDATE products SET stock=stock-? WHERE id=? AND stock>=? RETURNING stock", (line.quantity, line.product_id, line.quantity)).fetchone()
                if not updated:
                    current = db.execute("SELECT stock FROM products WHERE id=?", (line.product_id,)).fetchone()
                    raise HTTPException(409, f"Stock insuficiente para {product['name']}. Disponible: {current['stock'] if current else 0}.")
                stock_after = updated["stock"]
                stock_before = stock_after + line.quantity
                sale_lines.append({"product": product, "quantity": line.quantity, "unit_price": unit_price, "subtotal": line_total, "stock_before": stock_before, "stock_after": stock_after})
                total += line_total
            total = total.quantize(cents, rounding=ROUND_HALF_UP)
            total_value = format(total, ".2f")
            inserted = db.execute("INSERT INTO sales(occurred_at,reference,subtotal,total,created_by) VALUES(?,?,?,?,?) RETURNING id", (occurred_text, payload.reference, total_value, total_value, _user["id"])).fetchone()
            sale_id = inserted["id"]
            for line in sale_lines:
                product = line["product"]
                db.execute("INSERT INTO sale_items(sale_id,product_id,quantity,unit_price,subtotal) VALUES(?,?,?,?,?)", (sale_id, product["id"], line["quantity"], format(line["unit_price"], ".2f"), format(line["subtotal"], ".2f")))
                db.execute("INSERT INTO inventory_movements(product_id,movement_type,quantity,stock_before,stock_after,occurred_at,reason,reference,user_id) VALUES(?,?,?,?,?,?,?,?,?)", (product["id"], "Salida por venta", line["quantity"], line["stock_before"], line["stock_after"], occurred_text, f"Salida por venta {payload.reference}", payload.reference, _user["id"]))
            db.execute("INSERT INTO audit_log(actor_id,actor_email,actor_role,action,target_type,target_id,summary,created_at) VALUES(?,?,?,?,?,?,?,?)", (_user["id"], _user["email"], _user["role"], "sale.created", "sale", payload.reference, f"Venta registrada por S/ {total:.2f}.", now().isoformat()))
            created = _sale_detail(db, sale_id)
    except IntegrityError:
        raise HTTPException(409, "El número de comprobante ya fue registrado.")
    refresh_alerts_after_change()
    return created


@app.get("/api/sales")
def list_sales(from_date: date | None = None, to_date: date | None = None, product_id: int | None = Query(default=None, gt=0), category: str | None = None, page: int = Query(default=1, ge=1), page_size: int = Query(default=25, ge=1, le=100), _user=Depends(require_module("sales"))):
    where, params = _sales_filters(from_date, to_date, product_id, category)
    with connection() as db:
        total = db.execute(f"SELECT COUNT(*) FROM sales s{where}", params).fetchone()[0]
        rows = db.execute(f"SELECT s.id,s.occurred_at,s.reference,s.subtotal,s.total,u.display_name AS cashier,(SELECT COUNT(*) FROM sale_items si WHERE si.sale_id=s.id) AS items_count,(SELECT COALESCE(SUM(si.quantity),0) FROM sale_items si WHERE si.sale_id=s.id) AS units FROM sales s LEFT JOIN users u ON u.id=s.created_by{where} ORDER BY s.occurred_at DESC,s.id DESC LIMIT ? OFFSET ?", (*params, page_size, (page - 1) * page_size)).fetchall()
    return {"items": [{"id": row["id"], "occurred_at": row["occurred_at"], "reference": row["reference"], "subtotal": float(row["subtotal"]), "total": float(row["total"]), "cashier": row["cashier"], "items_count": row["items_count"], "units": row["units"]} for row in rows], "total": total, "page": page, "page_size": page_size, "pages": (total + page_size - 1) // page_size}


@app.get("/api/sales/stats")
def sales_stats(from_date: date | None = None, to_date: date | None = None, product_id: int | None = Query(default=None, gt=0), category: str | None = None, _user=Depends(require_module("sales"))):
    where, params = _sales_filters(from_date, to_date, product_id, category)
    with connection() as db:
        summary = db.execute(f"SELECT COALESCE(SUM(s.total),0) AS total_sales,COALESCE(SUM((SELECT COALESCE(SUM(si.quantity),0) FROM sale_items si WHERE si.sale_id=s.id)),0) AS units,COUNT(*) AS transactions FROM sales s{where}", params).fetchone()
        daily = db.execute(f"SELECT substr(s.occurred_at,1,10) AS day,SUM(s.total) AS sales,COALESCE(SUM((SELECT COALESCE(SUM(si.quantity),0) FROM sale_items si WHERE si.sale_id=s.id)),0) AS units FROM sales s{where} GROUP BY substr(s.occurred_at,1,10) ORDER BY day", params).fetchall()
    transactions = summary["transactions"]
    return {"total_sales": float(summary["total_sales"]), "units": summary["units"], "transactions": transactions, "average_ticket": float(summary["total_sales"]) / transactions if transactions else 0, "daily": [{"day": row["day"], "sales": float(row["sales"]), "units": row["units"]} for row in daily]}


@app.get("/api/sales/{sale_id}")
def get_sale(sale_id: int, _user=Depends(require_module("sales"))):
    with connection() as db:
        sale = _sale_detail(db, sale_id)
    if not sale:
        raise HTTPException(404, "Venta no encontrada.")
    return sale


@app.get("/api/inventory/products")
def inventory_products(_user=Depends(require_module("inventory"))):
    with connection() as db:
        rows = db.execute("SELECT * FROM products ORDER BY name").fetchall()
    return [row_product(row) for row in rows]


def _movement_filters(from_date: date | None, to_date: date | None, product_id: int | None, movement_type: str | None):
    if from_date and to_date and from_date > to_date:
        raise HTTPException(422, "La fecha inicial no puede ser posterior a la fecha final.")
    clauses, params = [], []
    if from_date:
        clauses.append("m.occurred_at >= ?")
        params.append(from_date.isoformat())
    if to_date:
        clauses.append("m.occurred_at < ?")
        params.append((to_date + timedelta(days=1)).isoformat())
    if product_id:
        clauses.append("m.product_id=?")
        params.append(product_id)
    if movement_type:
        clauses.append("m.movement_type=?")
        params.append(movement_type)
    return (" WHERE " + " AND ".join(clauses)) if clauses else "", params


@app.get("/api/inventory/movements")
def list_inventory_movements(from_date: date | None = None, to_date: date | None = None, product_id: int | None = Query(default=None, gt=0), movement_type: str | None = None, page: int = Query(default=1, ge=1), page_size: int = Query(default=25, ge=1, le=100), _user=Depends(require_module("inventory"))):
    allowed_types = {"Entrada por recepción", "Salida por venta", "Ajuste positivo", "Ajuste negativo", "Devolución", "Merma"}
    if movement_type and movement_type not in allowed_types:
        raise HTTPException(422, "Tipo de movimiento no válido.")
    where, params = _movement_filters(from_date, to_date, product_id, movement_type)
    with connection() as db:
        total = db.execute(f"SELECT COUNT(*) FROM inventory_movements m{where}", params).fetchone()[0]
        rows = db.execute(f"SELECT m.id,m.product_id,p.code,p.name,p.category,m.movement_type,m.quantity,m.stock_before,m.stock_after,m.occurred_at,m.reason,m.reference,m.user_id,u.display_name AS user_name FROM inventory_movements m JOIN products p ON p.id=m.product_id LEFT JOIN users u ON u.id=m.user_id{where} ORDER BY m.occurred_at DESC,m.id DESC LIMIT ? OFFSET ?", (*params, page_size, (page - 1) * page_size)).fetchall()
    return {"items": [{"id": row["id"], "product_id": row["product_id"], "code": row["code"], "product": row["name"], "category": row["category"], "movement_type": row["movement_type"], "quantity": row["quantity"], "stock_before": row["stock_before"], "stock_after": row["stock_after"], "occurred_at": row["occurred_at"], "reason": row["reason"], "reference": row["reference"], "user_id": row["user_id"], "user_name": row["user_name"]} for row in rows], "total": total, "page": page, "page_size": page_size, "pages": (total + page_size - 1) // page_size}


@app.post("/api/inventory/movements", status_code=201)
def create_inventory_movement(payload: InventoryMovementIn, _user=Depends(require_module("inventory"))):
    occurred_at = payload.occurred_at or now()
    if occurred_at.tzinfo is None:
        occurred_at = occurred_at.replace(tzinfo=PERU_TIMEZONE)
    occurred_text = occurred_at.astimezone(PERU_TIMEZONE).isoformat()
    with connection() as db:
        movement = apply_inventory_movement(db, payload.product_id, payload.movement_type, payload.quantity, occurred_text, payload.reason, payload.reference, _user["id"])
        db.execute("INSERT INTO audit_log(actor_id,actor_email,actor_role,action,target_type,target_id,summary,created_at) VALUES(?,?,?,?,?,?,?,?)", (_user["id"], _user["email"], _user["role"], "inventory.movement_recorded", "inventory_movement", str(movement["id"]), f"{payload.movement_type}: {payload.quantity} unidades de la referencia {payload.reference}.", now().isoformat()))
        product = db.execute("SELECT name,code,category FROM products WHERE id=?", (payload.product_id,)).fetchone()
    refresh_alerts_after_change()
    return {**movement, "product": product["name"], "code": product["code"], "category": product["category"], "user_name": _user.get("name") or _user.get("email")}


@app.get("/api/alerts")
def list_operational_alerts(status: Literal["active", "resolved", "all"] = "active",
                            alert_type: str | None = None, priority: Literal["alta", "media", "baja"] | None = None,
                            search: str | None = Query(default=None, max_length=120),
                            page: int = Query(default=1, ge=1), page_size: int = Query(default=25, ge=1, le=100),
                            _user=Depends(require_module("alerts"))):
    known_types = set(alerting.ALERT_TYPES)
    if alert_type and alert_type not in known_types:
        raise HTTPException(422, "Tipo de alerta no válido.")
    return alerting.list_alerts(status, alert_type, priority, search, page, page_size)


@app.post("/api/alerts/refresh")
def refresh_operational_alerts(_user=Depends(require_module("alerts"))):
    return alerting.refresh_alerts()


@app.get("/api/replenishment/suggestions")
def replenishment_suggestions(_user=Depends(require_module("replenishment"))):
    return replenishment.suggestions()


@app.get("/api/replenishment/requests")
def list_replenishment_requests(status: Literal["all", "pending_review", "approved", "rejected", "partially_received", "received"] = "all",
                                page: int = Query(default=1, ge=1), page_size: int = Query(default=25, ge=1, le=100),
                                _user=Depends(require_module("replenishment"))):
    return replenishment.list_requests(status, page, page_size)


@app.get("/api/replenishment/requests/{request_id}")
def get_replenishment_request(request_id: int, _user=Depends(require_module("replenishment"))):
    result = replenishment.get_request(request_id)
    if not result:
        raise HTTPException(404, "Solicitud de reposición no encontrada.")
    return result


@app.post("/api/replenishment/requests", status_code=201)
def create_replenishment_request(payload: ReplenishmentRequestIn, user=Depends(require_module("replenishment"))):
    try:
        result = replenishment.create_request(payload.product_id, user["id"], payload.note)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    try:
        alerting.refresh_alerts()
    except Exception:
        logger.exception("Could not refresh alerts after creating replenishment request")
    return result


@app.post("/api/replenishment/requests/{request_id}/decision")
def decide_replenishment_request(request_id: int, payload: ReplenishmentDecisionIn, admin=Depends(require_admin)):
    if "replenishment" not in admin.get("modules", []):
        raise HTTPException(403, "Tu rol no tiene acceso al módulo de reposición.")
    try:
        result = replenishment.decide(request_id, payload.decision, payload.note, admin)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    try:
        alerting.refresh_alerts()
    except Exception:
        logger.exception("Could not refresh alerts after replenishment decision")
    return result


@app.post("/api/replenishment/requests/{request_id}/receipts", status_code=201)
def receive_replenishment_request(request_id: int, payload: ReplenishmentReceiptIn,
                                  user=Depends(require_module("inventory"))):
    if "replenishment" not in user.get("modules", []):
        raise HTTPException(403, "Tu rol necesita los módulos Inventarios y Reposición para registrar una recepción.")
    try:
        result = replenishment.receive(request_id, payload.quantity, payload.reference, user, apply_inventory_movement)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    refresh_alerts_after_change()
    return result


@app.get("/api/dashboard")
def dashboard(from_date: date | None = None, to_date: date | None = None,
              product_id: int | None = Query(default=None, gt=0), category: str | None = None,
              _user=Depends(require_module("dashboard"))):
    today = datetime.now(PERU_TIMEZONE).date()
    end = to_date or today
    start = from_date or (end - timedelta(days=29))
    try:
        return dashboard_data(start, end, product_id, category.strip() if category and category.strip() else None)
    except ValueError as exc:
        raise HTTPException(422, str(exc))


@app.get("/api/forecast/local/status")
def local_forecast_status(_user=Depends(require_module("forecast"))):
    return local_forecasting.status()


@app.post("/api/forecast/local/train")
def train_local_forecast_model(admin=Depends(require_super_admin)):
    try:
        result = local_forecasting.train(requested_by=admin["id"])
    except RuntimeError as exc:
        raise HTTPException(409, str(exc))
    except Exception as exc:
        logger.exception("Local forecast model training failed")
        raise HTTPException(500, f"No se pudo entrenar el modelo local: {exc}")
    write_audit(admin["id"], admin["email"], admin["role"], "forecast.local_model_trained", "local_forecast_model", None,
                f"Entrenamiento local finalizado: {result['summary']['products_trained']} productos entrenados, {result['summary']['products_validated']} validados.")
    alerting.refresh_alerts()
    return result


@app.post("/api/forecast/local/predict")
def local_forecast_prediction(payload: LocalForecastIn, user=Depends(require_module("forecast"))):
    try:
        result = local_forecasting.predict(payload.product_id, payload.horizon, created_by=user["id"])
    except RuntimeError as exc:
        raise HTTPException(409, str(exc))
    except KeyError as exc:
        raise HTTPException(404, str(exc).strip("'"))
    write_audit(user["id"], user["email"], user["role"], "forecast.local_generated", "local_forecast_model", str(payload.product_id),
                f"Pronóstico local generado a {payload.horizon} días ({result['validation_status']}).")
    alerting.refresh_alerts()
    return result
