# Reconciliación UTC entre informe y base SQLite

## Problema confirmado en el PR de Hermes #17

El HTML publicó BTC USD 81,676 y RSI 46.9, pero el mismo ciclo archivó BTC USD 83,010 y RSI 51.76. La causa fue que archive_metrics reutilizaba una vela diaria de Yahoo aún abierta y el HTML no.

## Política de integridad

- HTML y archivo diario deben seleccionar las mismas velas de mercado con closed_daily_bars.
- La fecha del reporte y los nombres de archivos usan UTC, nunca el día civil local del PC.
- Cada activo BTC, SPY, GOLD, SILVER y OIL guarda price y close_ts (Unix segundos UTC) por report_date.
- El publication gate compara las cotizaciones del HTML con daily_metrics del mismo report_date. Compara RSI y MACD BTC y su marca as-of. Bloquea si faltan datos o se archiva una vela del día todavía abierta.
- Tolerancias por redondeo visual: BTC/ORO 0.51 USD; SPY/PLATA/WTI 0.011 USD; RSI 0.055; MACD 0.015.
- La falla es crítica y no se compensa con el score de otros criterios. No mandar Telegram ni exportar HTML portátil si falla el gate.
- Los históricos ya contaminados de otras fechas no se modifican automáticamente. Requieren un backfill separado y auditable.

## Validación local Hermes

Ejecutar las pruebas de regresión:

    python3 -m unittest -v tests.unit.test_archive_reconciliation

Luego ejecutar el ciclo real sin envío de Telegram:

    bash scripts/hermes_review_cycle.sh --no-update --pipeline --include-html

Verificar que el nuevo reporte y el valor BTC/RSI archivado coincidan. Entregar log y HTML mediante un NUEVO PR borrador de diagnóstico; no fusionar los PR borrador de Hermes.

**Límite:** la reconciliación prueba consistencia entre el HTML y la base local; no verifica de forma independiente la autenticidad de las fuentes Yahoo, FRED o bitview.
