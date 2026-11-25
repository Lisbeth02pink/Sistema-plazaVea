# API del dashboard BI

`GET /api/dashboard?from_date=YYYY-MM-DD&to_date=YYYY-MM-DD&category=Abarrotes&product_id=1`

Requiere el módulo `dashboard` y una sesión iniciada. El período predeterminado cubre los últimos 30 días; se acepta un máximo de 366 días. Categoría y producto son filtros opcionales y se aplican a ventas, inventario, alertas, solicitudes y productos destacados.

La respuesta contiene:

- `summary`: ventas reales en soles, unidades, transacciones, ticket promedio, stock, conteos de estado, alertas activas y reposiciones abiertas.
- `categories` y `product_options`: opciones para los filtros, incluso cuando el usuario solo tiene permiso para consultar el dashboard.
- `sales_daily`: totales por día obtenidos del detalle de ventas. Si no hubo ventas, se devuelve una lista vacía.
- `top_products`: productos ordenados por unidades vendidas durante el período.
- `forecast`: demanda diaria únicamente de modelos locales validados, con pronóstico vigente y generado para los próximos 30 días. Es una estimación aparte de las ventas.
- `recent_alerts`: hasta cinco alertas activas que respetan los filtros.
- `data_quality`: disponibilidad de ventas, pronóstico e inventario para mostrar estados vacíos.

El dashboard no usa datos de demostración ni inserta ceros para una serie sin ventas. Los días sin transacciones se completan con cero solamente cuando sí existe al menos una venta dentro del período; en ese caso, el cero representa que no se registraron ventas ese día.
