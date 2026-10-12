# Package B — ejecución

Estado B_IN_PROGRESS / DRAFT / HERMES_STOP / PRODUCTION_NOT_ENABLED.
Spec: PACKAGE_B_DESIGN.md. Plan: PACKAGE_B_IMPLEMENTATION_PLAN.md.
Base master 8fc5a60 (cierre A), una PR B, ejecución inline.

Ruling: se continúa el diseño y alcance ya autorizados en A/B/C e Issue #77,
con artefactos concretos revisables y trabajo offline autónomo. No se altera
el flujo productivo ni se infieren permisos externos.

Ruling: el candidato visual usa GLD (contrato Yahoo validado en A) para oro;
no lo llama spot/futuro ni lo compara como mismo instrumento que GC=F.

## 2026-10-12 — primer candidato integrado, no aceptación final

Implementado snapshot inmutable de una sola transacción RO (auditor A +
selección observed/captured/vintage), 3 assets OHLCV, cálculos source-keyed,
HTML autocontenido y gate local que recompone modelo/HTML. Ejemplo E2E:
57 streams/seis proveedores vía HTTP congelado y los adaptadores A reales.

RSI legacy tenía seed del último período y después recorría cambios anteriores:
vector independiente final 30.2749128664 vs Wilder correcto 37.7887719821.
Dos regresiones RED→GREEN corrigen seed al primer período. Se conserva la
convención legacy zero-loss=100, incluyendo flat prices; no se afirma una
paridad no demostrada con todas las implementaciones externas.

Revisión independiente del diff: dos P1 y un P2 reproducidos y corregidos
RED→GREEN. Freshness por cadencia incluye STALE explícito en macro/derivados/
onchain y horas UTC visibles; GLD conserva centavos en escenarios; export
rechaza symlink/hardlink y reemplaza archivos atómicamente. Un funding 1e-8
ya no se redondea falsamente a cero. Tres suites B: 25 métodos nuevos.

Ruling: optional series STALE se pueden mostrar como histórico claramente
rotulado, con warnings y timestamps; no sostienen inferencias actuales.
Precios críticos ausentes/stale/misaligned bloquean el candidato. El score
local evalúa reconciliación, no frescura externa/entitlement ni permiso de
publicación; el gate productivo anterior se mantiene intacto.

Ruling: verificación visual todavía pendiente. No hay binario Chromium en
este entorno; la descarga de headless-shell devolvió archivo inválido y
falló. No existen capturas ni PDF A4 verificados y no se simula QA visual.
El candidato HTML es reproducible con el CLI sintético, no un deploy.

Pendiente B antes de una única orden Hermes: integración completa con el
Publication Gate productivo sobre fixture, endurecer fuentes/metodología/
unidades/criterios analíticos restantes, actualizar y validar el skill de
diseño exclusivo, render responsive/A4 auténtico y auditoría de cierre.
B_IN_PROGRESS; HERMES_STOP. No live API/DB/cron/publicación.
