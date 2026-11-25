# PostgreSQL: fase 1

El backend usa SQLAlchemy para soportar PostgreSQL mediante Psycopg 3. Si no se
define `DATABASE_URL`, conserva SQLite como modo local de compatibilidad. La
base SQLite no se elimina ni se modifica al configurar PostgreSQL.

## Configuración

1. Crea una base vacía y un usuario PostgreSQL con permisos de conexión, DDL y
   escritura sobre esa base.
2. Copia `.env.example` como `.env` dentro de `backend/` y configura una URL:

   ```text
   DATABASE_URL=postgresql+psycopg://postgres:TU_CONTRASENA@localhost:5434/sistema_Plazavea
   ```

   Si la contraseña contiene caracteres reservados de URL, codifícala según
   percent-encoding. No guardes `.env` en el repositorio.

3. Instala las dependencias del entorno virtual:

   ```powershell
   .\backend\.venv\Scripts\python.exe -m pip install -r .\backend\requirements.txt
   ```

4. Ejecuta la migración de datos una sola vez:

   ```powershell
   .\backend\.venv\Scripts\python.exe -m backend.migrate_sqlite_to_postgres
   ```

El comando requiere que el destino esté vacío. Aplica primero la migración
Alembic, copia las 12 tablas de aplicación desde SQLite en una transacción y
compara conteos, hashes de contenido y secuencias de IDs antes de terminar.
Primero crea una copia SQLite consistente y verifica su integridad. Ante errores,
revierte la carga y deja intacto el origen. No vuelve a copiar si encuentra
registros en el destino.

## Respaldo y reversión

Antes del cambio, se conserva una copia SQLite verificada en `backups/`. La
funcionalidad de respaldos administrativos usa `pg_dump` al operar en
PostgreSQL. Para volver temporalmente al archivo previo, quita `DATABASE_URL`
del `.env`; el backend vuelve a abrir SQLite. No uses ambos motores como bases
de escritura simultáneas.

## Esquema de esta fase

Alembic crea o adopta las tablas existentes: `products`, `users`, `roles`,
`role_modules`, `deleted_roles`, `sessions`, `password_resets`,
`login_attempts`, `login_locks`, `security_settings`, `audit_log` y
`security_reviews`. Esta fase conserva las entidades actuales; las tablas
operativas de ventas, kardex, alertas y reposición pertenecen a fases
posteriores.
