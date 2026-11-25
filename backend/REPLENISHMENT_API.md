# Sugerencias y flujo de reposición

## Endpoints

- `GET /api/replenishment/suggestions` — requiere permiso `replenishment`. Evalúa productos, política, demanda, entradas pendientes y cantidad necesaria.
- `GET /api/replenishment/requests?status=all|pending_review|approved|rejected|partially_received|received&page=1&page_size=25` — consulta e historial de solicitudes.
- `GET /api/replenishment/requests/{id}` — detalle con decisiones y recepciones.
- `POST /api/replenishment/requests` — requiere permiso `replenishment`; cuerpo `{"product_id": 123, "note": ""}`. Recalcula al crear y guarda la cantidad calculada. No acepta que el cliente imponga una cantidad.
- `POST /api/replenishment/requests/{id}/decision` — requiere `can_manage_users` y módulo `replenishment`; cuerpo `{"decision":"approve|reject","note":"..."}`. La aprobación no cambia existencias.
- `POST /api/replenishment/requests/{id}/receipts` — requiere módulos `inventory` y `replenishment`; cuerpo `{"quantity": 8, "reference":"GUIA-123"}`. Acepta recepciones parciales y actualiza existencias y kardex en una transacción.

## Regla de cálculo

`Punto de reposición = demanda esperada durante el plazo de entrega + stock de seguridad`.

Se usa primero un pronóstico local validado y vigente si hay cobertura diaria completa del plazo configurado. En caso contrario, el sistema utiliza un promedio de demanda neta local de 28 días, solo si hay 28 días observados y al menos 5 días con venta neta. Las devoluciones se restan. Sin historial suficiente, stock de seguridad o plazo de entrega, el producto se muestra con motivo y no se crea una solicitud.

`Cantidad sugerida = max(0, punto de reposición − stock disponible − entradas pendientes)`.

Las entradas pendientes incluyen solicitudes en revisión, aprobadas o parcialmente recibidas, descontando las cantidades ya recibidas. Se impide mantener más de una solicitud abierta por producto. La aprobación solo registra la decisión. La recepción efectiva incrementa el stock a través de `inventory_movements` (`Entrada por recepción`), conserva el comprobante y puede registrarse en varias entregas. Las actualizaciones del estado de recepción y del inventario son transaccionales.

La base operativa actual no tiene ventas locales y los seis productos aún requieren configurar la política de stock y el plazo de entrega; por eso no habrá recomendaciones numéricas ni solicitudes hasta contar con esos datos.
