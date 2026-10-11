# R10-E.7 — Manifiesto de cobertura histórica local, multifuente y solo lectura

**Alcance:** inventario de archivos ya persistidos, NO ingesta, NO descargas, NO validación externa. Basado en Issue #60. El propietario conserva autorización de producción; ChatGPT implementa/modifica/mergea dentro del alcance autorizado; Hermes únicamente ejecuta QA de un SHA exacto y reporta.

## Cambio técnico

El módulo `scripts/market_r10e7_ro_archive_manifest.py` abre **exclusivamente** una SQLite ya existente y especificada por `--db`; nunca usa la ruta por defecto del repositorio. Valida existencia y esquema de tablas conocidas, y usa `mode=ro`, `PRAGMA query_only=ON` y transacción de lectura/snapshot coherente. Rechaza rutas ausentes y bases anexas. No contiene cliente HTTP, `--apply`, migración, cron ni escritor.

### Inventarios soportados

| Fuente | Tablas usadas | Qué puede observarse | Qué NO se puede inferir |
|---|---|---|---|
| ResearchBitcoin | `onchain_external_observations`, `archive_sources`, `archive_ingest_windows` | 13 métricas escalares conocidas, filas y MIN/MAX UTC, unidad y raw_scale original, `plan_tier` y flag local declarados, estados de windows | Que token tenga Tier 2 REAL; fecha de primera serie externa; completitud del catálogo Tier 2 |
| Bitview | `daily` + `series` | Filas por serie marcada `idx=1d`; rangos UTC archivados | Que estén todos los días que la API ofrece, ni cobertura de series indexadas por altura |
| FRED | `macro_fred`, `archive_fred_vintages` | 12 series del catálogo actual, rangos por serie y cantidad de vintages archivados | Que los días no hábiles o meses sin publicación sean gaps, o que se conserven todas las revisiones pasadas |
| Yahoo | `market_ohlc_history` | Rangos y filas del proveedor `Yahoo Finance` por símbolo | Que `range=max` devuelva toda la historia, ni que días sin mercado sean gaps |

El manifiesto diferencia **NOT_INITIALIZED**, **NO_OBSERVATIONS** y **ARCHIVED_RANGE_ONLY**. Las fechas inicial y final son límites **guardados localmente**, no disponibilidad externa. Al faltar una tabla, se informa explícitamente; si existe con columnas incompatibles, el lector falla cerrado.

**ResearchBitcoin:** se presenta el `entitlement_flag_db` únicamente como dato almacenado, y `independent_tier2_access_proved=false` SIN EXCEPCIONES. La evaluación actual no tiene credenciales ni HTTP y nunca puede afirmar `real_tier2_entitlement_verified=true`.

**FRED:** la existencia de filas en `archive_fred_vintages` no reconstruye todas las eras ALFRED. Para Yahoo, ni fines de semana ni festivos equivalen automáticamente a datos faltantes.

**Archivador:** resume `archive_ingest_windows` separando estados `ok`, `empty`, `partial`, `failed`; no equipara `empty` con historia completa.

## Garantías

- Sin HTTP de mercado, sin escritura SQLite, sin `--apply`, sin credenciales. `db_location=REDACTED` para evitar filtrado accidental de rutas en la salida JSON.
- `historical_completeness=NOT_VERIFIED`, `provider_availability=NOT_CHECKED`, `execution_authorized=false` para todos los resultados.
- Los tests emplean **solo TemporaryDirectory con SQLite sintética**: 16 pruebas de inventario, fechas, proveedores excluidos, columnas incompletas, fallas cerradas, flag Tier2 local, ro/no WAL, rechazo de CLI `--apply` y unicidad de suites.
- Registro del runner **45 entradas** con E.7 agregada una vez. E.0–E.6 acumulan 91 pruebas focales, total de E.7 + regresión **107** casos esperados, sujeto a QA real. No afirmar PASS antes del reporte de Hermes.

## Limitaciones y siguiente gate

Es un **auditor de rangos internos**, no un verificador de proveedor. No inspecciona por sí solo discontinuidades 5m de OI, receipts HTTP o RAW; siguen existiendo auditores específicos R10-C/D. No crea nuevas tablas o migraciones, no consolida todas las fuentes en una única ingestión recurrente y no habilita scheduler.

Para aproximarse al objetivo de **histórico completo disponible por API**, la próxima fase necesita una matriz de evidencia externa por endpoint, primeras/últimas fechas accesibles, cuota y límites efectivamente autorizados, una sola página live de piloto bajo permiso del propietario y posterior conciliación WAL-safe. ResearchBitcoin Tier 2 requiere prueba de entitlement por medios oficiales y sin exponer token. Funding P1 preserva sus 1.035 observaciones heredadas sin request receipts inventados. La reserva retrospectiva de R10-D.2 permanece separada.

**Estado de la PR:** DRAFT hasta QA Hermes sobre SHA congelado. No está autorizado usarla contra la DB productiva por el mero hecho de que sea lectura.
