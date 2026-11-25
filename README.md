# Sistema BI e IA · Plaza Vea Chiclayo

El sistema usa **machine learning** para pronosticar la demanda de productos en Plaza Vea Chiclayo. Entrena el algoritmo `HistGradientBoostingRegressor` de scikit-learn con el historial de ventas registrado en el sistema y contrasta los pronósticos con el stock para orientar la reposición. El panel de **Business Intelligence (BI)** presenta indicadores de ventas, inventario, alertas y demanda proyectada en la aplicación web con React y Recharts.

## Módulos

- **Panel de control:** indicadores de ventas e inventario.
- **Productos, ventas e inventario:** catálogo, historial de ventas y movimientos de stock (kardex).
- **Pronósticos, alertas y reposición:** estimaciones de demanda, alertas operativas y gestión del reabastecimiento.
- **Acceso y seguridad:** inicio de sesión, usuarios, roles y auditoría.

## Ejecución local

Necesitas Python y Node.js con npm. El backend usa FastAPI y admite SQLite para desarrollo local. La interfaz usa React y Vite.

En una terminal PowerShell, desde la raíz del proyecto, inicia el backend:

```powershell
cd backend
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe create_admin.py
.\.venv\Scripts\python.exe -m uvicorn main:app --port 8000
```

Ejecuta `create_admin.py` solo para crear la cuenta inicial. En otra terminal, desde la raíz del proyecto, inicia la interfaz:

```powershell
npm ci
npm run dev
```
