# Kardex de inventario

Las rutas requieren sesión y permiso del módulo `inventory`. Cada registro es inmutable desde la API; el saldo se actualiza y el movimiento se inserta en una sola transacción.

## Endpoints

- `GET /api/inventory/products`: productos y saldos actuales para el módulo de inventarios.
- `GET /api/inventory/movements`: kardex paginado. Filtros opcionales: `from_date`, `to_date`, `product_id`, `movement_type`, `page` y `page_size`.
- `POST /api/inventory/movements`: registra una entrada, ajuste positivo, ajuste negativo, devolución o merma. Las salidas por venta solo se crean desde `POST /api/sales`.

## Ejemplo de entrada

```json
{
  "product_id": 1,
  "movement_type": "Entrada por recepción",
  "quantity": 24,
  "reason": "Recepción de mercadería según guía",
  "reference": "GR-004218",
  "occurred_at": "2026-11-24T10:35:00-05:00"
}
```

Entradas por recepción, ajustes positivos y devoluciones aumentan el saldo. Ventas, mermas y ajustes negativos lo reducen y se rechazan si exceden el stock. Las existencias iniciales anteriores al kardex se conservan como movimientos de apertura; en altas nuevas, el stock inicial también genera un movimiento.

Las ediciones de producto ya no pueden cambiar el stock. Las ventas y movimientos guardan usuario, fecha, referencia, motivo y saldos anterior/posterior, y generan auditoría dentro de la transacción.
