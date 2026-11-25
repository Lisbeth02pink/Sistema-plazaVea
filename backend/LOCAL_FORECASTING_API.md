# Pronóstico local de demanda

El modelo operativo se alimenta exclusivamente de `sales` y `sale_items` de la base configurada en `DATABASE_URL` (PostgreSQL para la instancia local actual). El conjunto público de Polonia permanece en `/api/forecast/*` como referencia experimental. No se mezcla con el flujo local.

## Rutas

- `GET /api/forecast/local/status` — requiere permiso del módulo `forecast`. Devuelve el estado por producto, historial disponible, estado de entrenamiento, métricas temporales y controles de calidad.
- `POST /api/forecast/local/train` — requiere Super Administrador. Evalúa cada producto que alcanza los mínimos; guarda corrida, métricas y versión en PostgreSQL y artefacto en `backend/models/local_demand.joblib`.
- `POST /api/forecast/local/predict` — requiere permiso `forecast`. Cuerpo: `{"product_id": 1, "horizon": 7}`; horizontes permitidos: 7, 14 o 30 días. Solo emite para un producto con modelo validado y vigente, y persiste cada día pronosticado en `demand_forecasts`.

## Estado y control de calidad inicial

Las etiquetas de estado son `no_local_sales`, `insufficient_history`, `ready_to_train`, `trained_local`, `validated_operational` y `stale`. Los criterios iniciales configurables requieren 120 días de historial, 60 filas de entrenamiento, 20 días de venta neta, 14 días de validación temporal (al menos 10 días válidos), WAPE máximo de 40%, sesgo absoluto máximo de 25% y superar el baseline estacional semanal. Estos umbrales son gates iniciales explícitos, no una garantía universal de precisión.

Variables opcionales: `LOCAL_FORECAST_MIN_HISTORY_DAYS`, `LOCAL_FORECAST_MIN_TRAIN_ROWS`, `LOCAL_FORECAST_MIN_POSITIVE_DAYS`, `LOCAL_FORECAST_MIN_VALIDATION_DAYS`, `LOCAL_FORECAST_VALIDATION_DAYS`, `LOCAL_FORECAST_MAX_WAPE_PERCENT` y `LOCAL_FORECAST_MAX_ABS_BIAS_PERCENT`.

Se construye una serie diaria por producto con lags 1, 7, 14 y 28; medias y variación móvil; ciclo semanal y mensual; fin de semana; y último precio y su cambio reciente. Los días sin comprobante se consideran cero ventas, devoluciones registradas se restan del día y fechas con stock agotado inferido del kardex se excluyen. Una predicción no se habilita si el historial fuente cambió desde el entrenamiento.

Con el estado actual de la base (cero ventas locales), la respuesta debe mostrar exactamente **HISTORIAL INSUFICIENTE PARA GENERAR UNA PREDICCIÓN OPERATIVA.** No se insertan predicciones ni datos sintéticos en la base operativa.
