"""SQLAlchemy Core metadata for the current persisted application data."""
from sqlalchemy import (
    CheckConstraint, Column, ForeignKey, Index, Integer, MetaData, Numeric, String, Table, Text, UniqueConstraint, func
)

metadata = MetaData()

products = Table(
    "products", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("code", String(40), nullable=False, unique=True),
    Column("name", String(180), nullable=False),
    Column("category", String(80), nullable=False),
    Column("price", Numeric(12, 2), nullable=False),
    Column("stock", Integer, nullable=False),
    CheckConstraint("price >= 0", name="ck_products_price_nonnegative"),
    CheckConstraint("stock >= 0", name="ck_products_stock_nonnegative"),
)
users = Table(
    "users", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("email", String(254), nullable=False),
    Column("display_name", String(180), nullable=False),
    Column("password_hash", Text, nullable=False),
    Column("is_active", Integer, nullable=False, server_default="1"),
    Column("created_at", Text, nullable=False),
    Column("role", String(80), ForeignKey("roles.name", onupdate="CASCADE"), nullable=False, server_default="Personal de operaciones"),
    Column("last_login_at", Text),
    UniqueConstraint("email", name="uq_users_email"),
    CheckConstraint("is_active IN (0, 1)", name="ck_users_active_boolean"),
)
Index("uq_users_email_lower", func.lower(users.c.email), unique=True)
roles = Table(
    "roles", metadata,
    Column("name", String(80), primary_key=True),
    Column("description", String(180), nullable=False),
    Column("can_manage_users", Integer, nullable=False, server_default="0"),
)
Index("uq_roles_name_lower", func.lower(roles.c.name), unique=True)
deleted_roles = Table(
    "deleted_roles", metadata,
    Column("name", String(80), primary_key=True),
    Column("deleted_at", Text, nullable=False),
)
role_modules = Table(
    "role_modules", metadata,
    Column("role_name", String(80), ForeignKey("roles.name", ondelete="CASCADE"), primary_key=True),
    Column("module_key", String(40), primary_key=True),
)
sessions = Table(
    "sessions", metadata,
    Column("token_hash", String(64), primary_key=True),
    Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("expires_at", Text, nullable=False),
    Column("created_at", Text, nullable=False),
)
password_resets = Table(
    "password_resets", metadata,
    Column("token_hash", String(64), primary_key=True),
    Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("expires_at", Text, nullable=False),
    Column("used_at", Text),
)
login_attempts = Table(
    "login_attempts", metadata,
    Column("fingerprint", String(64), nullable=False),
    Column("attempted_at", Text, nullable=False),
)
login_locks = Table(
    "login_locks", metadata,
    Column("fingerprint", String(64), primary_key=True),
    Column("locked_until", Text, nullable=False),
)
security_settings = Table(
    "security_settings", metadata,
    Column("id", Integer, primary_key=True),
    Column("session_hours", Integer, nullable=False, server_default="8"),
    Column("failed_attempts", Integer, nullable=False, server_default="10"),
    Column("lockout_minutes", Integer, nullable=False, server_default="15"),
    Column("updated_at", Text, nullable=False),
    Column("updated_by", String(254)),
    CheckConstraint("session_hours BETWEEN 1 AND 24", name="ck_security_session_hours"),
    CheckConstraint("failed_attempts BETWEEN 3 AND 20", name="ck_security_failed_attempts"),
    CheckConstraint("lockout_minutes BETWEEN 1 AND 60", name="ck_security_lockout_minutes"),
)
audit_log = Table(
    "audit_log", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("actor_id", Integer),
    Column("actor_email", String(254)),
    Column("actor_role", String(80)),
    Column("action", String(120), nullable=False),
    Column("target_type", String(80)),
    Column("target_id", String(120)),
    Column("summary", Text, nullable=False, server_default=""),
    Column("created_at", Text, nullable=False),
)
security_reviews = Table(
    "security_reviews", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("reviewer_id", Integer),
    Column("reviewer", String(180), nullable=False),
    Column("reviewer_email", String(254), nullable=False),
    Column("reviewer_role", String(80), nullable=False),
    Column("notes", Text, nullable=False, server_default=""),
    Column("reviewed_at", Text, nullable=False),
)

Index("idx_audit_log_created_at", audit_log.c.created_at.desc())
Index("idx_sessions_user_id", sessions.c.user_id)
Index("idx_password_resets_user_id", password_resets.c.user_id)
Index("idx_login_attempts_fingerprint_time", login_attempts.c.fingerprint, login_attempts.c.attempted_at)
