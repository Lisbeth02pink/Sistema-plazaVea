"""SQLAlchemy Core metadata for the current persisted application data."""
from sqlalchemy import (
    CheckConstraint, Column, Float, ForeignKey, Index, Integer, MetaData, Numeric, String, Table, Text, UniqueConstraint, func
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
    Column("unit_of_measure", String(30)),
    Column("minimum_stock", Integer),
    Column("safety_stock", Integer),
    Column("maximum_stock", Integer),
    Column("supplier", String(180)),
    Column("lead_time_days", Integer),
    Column("inventory_registered", Integer, nullable=False, server_default="1"),
    Column("source_item_id", String(40)),
    Column("source_url", Text),
    Column("source_captured_at", String(40)),
    Column("stock_simulated", Integer, nullable=False, server_default="0"),
    CheckConstraint("price >= 0", name="ck_products_price_nonnegative"),
    CheckConstraint("stock >= 0", name="ck_products_stock_nonnegative"),
    CheckConstraint("minimum_stock IS NULL OR minimum_stock >= 0", name="ck_products_minimum_stock_nonnegative"),
    CheckConstraint("safety_stock IS NULL OR safety_stock >= 0", name="ck_products_safety_stock_nonnegative"),
    CheckConstraint("maximum_stock IS NULL OR maximum_stock >= 0", name="ck_products_maximum_stock_nonnegative"),
    CheckConstraint("lead_time_days IS NULL OR lead_time_days >= 1", name="ck_products_lead_time_positive"),
    CheckConstraint("safety_stock IS NULL OR minimum_stock IS NULL OR safety_stock <= minimum_stock", name="ck_products_safety_not_above_minimum"),
    CheckConstraint("maximum_stock IS NULL OR minimum_stock IS NULL OR maximum_stock > minimum_stock", name="ck_products_maximum_above_minimum"),
    CheckConstraint("inventory_registered IN (0, 1)", name="ck_products_inventory_registered_boolean"),
    CheckConstraint("stock_simulated IN (0, 1)", name="ck_products_stock_simulated_boolean"),
)
Index("uq_products_source_item_id", products.c.source_item_id, unique=True)

# Raw public Plaza Vea catalog snapshots are kept separate from operational
# products: online availability is not the same as Chiclayo store inventory.
web_catalog_products = Table(
    "web_catalog_products", metadata,
    Column("item_id", String(40), primary_key=True),
    Column("product_id", String(40), nullable=False),
    Column("reference_code", String(80)),
    Column("ean", String(40)),
    Column("name", String(300), nullable=False),
    Column("brand", String(160)),
    Column("category", String(300), nullable=False),
    Column("unit", String(40)),
    Column("price_pen", Numeric(12, 2), nullable=False),
    Column("list_price_pen", Numeric(12, 2)),
    Column("online_available_quantity", Integer),
    Column("online_available", Integer),
    Column("product_url", Text, nullable=False),
    Column("source_category_url", Text, nullable=False),
    Column("source", String(80), nullable=False, server_default="plazavea.com.pe"),
    Column("captured_at", String(40), nullable=False),
    CheckConstraint("price_pen >= 0", name="ck_web_catalog_price_nonnegative"),
    CheckConstraint("list_price_pen IS NULL OR list_price_pen >= 0", name="ck_web_catalog_list_price_nonnegative"),
    CheckConstraint("online_available_quantity IS NULL OR online_available_quantity >= 0", name="ck_web_catalog_available_nonnegative"),
    CheckConstraint("online_available IS NULL OR online_available IN (0, 1)", name="ck_web_catalog_available_boolean"),
)
Index("idx_web_catalog_name", web_catalog_products.c.name)
Index("idx_web_catalog_category", web_catalog_products.c.category)
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
Index("idx_products_category", products.c.category)
Index("idx_products_stock", products.c.stock)

sales = Table(
    "sales", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("occurred_at", String(40), nullable=False),
    Column("reference", String(80), nullable=False, unique=True),
    Column("subtotal", Numeric(12, 2), nullable=False),
    Column("total", Numeric(12, 2), nullable=False),
    Column("created_by", Integer, ForeignKey("users.id", ondelete="SET NULL")),
    CheckConstraint("subtotal >= 0", name="ck_sales_subtotal_nonnegative"),
    CheckConstraint("total >= 0", name="ck_sales_total_nonnegative"),
)
sale_items = Table(
    "sale_items", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("sale_id", Integer, ForeignKey("sales.id", ondelete="CASCADE"), nullable=False),
    Column("product_id", Integer, ForeignKey("products.id", ondelete="RESTRICT"), nullable=False),
    Column("quantity", Integer, nullable=False),
    Column("unit_price", Numeric(12, 2), nullable=False),
    Column("subtotal", Numeric(12, 2), nullable=False),
    CheckConstraint("quantity > 0", name="ck_sale_items_quantity_positive"),
    CheckConstraint("unit_price >= 0", name="ck_sale_items_price_nonnegative"),
    CheckConstraint("subtotal >= 0", name="ck_sale_items_subtotal_nonnegative"),
)
inventory_movements = Table(
    "inventory_movements", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("product_id", Integer, ForeignKey("products.id", ondelete="RESTRICT"), nullable=False),
    Column("movement_type", String(30), nullable=False),
    Column("quantity", Integer, nullable=False),
    Column("stock_before", Integer, nullable=False),
    Column("stock_after", Integer, nullable=False),
    Column("occurred_at", String(40), nullable=False),
    Column("reason", String(240), nullable=False),
    Column("reference", String(80), nullable=False),
    Column("user_id", Integer, ForeignKey("users.id", ondelete="SET NULL")),
    CheckConstraint("quantity > 0", name="ck_inventory_movements_quantity_positive"),
    CheckConstraint("stock_before >= 0", name="ck_inventory_movements_stock_before_nonnegative"),
    CheckConstraint("stock_after >= 0", name="ck_inventory_movements_stock_after_nonnegative"),
    CheckConstraint("movement_type IN ('Entrada por recepción','Salida por venta','Ajuste positivo','Ajuste negativo','Devolución','Merma')", name="ck_inventory_movements_type"),
    CheckConstraint("((movement_type IN ('Salida por venta','Ajuste negativo','Merma') AND stock_before - quantity = stock_after) OR (movement_type IN ('Entrada por recepción','Ajuste positivo','Devolución') AND stock_before + quantity = stock_after))", name="ck_inventory_movements_balance"),
)
Index("idx_sales_occurred_at", sales.c.occurred_at)
Index("idx_sale_items_sale_id", sale_items.c.sale_id)
Index("idx_sale_items_product_id", sale_items.c.product_id)
Index("idx_inventory_movements_product_time", inventory_movements.c.product_id, inventory_movements.c.occurred_at)
Index("idx_inventory_movements_reference", inventory_movements.c.reference)
Index("idx_inventory_movements_occurred_at", inventory_movements.c.occurred_at)

forecast_training_runs = Table(
    "forecast_training_runs", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("started_at", String(40), nullable=False),
    Column("finished_at", String(40)),
    Column("status", String(40), nullable=False),
    Column("source", String(100), nullable=False),
    Column("message", Text, nullable=False, server_default=""),
    Column("products_evaluated", Integer, nullable=False, server_default="0"),
    Column("products_trained", Integer, nullable=False, server_default="0"),
    Column("products_validated", Integer, nullable=False, server_default="0"),
    Column("requested_by", Integer, ForeignKey("users.id", ondelete="SET NULL")),
    CheckConstraint("products_evaluated >= 0 AND products_trained >= 0 AND products_validated >= 0", name="ck_forecast_training_counts_nonnegative"),
)
local_forecast_models = Table(
    "local_forecast_models", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("training_run_id", Integer, ForeignKey("forecast_training_runs.id", ondelete="CASCADE"), nullable=False),
    Column("product_id", Integer, ForeignKey("products.id", ondelete="RESTRICT"), nullable=False),
    Column("model_version", String(40), nullable=False),
    Column("model_name", String(100), nullable=False),
    Column("trained_at", String(40), nullable=False),
    Column("source_updated_at", String(40), nullable=False),
    Column("history_start", String(10), nullable=False),
    Column("history_end", String(10), nullable=False),
    Column("history_days", Integer, nullable=False),
    Column("positive_days", Integer, nullable=False),
    Column("stockout_days", Integer, nullable=False),
    Column("validation_days", Integer, nullable=False),
    Column("mae", Float),
    Column("rmse", Float),
    Column("wape_percent", Float),
    Column("bias_percent", Float),
    Column("baseline_wape_percent", Float),
    Column("beats_baseline", Integer, nullable=False),
    Column("validated_operational", Integer, nullable=False),
    Column("artifact_key", String(120), nullable=False),
    Column("status", String(40), nullable=False),
    UniqueConstraint("training_run_id", "product_id", name="uq_local_model_run_product"),
    CheckConstraint("history_days >= 0 AND positive_days >= 0 AND stockout_days >= 0 AND validation_days >= 0", name="ck_local_model_counts_nonnegative"),
    CheckConstraint("beats_baseline IN (0,1) AND validated_operational IN (0,1)", name="ck_local_model_flags_boolean"),
)
demand_forecasts = Table(
    "demand_forecasts", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("model_id", Integer, ForeignKey("local_forecast_models.id", ondelete="RESTRICT"), nullable=False),
    Column("product_id", Integer, ForeignKey("products.id", ondelete="RESTRICT"), nullable=False),
    Column("generated_at", String(40), nullable=False),
    Column("forecast_date", String(10), nullable=False),
    Column("horizon_days", Integer, nullable=False),
    Column("estimated_units", Numeric(12, 3), nullable=False),
    Column("model_version", String(40), nullable=False),
    Column("validation_status", String(40), nullable=False),
    Column("created_by", Integer, ForeignKey("users.id", ondelete="SET NULL")),
    UniqueConstraint("model_id", "generated_at", "forecast_date", name="uq_demand_forecast_generated_day"),
    CheckConstraint("horizon_days IN (7,14,30)", name="ck_demand_forecast_horizon_allowed"),
    CheckConstraint("estimated_units >= 0", name="ck_demand_forecast_units_nonnegative"),
)
Index("idx_forecast_training_runs_started", forecast_training_runs.c.started_at)
Index("idx_local_forecast_models_product", local_forecast_models.c.product_id, local_forecast_models.c.trained_at)
Index("idx_local_forecast_models_run", local_forecast_models.c.training_run_id)
Index("idx_demand_forecasts_product_date", demand_forecasts.c.product_id, demand_forecasts.c.forecast_date)
Index("idx_demand_forecasts_generated", demand_forecasts.c.generated_at)

alerts = Table(
    "alerts", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("product_id", Integer, ForeignKey("products.id", ondelete="RESTRICT"), nullable=False),
    Column("alert_type", String(40), nullable=False),
    Column("priority", String(16), nullable=False),
    Column("description", Text, nullable=False),
    Column("trigger_value", Text, nullable=False),
    Column("generated_at", String(40), nullable=False),
    Column("updated_at", String(40), nullable=False),
    Column("status", String(16), nullable=False, server_default="active"),
    Column("resolved_at", String(40)),
    Column("resolution_reason", Text),
    Column("active_dedupe_key", String(100), unique=True),
    CheckConstraint("priority IN ('alta','media','baja')", name="ck_alerts_priority_allowed"),
    CheckConstraint("status IN ('active','resolved')", name="ck_alerts_status_allowed"),
)
Index("idx_alerts_status_generated", alerts.c.status, alerts.c.generated_at)
Index("idx_alerts_product_status", alerts.c.product_id, alerts.c.status)
Index("idx_alerts_type_status", alerts.c.alert_type, alerts.c.status)

replenishment_requests = Table(
    "replenishment_requests", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("reference", String(40), nullable=False, unique=True),
    Column("product_id", Integer, ForeignKey("products.id", ondelete="RESTRICT"), nullable=False),
    Column("suggested_quantity", Integer, nullable=False),
    Column("received_quantity", Integer, nullable=False, server_default="0"),
    Column("stock_at_request", Integer, nullable=False),
    Column("incoming_at_request", Integer, nullable=False),
    Column("demand_during_lead", Float, nullable=False),
    Column("reorder_point", Integer, nullable=False),
    Column("demand_source", String(40), nullable=False),
    Column("lead_time_days", Integer, nullable=False),
    Column("priority", String(16), nullable=False),
    Column("status", String(24), nullable=False, server_default="pending_review"),
    Column("request_note", Text, nullable=False, server_default=""),
    Column("created_by", Integer, ForeignKey("users.id", ondelete="SET NULL")),
    Column("created_at", String(40), nullable=False),
    Column("decided_by", Integer, ForeignKey("users.id", ondelete="SET NULL")),
    Column("decided_at", String(40)),
    Column("decision_note", Text),
    Column("open_product_key", String(24), unique=True),
    CheckConstraint("suggested_quantity > 0", name="ck_replenishment_quantity_positive"),
    CheckConstraint("received_quantity >= 0 AND received_quantity <= suggested_quantity", name="ck_replenishment_received_not_over_ordered"),
    CheckConstraint("stock_at_request >= 0 AND incoming_at_request >= 0 AND demand_during_lead >= 0 AND reorder_point >= 0", name="ck_replenishment_snapshot_nonnegative"),
    CheckConstraint("lead_time_days > 0", name="ck_replenishment_lead_time_positive"),
    CheckConstraint("priority IN ('alta','media')", name="ck_replenishment_priority_allowed"),
    CheckConstraint("status IN ('pending_review','approved','rejected','partially_received','received')", name="ck_replenishment_status_allowed"),
)
replenishment_receipts = Table(
    "replenishment_receipts", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("request_id", Integer, ForeignKey("replenishment_requests.id", ondelete="RESTRICT"), nullable=False),
    Column("inventory_movement_id", Integer, ForeignKey("inventory_movements.id", ondelete="RESTRICT"), nullable=False, unique=True),
    Column("quantity", Integer, nullable=False),
    Column("reference", String(80), nullable=False, unique=True),
    Column("occurred_at", String(40), nullable=False),
    Column("received_by", Integer, ForeignKey("users.id", ondelete="SET NULL")),
    CheckConstraint("quantity > 0", name="ck_replenishment_receipt_quantity_positive"),
)
Index("idx_replenishment_product_status", replenishment_requests.c.product_id, replenishment_requests.c.status)
Index("idx_replenishment_status_created", replenishment_requests.c.status, replenishment_requests.c.created_at)
Index("idx_replenishment_receipts_request", replenishment_receipts.c.request_id, replenishment_receipts.c.occurred_at)
