# Paquete A — gate de evidencia integrada (seis proveedores, offline)

**Estado:** gate sintético de *preparación de evidencia para QA independiente*; no es autorización para producción, acceso real a proveedores, publicación ni aceptación de cobertura histórica completa.

## Hallazgo transversal

La auditoría local preexistente `LOCAL_FIXTURE_INTEGRITY_PASS` verificaba que los recibos y observaciones **existentes** tenían RAW/SHA, enlaces y cursores coherentes. Pero podía aprobar una SQLite que contenía únicamente algunas fuentes, o recibos provenientes de fixtures directos sin solicitud HTTP congelada. La integridad de lo almacenado **no equivale** a la presencia del universo contratado ni a un cierre verificable.

## Nuevo criterio verificable

`scripts/package_a_integrated_acceptance.py` incorpora `audit_integrated_offline_acceptance(db_path, lock_dir)`, sin network, shell, subprocess, escrituras o acceso productivo. Solo admite el archivo SQLite desechable bajo el marcador original Package A y abre la evidencia en una conexión SQLite `mode=ro` con `PRAGMA query_only=ON` y transacción de lectura consistente.

La matriz exacta —conforme al catálogo instalado y sin inventar fuentes Tier2— contiene **46 series**:

| Proveedor | Streams requeridos | Condición |
|---|---:|---|
| ResearchBitcoin | 13 | Scalar BTC d1 del catálogo local, RAW y recibo vinculado a un `source_run` v3 |
| Bitview | 23 | Series BTC d1 configuradas, índice temporal preservado y recibos en un `source_run` |
| Yahoo | 5 | SPY open/high/low/close/volume, misma ventana, mismo SHA RAW y SHA de solicitud simulada |
| FRED | 1 | CPIAUCSL mensual con observaciones fuente y request lineage |
| Binance | 2 | BTCUSDT OI 5m y funding settled event |
| Bybit | 2 | BTCUSDT OI 5m y funding settled event |

**Gate fail-closed:** se requieren todas las 46 series con al menos una observación, recibo exitoso, request provenance nativa comprobada y cursor exitoso; no se permite ninguna serie ajena al perfil. Los runs del catálogo deben estar en estado `complete`; no se presenta un run `running` o `blocked` como finalizado. No se admiten recibos `failed`, `rejected` ni `empty` en **este perfil de evidencia limpia**; un historial de intentos fallidos y recuperados requiere su reconciliación y aceptación específica, no su borrado.

La salida distingue **conteo de series por proveedor** de **conteo de observaciones por proveedor** y reporta por separado ausencias, extras, defectos de custodia en `package_a_run_pages`, SHA/linaje RAW, integridad FK y estados de source runs.

La etiqueta `OFFLINE_INTEGRATED_EVIDENCE_READY_FOR_INDEPENDENT_QA` significa **solo** que una SQLite sintética completa ha superado esos criterios para ser examinada. No significa `A_OFFLINE_ACCEPTED`, no acredita un endpoint o respuesta externa, y no ejecuta ni convoca a Hermes.

## Pruebas adversariales

`tests/unit/test_package_a_integrated_acceptance.py` construye 46 streams reales del contrato del código fuente en una **única SQLite temporal**, utilizando el catálogo RBN+Bitview, una única petición Yahoo simulada con fan-out de cinco métricas y los constructores legados reales de FRED, Binance OI/funding y Bybit OI/funding, siempre con opener HTTP sintético one-shot.

Se ejercitan integración íntegra, incompletitud con auditoría local PASS, ausencia de Yahoo, receipt 429 adicional, series Yahoo no catalogadas, receipt antiguo sin HTTP proveniente de acceso directo, RAW digest falsificado, metadatos eliminados, custodia de run ausente, distinción precisa de observaciones/streams, run falsamente marcado `blocked`, y negación explícita de permisos de producción o QA completado.

No se afirma que los 46 streams representen todos los indicadores y símbolos necesarios para el producto final ni que sus datos se hayan descargado de forma real. **RBN Tier2, calendario/retención Yahoo, unidades Bitview y global host writer exclusion siguen sin verificar**.

## Auditoría de cambios contra master

La comparación GitHub de la PR #79 en este ciclo alcanza **más de 80 archivos y alrededor de 300 commits**; su tamaño exige una revisión independiente por dominios antes de congelar un único SHA. El runner portable existente omite explícitamente la integración live. La PR continúa como borrador. No se debe utilizar la aprobación de CI como sustituto de la revisión de seguridad/diff completa ni como permiso de cutover.
