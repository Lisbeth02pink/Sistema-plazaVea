# Registro de ventas

Las operaciones de venta requieren una sesión activa y el módulo `sales` habilitado para el rol.

## Endpoints

- `GET /api/sales/products`: catálogo vendible con precio y existencias actuales.
- `POST /api/sales`: registra comprobante, detalle, salida de stock, movimiento de kardex y auditoría en una transacción.
- `GET /api/sales`: historial paginado. Filtros opcionales: `from_date`, `to_date`, `product_id`, `category`, `page` y `page_size`.
- `GET /api/sales/{sale_id}`: comprobante y líneas de detalle.
- `GET /api/sales/stats`: total vendido, unidades, número de comprobantes, ticket promedio y totales diarios. Acepta `from_date`, `to_date`, `product_id` y `category`.

## Ejemplo de registro

```json
{
  "reference": "B001-000123",
  "occurred_at": "2026-11-24T10:35:00-05:00",
  "items": [
    { "product_id": 1, "quantity": 2, "unit_price": 4.20 }
  ]
}
```

Si no se envía `unit_price`, se usa el precio actual del catálogo. Cada producto solo puede aparecer una vez por comprobante; las cantidades deben ser enteros positivos y el stock se valida en el mismo `UPDATE` que lo descuenta. El número de comprobante es único para evitar duplicar operaciones. Un fallo en cualquier línea revierte toda la transacción.

Los importes se expresan en soles. La fecha se guarda con la zona horaria de Perú. Los movimientos generados en esta fase son salidas de tipo `Venta`; los otros tipos de movimiento se incorporarán en la Fase 4.
