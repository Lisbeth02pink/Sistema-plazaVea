# Sistema BI & IA — Plaza Vea Chiclayo

Sistema local BI & IA para inicio de sesión, resumen ejecutivo, gestión de productos y administración local de usuarios. El módulo de predicción entrena un modelo con un conjunto público de referencia, separado de los datos de Plaza Vea.

## Requisitos

- Node.js 20 o posterior y npm
- Python 3.10 o posterior

## Instalar y ejecutar

En una terminal, desde `backend`, crea el entorno virtual si aún no existe e instala dependencias. Si ya existe `.venv`, omite los dos primeros comandos:

```powershell
cd backend
py -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
.venv\Scripts\python.exe -m uvicorn main:app --reload --port 8000
```

En otra terminal, desde la raíz del proyecto:

```powershell
npm install
npm run dev
```

Abre la dirección local que informa Vite (habitualmente `http://localhost:5173`). La API y la documentación interactiva están en `http://127.0.0.1:8000` y `/docs`.

## Crear una cuenta autorizada

No hay registro público, usuarios iniciales ni contraseña de demostración. En otra terminal, desde `backend`, crea una cuenta de forma interactiva:

```powershell
.venv\Scripts\python.exe create_admin.py
```

El script solicita correo, nombre y una contraseña de al menos 8 caracteres. La contraseña se introduce de forma oculta y se guarda como hash scrypt. La cuenta creada recibe el rol Administrador.

## Gestión de usuarios

Desde **Usuarios y roles**, una cuenta con permiso para ese módulo puede crear, editar y eliminar usuarios locales, cambiar contraseñas, asignar roles, y suspender o reactivar cuentas. También puede crear, editar y eliminar roles, y configurar sus módulos. Los cambios se guardan en SQLite. No puedes eliminarte, cambiar tu propio rol ni suspender tu propia cuenta; el rol Administrador no se puede renombrar, borrar ni editar en sus módulos. Un rol con usuarios asignados no se elimina hasta que sus cuentas sean reasignadas. Las contraseñas se guardan con scrypt y se exige un mínimo de 8 caracteres.

En **Roles y permisos**, el Administrador conserva acceso a todos los módulos. Puede conceder o retirar acceso a Dashboard, Productos, Ventas históricas, Inventarios, Predicción con IA, Alertas, Reposición, Usuarios y roles, Auditoría y Seguridad para los otros roles. El acceso a Dashboard y Productos también se comprueba en la API; los demás módulos todavía no tienen operaciones de servidor que proteger. La pantalla **Seguridad** sigue mostrando controles conceptuales; no guarda configuraciones ni certifica cumplimiento.

## Recuperación de contraseña por correo

La recuperación usa SMTP opcional. Copia `backend/.env.example` a `backend/.env` y configura `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM` y `APP_BASE_URL`. Si SMTP no está configurado, el formulario explica que la recuperación está temporalmente deshabilitada y dirige a contactar al administrador. Con SMTP activo, el servidor envía un enlace de un solo uso con vencimiento de 45 minutos; el endpoint responde con un mensaje genérico para no revelar si una cuenta existe.

El correo y la contraseña SMTP permanecen en el backend. No uses credenciales reales en archivos versionados. En producción configura HTTPS y `COOKIE_SECURE=true`; el backend marca la cookie de sesión como `HttpOnly` y `SameSite=Lax`.

## Seguridad y alcance

- El backend valida contraseñas scrypt y entrega una cookie aleatoria, almacenada en SQLite solo como hash, con vencimiento de ocho horas.
- Limita intentos fallidos a 10 por combinación de dirección IP y correo cada 15 minutos; configura además límites en el proxy público antes de desplegar.
- El frontend consulta la sesión al cargar, protege las páginas privadas y permite cerrar sesión.
- Las rutas de productos y dashboard de la API requieren sesión. El endpoint de salud y los endpoints de login/recuperación son públicos.
- No hay servicio SMTP configurado por defecto; la recuperación requiere que completes esa configuración.
- La API de administración de cuentas y roles requiere el permiso del módulo Usuarios y roles; el rol Administrador lo tiene siempre.
- La pantalla de seguridad muestra controles conceptuales; la gestión de esos parámetros todavía no está conectada.
- Antes de publicar, configura HTTPS, límites de intentos en el proxy, política de contraseñas, copias protegidas de SQLite y procedimientos de rotación de sesiones. SQLite local no es una solución multiinstancia.

SQLite crea `backend/plaza_vea.db` y carga seis productos ficticios la primera vez. No se crea ninguna cuenta por defecto.

## Modelo de predicción de demanda

El módulo Predicción con IA usa el conjunto scanner `dataRSM.csv`, agregado por mes, con registros de arroz, azúcar y leche de supermercados de Polonia, descripciones en polaco, cantidades y precios en PLN. La interfaz traduce las descripciones a nombres comunes en español. El conjunto no contiene datos de Plaza Vea ni ventas de aceite. El archivo y el artefacto entrenado no se versionan en Git.

La cuenta con rol Super Administrador puede entrenar o reentrenar el modelo desde esa pantalla. Se seleccionan hasta 10 productos por categoría y se comparan HistGradientBoosting, RandomForest y ExtraTrees con `TimeSeriesSplit` en tres bloques, usando el último mes como referencia. Los dos últimos meses quedan separados como prueba final. Se informan MAE, RMSE, WAPE y F1. El F1 clasifica meses de demanda alta usando el percentil 75 de ventas mensuales positivas del producto en entrenamiento. La proyección mensual se prorratea al horizonte de 7, 14 o 30 días de la pantalla. El artefacto queda en `backend/models/retail_demand.joblib` y los pronósticos se calculan en el backend.

Los precios de referencia se convierten a soles con `RETAIL_PLN_PEN_RATE`, cuyo valor inicial es `0.95`. Modifica esa variable en `backend/.env` si deseas usar otra tasa. Esta conversión es configurable y no representa una serie histórica diaria. La fuente está documentada en `backend/data/README.md`.
