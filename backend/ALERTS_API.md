# Servicio centralizado de alertas

## Endpoints

- `GET /api/alerts?status=active|resolved|all&alert_type=...&priority=alta|media|baja&search=...&page=1&page_size=25` — requiere permiso del módulo `alerts`. Reconcilia las reglas con PostgreSQL y devuelve filas paginadas, conteos activos y filtros admitidos.
- `POST /api/alerts/refresh` — requiere permiso `alerts`; recalcula condiciones luego de operaciones externas o para actualizar la pantalla.

La reconciliación también se ejecuta al consultar alertas y después de registrar o editar productos, ventas y movimientos de inventario. Cuando una condición continúa activa, se actualiza esa fila; la restricción única de `active_dedupe_key` impide duplicados incluso ante solicitudes concurrentes. Al dejar de cumplirse, pasa a `resolved`, conserva el historial y libera la clave para una futura ocurrencia.

## Reglas implementadas

- **Stock crítico**, **stock bajo** y **exceso de inventario**: comparan existencias con las políticas configuradas por producto. Se comparte `inventory_status` con el catálogo; un máximo configurado puede detectar exceso aun si faltan los otros umbrales.
- **Incremento de demanda**: promedio diario de los últimos 7 días al menos 30% mayor que el promedio de los 28 días anteriores, con 35 días de rango local observado y promedio previo positivo.
- **Riesgo de desabastecimiento**: el stock actual no cubre el promedio diario de los 28 días anteriores multiplicado por el plazo de entrega configurado.
- **Necesidad de reposición**: stock actual igual o menor que ese consumo previsto durante el plazo más el stock de seguridad.

Las dos últimas reglas requieren datos locales de ventas, plazo de entrega y política de seguridad. La necesidad de reposición descuenta solicitudes abiertas, aprobadas y parcialmente recibidas; el riesgo de desabastecimiento se conserva basado en stock físico hasta que se registre una recepción efectiva, porque el sistema aún no dispone de fechas de entrega confirmadas. No se asume stock mínimo, plazo ni ventas faltantes. El sistema declara en la pantalla cuántos productos carecen de umbrales o plazo. Las reglas basadas en ventas presuponen que el registro del sistema está completo durante el período analizado.

La tabla `alerts` conserva producto, tipo, prioridad, descripción, valores disparadores, fechas de generación/actualización/resolución y estado. No se generan alertas en la base hasta que una regla verificable se cumpla.
