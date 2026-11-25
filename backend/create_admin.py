"""Create an initial system user without storing a plaintext password."""
from getpass import getpass
import re

from main import connection, make_password_hash, now

email = input("Correo del usuario autorizado: ").strip().lower()
name = input("Nombre para mostrar: ").strip()
if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email) or not name:
    raise SystemExit("Correo o nombre no válido.")
password = getpass("Contraseña (mínimo 8 caracteres): ")
confirm = getpass("Confirma la contraseña: ")
if len(password) < 8 or password != confirm:
    raise SystemExit("Las contraseñas deben coincidir y tener al menos 8 caracteres.")
try:
    with connection() as db:
        db.execute("CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT NOT NULL UNIQUE COLLATE NOCASE, display_name TEXT NOT NULL, password_hash TEXT NOT NULL, is_active INTEGER NOT NULL DEFAULT 1, role TEXT NOT NULL DEFAULT 'Personal de operaciones', created_at TEXT NOT NULL, last_login_at TEXT)")
        columns = {row[1] for row in db.execute("PRAGMA table_info(users)")}
        if "role" not in columns:
            db.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'Personal de operaciones'")
        if "last_login_at" not in columns:
            db.execute("ALTER TABLE users ADD COLUMN last_login_at TEXT")
        db.execute("CREATE TABLE IF NOT EXISTS roles (name TEXT PRIMARY KEY, description TEXT NOT NULL, can_manage_users INTEGER NOT NULL DEFAULT 0)")
        role_columns = {row[1] for row in db.execute("PRAGMA table_info(roles)")}
        if "can_manage_users" not in role_columns:
            db.execute("ALTER TABLE roles ADD COLUMN can_manage_users INTEGER NOT NULL DEFAULT 0")
        db.executemany("INSERT OR IGNORE INTO roles(name,description,can_manage_users) VALUES(?,?,?)", [
            ("Administrador", "Administración de usuarios y acceso al sistema", 1),
            ("Responsable de abastecimiento", "Gestión de inventarios y reposición", 0),
            ("Personal de operaciones", "Operación de tienda", 0),
        ])
        db.execute("UPDATE roles SET can_manage_users=1 WHERE name='Administrador'")
        db.execute("CREATE TABLE IF NOT EXISTS sessions (token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, expires_at TEXT NOT NULL, created_at TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS password_resets (token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, expires_at TEXT NOT NULL, used_at TEXT)")
        db.execute("INSERT INTO users(email,display_name,password_hash,role,created_at) VALUES(?,?,?,'Administrador',?)", (email, name, make_password_hash(password), now().isoformat()))
except Exception as exc:
    raise SystemExit(f"No se pudo crear el usuario: {exc}") from exc
print("Usuario creado. El sistema no guarda la contraseña en texto legible.")
