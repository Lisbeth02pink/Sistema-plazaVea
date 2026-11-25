# Sistema BI e IA · Plaza Vea Chiclayo

El sistema apoya la toma de decisiones sobre inventario y reposición en Plaza Vea Chiclayo. Reúne información de ventas y stock, estima la demanda y genera alertas para ayudar a decidir qué productos reponer y cuándo, con el fin de reducir los quiebres de stock y el exceso de inventario.

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
