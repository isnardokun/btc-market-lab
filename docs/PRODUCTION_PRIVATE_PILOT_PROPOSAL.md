# Primer piloto HTTP privado — propuesta, NO orden de ejecución

Estado: NOT_AUTHORIZED / CODE_NOT_IMPLEMENTED. No enviar esto como orden a
Hermes. Ingeniería G0/G1 y autorización G2 son decisiones independientes.

Objetivo: observar una adquisición HTTP auténtica, RAW/receipt/normalización/
SQLite/audit y reanudación sin refetch, sobre base nueva privada. No usar ni
copiar btc_research.db, el backup real o datos sintéticos etiquetados reales.

## Operación exacta propuesta para autorización futura

| Campo | Límite |
|---|---|
| Proveedor/mercado | Binance USD-M, BTCUSDT, funding liquidado |
| Método/origen/path | GET https://fapi.binance.com/fapi/v1/fundingRate |
| Ventana UTC | [2026-10-06T00:00:00Z, 2026-10-13T00:00:00Z) |
| Params | symbol=BTCUSDT, startTime=1791244800000, endTime=1791849599999, limit=100 |
| Peticiones máximas | 2 en total; una inicial y como máximo un retry autorizado |
| Retry | Solo timeout/5xx; 429/403 o cambio de host/contrato ⇒ STOP |
| Timeout / deadline | socket 10s; supervisor deadline 60s |
| Body / filas | <=1 MiB por respuesta, <=100 filas; máximo 2 MiB de RAW |
| Credenciales / trading | Sin credenciales; sin endpoints de órdenes/cuentas |
| Destino | Nueva raíz /home/ignotus/live-private-pilot-1, 0700 |
| DB / artefactos | pilot.db y outputs nuevos 0600; existencia previa ⇒ BLOCKED |
| Lectura/escritura DB instalada | Ninguna |
| Backup/migración/restore instalados | Ninguno |
| Cron/flags/noticias/publicación | Ninguno |
| Evidencia pública | Solo SHA/RC/conteos/estado; no RAW ni informe privado en Git |

Params se derivan de UTC; endTime incluye el último milisegundo previo al
extremo exclusivo. No pedir server-time, fundingInfo ni otro endpoint. No
continuar paginando si llega al límite: PARTIAL_VERIFIED, no histórico completo.
Una respuesta vacía no acredita cobertura; conservar receipt de empty.

Se espera consumo de cuota pública de ese endpoint; no se ha verificado
disponibilidad regional ni coste/entitlement del host. STOP ante rechazo;
no cambiar dominio, proxy o proveedor para eludirlo. Documentación oficial:
https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Get-Funding-Rate-History

## Condiciones antes de pedir G2

- G1 integrado y aceptado: SHA exacto futuro de código/plan/permiso fijados.
- Revisar esta ventana fija: si ya no sirve al objetivo, modificar propuesta
  ANTES de aprobar; jamás desplazarla automáticamente a «últimos 7 días».
- Confirmar directorio nuevo, identidad/UID, recursos y namespace privado.
- Crear grant específico con referencia del consentimiento del propietario,
  vigencia UTC concreta de <=30 minutos y budgets anteriores; no genera permiso
  por existir un archivo. Propuesta actual no tiene SHA de código aprobado.
- Autorizar quién ejecuta: Hermes puede realizar este piloto solo mediante
  orden específica posterior y dentro del rol de QA/operador autorizado.

## Aceptación del piloto

Receipt vincula GET/params/window/body SHA/status/capturas UTC al grant/run.
Funding preserva unidad fraction y eventos nativos, sin asumir 8h fijas.
Filas normalizadas concuerdan con RAW; no cursor avanzado en empty/failure.
Segunda invocación del mismo run exitoso hace cero HTTP; intentos inciertos
consumen el budget y no son reenviados sin margen aprobado.
Audit devuelve LIVE_PRIVATE_ACQUISITION_RECONCILED o FAIL/BLOCKED con causa.
Solo diagnóstico privado de una fuente; publication_authorized=false,
installed_db_migrated=false, historical_completeness=NOT_VERIFIED.

Se conserva la carpeta/evidencia; no limpiar, borrar ni tocar worktrees QA.
Los siguientes proveedores y la DB instalada requieren propuestas/consentimiento
propios; este piloto no autoriza descargar todo el histórico o gastar cuota RBN.
