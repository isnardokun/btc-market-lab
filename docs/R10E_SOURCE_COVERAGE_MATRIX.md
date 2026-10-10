# R10-E.0 — Matriz de fuentes, límites y cobertura

**Estado:** arquitectura offline; sin consulta a la DB productiva ni a APIs. Última evidencia publicada: PR #59, 10-oct-2026. No interpretar sus conteos como estado actual. Expediente: https://github.com/isnardokun/btc-market-lab/issues/60.

## Diferenciar las cinco propiedades

- **RAW:** el valor almacenado coincide con bytes adquiridos y su SHA256. No significa historia completa.
- **LINEAGE:** petición y respuesta enlazadas mediante receipt, acquisition, run y BLOB; la ausencia de receipt heredado no se suple inventándolo.
- **Contigüidad interna:** sin huecos entre MIN y MAX observados a la cadencia correcta. No acredita nada fuera del intervalo.
- **Frescura:** última marca disponible frente a última barra cerrada y latencia del proveedor.
- **Cobertura externa:** historia recuperable contrastada con documentación, límites, cuota y prueba autorizada. Estados: NOT_VERIFIED, PARTIAL, PROVIDER_LIMITED, VERIFIED_AVAILABLE_WINDOW, STALE, UNAVAILABLE.

## Evidencia histórica interna reportada (no nueva lectura)

| Fuente / métrica | Cadencia y unidad | MIN registrado | MAX comunicado | Registros | Restricción comprobada |
|---|---|---|---|---:|---|
| Binance BTCUSDT open_interest | 5m, BTC, valor USD auxiliar opcional | 2026-09-10 05:05 UTC | 2026-10-10 20:40 UTC | 8.828 | En código 500/página forward; ventana histórica externa **NO_VERIFICADA** |
| Bybit BTCUSDT open_interest | 5m, BTC | 2026-08-09 22:10 UTC | 2026-10-10 17:25 UTC | 17.800 | En código 200/página forward; ventana histórica externa **NO_VERIFICADA** |
| Binance funding_settled | eventos, fracción | 2026-03-12 16:00 UTC | 2026-10-10 00:00 UTC | 635 | Cadencia variable según contrato/período; no asumir 8h invariable |
| Bybit funding_settled | eventos, fracción | 2026-05-30 00:00 UTC | 2026-10-10 00:00 UTC | 400 | Cadencia variable según contrato/período |
| ResearchBitcoin V2 | 13 métricas escalares d1 del catálogo actual | No consultado | No consultado | No consultado | Archiver en ventanas de 14 días; Tier 2 reportado, entitlement no validado con API |
| Bitview on-chain | mixto | No consultado | No consultado | No consultado | Inventariar por serie y contrato |
| FRED | diaria/mensual; revisiones | No consultado | No consultado | No consultado | Distinguir vintages y última publicación |
| Yahoo precios | OHLC y cierres de sesiones | No consultado | No consultado | No consultado | Respetar cierres y revisiones |

**Funding P1:** 635+400 = 1.035 observaciones RAW heredadas sin recibos por petición. No fabricar URLs, timestamps de solicitudes ni intentos retrospectivos.

**Reserva R10-D.2:** el cierre informó gates inline PASS, pero no itemizó públicamente todos los manifests PK+SHA de diez tablas y cotejo backup-PRE. R10-E no cierra esa reserva por sí misma.

## Política de siguiente fase

Registrar por proveedor/símbolo/métrica/intervalo: fecha solicitada más antigua, MIN interno, MIN recuperable comprobado, último almacenado, último intento, cutoff de barra cerrada y retraso de publicación. Distinguir valores desconocidos de ceros.

El planificador scripts/market_r10e_plan_only.py mide **solo atraso forward interno OI** mediante SQLite indicada explícitamente en modo lectura; nunca hace HTTP ni escrituras, y nunca afirma historical_completeness distinto de NOT_VERIFIED.

Antes de permitir ingesta real: cuotas, entitlement, límites, exclusión mutua de writers, adquisición request→SHA, baseline/backup WAL-safe y autorización separada del propietario.
