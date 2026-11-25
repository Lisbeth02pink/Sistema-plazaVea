# Seguridad y trazabilidad

## Acceso y sesión

- Contraseñas con hash scrypt y sal aleatoria, nunca se guardan en texto plano.
- La cookie `plaza_session` es `HttpOnly`, `SameSite=Lax` y el token se guarda en PostgreSQL como SHA-256.
- Las sesiones expiran según la política de seguridad. Cerrar sesión, suspender una cuenta, cambiar su contraseña o restablecerla revoca las sesiones correspondientes.
- Los intentos fallidos se limitan por huella de correo y dirección remota. La respuesta de inicio de sesión no revela si una cuenta existe.
- Los endpoints validan sesión y módulo en el backend. La administración requiere el permiso correspondiente, y las operaciones reservadas al Super Administrador comprueban ese rol en servidor.
- Para publicar detrás de HTTPS, configura `COOKIE_SECURE=true` en el entorno del backend.

## Bitácora

La API `GET /api/admin/audit` es de consulta y el endpoint de exportación CSV registra también quién realizó la exportación. La pantalla clasifica acciones por módulo: autenticación, usuarios y roles, productos, ventas, inventarios, predicción, alertas, reposición, seguridad y auditoría.

Se registran ventas, movimientos de inventario, cambios de catálogo y de políticas, decisiones y recepciones de reposición, cambios de usuarios y roles, permisos por módulo, cambios de configuración, respaldos, revisiones y acciones de entrenamiento o generación de pronósticos. Los eventos operativos almacenan al actor, acción, registro relacionado, resumen y fecha. Ventas, kardex, recepción de reposición y cambios de política escriben su evento de auditoría dentro de la misma transacción que la operación.

Las contraseñas, cookies y tokens de recuperación no se escriben en la bitácora. Los eventos de acceso fallido se registran sin incluir credenciales.
