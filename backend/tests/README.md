# Pruebas de la fase 11

Ejecuta desde `backend/`:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

La suite usa `unittest` y solo dependencias que ya requiere el backend. La prueba de flujo inicia FastAPI con Uvicorn en un puerto temporal y una base SQLite dentro de una carpeta temporal. Sobrescribe `DATABASE_URL` antes de importar el backend, reduce los umbrales de historial únicamente en ese proceso de prueba y elimina la base al terminar. No lee ni modifica PostgreSQL, SQLite de la aplicación, artefactos del modelo ni credenciales reales.

Incluye pruebas unitarias de estados y políticas de inventario, una prueba de autorización por módulo y un recorrido HTTP integral: producto con stock inicial, venta, kardex, historial, dashboard, alerta, sugerencia, aprobación sin cambio de stock, recepción efectiva, ajuste de inventario y verificación de actor y eventos en auditoría.

Los registros de producto y venta creados por esta prueba son datos sintéticos de QA, viven únicamente en la base temporal y no deben cargarse en el sistema de la tienda.
