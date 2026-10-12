# Package B — plan de implementación inline

Spec: PACKAGE_B_DESIGN.md. Rama única feat/package-b-auditable-research-report.
Base 8fc5a60. No API/SQLite/cron productivos. Conservar SHA de QA A.

1. Snapshot y temporalidad: TDD de fuente completa, hashes, dato posterior,
   captura futura, FRED vintage; implementar reader read-only y perfil.
2. Analítica: referencia publicada de Wilder usada solo como vector matemático
   suministrado en tests, SMA/MACD/ATR/escenarios existentes y alineación OHLCV;
   demostrar RED→GREEN del RSI y propagar parameter/input SHA.
3. Informe: TDD HTML autocontenido, 3 assets, ejes, unidades/dígitos y estados;
   renderer determinista + gate anti-tamper que comparte modelo auditable.
4. E2E: generar SQLite marcada con fake HTTP A y un reporte completo, someter
   fallos, integrar runner/CI; inspeccionar responsive y print sobre HTML local.
5. Publicar en PR B draft. Revisar dominios con contexto fresco, corregir P0/P1,
   confirmar regresión/CI y solo entonces una orden Hermes de SHA congelado.

Revisión de interfaces: evidence devuelve modelo inmutable al cálculo/render;
renderer no tiene SQLite/HTTP; gate compara modelo y documento generado,
legacy checks son complemento, no certificación externa. Fixtures tienen RAW
native request provenance original, no INSERT de cifras directas de informe.
Se mantiene un ledger de ejecución en PACKAGE_B_EXECUTION_LEDGER.md.
