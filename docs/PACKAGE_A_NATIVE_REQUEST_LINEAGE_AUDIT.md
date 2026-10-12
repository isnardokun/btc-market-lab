# Paquete A — Auditoría de identidad de solicitud nativa contra recibo RAW

**Estado:** implementado solo en fixtures HTTP congelados y SQLite marcada.
No supone llamadas reales, firma externa de datos, acceso Tier2 ni derecho
de publicación. Complementa, no sustituye, la auditoría de hashes previa.

## Vulnerabilidad de linaje corregida

Antes, el auditor `audit_frozen_request_provenance` comprobaba el SHA-256
almacenado de los metadatos de una petición HTTP, su transporte ficticio y
redacción de credenciales. No podía demostrar que esos campos coincidieran
con el recibo real. Un cambio de `startTime` o del endpoint, acompañado
de un SHA-256 **correctamente recalculado**, pasaba dicha prueba de
integridad interna: la solicitud podía pertenecer a **otra ventana o métrica**
sin ser detectada por ese gate.

El nuevo módulo `scripts/package_a_native_request_audit.py` reconstruye
de forma independiente la petición esperada desde **cada recibo** y el
constructor de URL del cliente legado instalado en el repositorio:

- Proveedor, métrica, símbolo, frecuencia, `start_utc` y
  `end_exclusive_utc` del recibo definen el contrato.
- El generador fuente `_request_for` determina endpoint HTTPS, ruta y
  parámetros nativos exactos, incluyendo fronteras de intervalos
  semicerrados, `interval=1d/range=max` de Yahoo, límites sintéticos y
  consultas de FRED/Binance/Bybit.
- La credencial ficticia `api_key` se compara **redactada**, sin exponer
  tokens ni guardar valor real. Los parámetros se comparan en JSON
  canónico contra los metadatos persistidos.
- Una identidad alterada, aunque esté firmada con un hash internamente
  correcto, produce `NATIVE_FROZEN_REQUEST_LINEAGE_FAIL`.
- Páginas fallidas/sin observaciones pueden validar endpoint y rango con
  el catálogo explícito, pero `requests_without_observed_unit_contract`
  permanece visible. **No se inventan unidades observadas** ni
  disponibilidad externa.
- Recibos antiguos o directos sin metadatos HTTP conservan el contador
  `historical_receipts_without_frozen_provenance`; no se atribuyen
  peticiones retrospectivas inexistentes.

## Integración transaccional

`run_fixture_page` exige ahora el nuevo gate además de:
`RAW_SQL_LINEAGE_PASS`, `FROZEN_REQUEST_METADATA_PASS`,
`BATCH_SOURCE_RUN_LINEAGE_PASS`, `SOURCE_MANIFEST_LOCAL_EVIDENCE_PASS`
y `PRAGMA foreign_key_check/quick_check`.

La nueva verificación ocurre **dentro de la transacción aún no confirmada**
para cada página. Si alguien manipula la identidad de la petición previa
o la nueva página, se revierte toda escritura de esta última. El
`inspect_fixture` de solo lectura incorpora los contadores bajo
`native_request_lineage`. Los recibos y observaciones anteriores no se
modifican para falsear un PASS.

## Pruebas

`tests/unit/test_package_a_native_request_audit.py` contiene 12 casos
adversariales. Incluye compatibilidad con las ocho peticiones fuente de
los seis proveedores, sustitución de endpoint OI→funding de Binance,
ventana temporal RBN falsa, `observation_end` FRED adulterado,
símbolo de Bybit cambiado, origen HTTPS falsificado y JSON firmado que
no corresponde al constructor real.

Los casos verifican explícitamente que el **auditor anterior sigue PASS**
en las falsificaciones que conservan su SHA, mientras el nuevo auditor
falla y detiene nuevas transacciones. También se ensayan fallos 429/503
sin observaciones, respuesta HTTP 200 `rejected` y recibo anterior sin
provenance HTTP para impedir retrospectivas ficticias.

El runner offline se amplía a **69 suites registradas, 68 ejecutables
offline y una integración real intencionalmente excluida**.

## Límites de garantía

La integridad interna reconstruida no constituye una firma independiente
del proveedor y no evita un adversario que controle tanto el código Python
de verificación como la base de datos. Sigue faltando identificar la
retención y entitlement reales, medir cuota/costo, inventariar todos los
writers en host y realizar el QA independiente sobre SHA congelada. No se
habilita red ni el scheduler para resolver esas dependencias.
