# Package A — revisión transversal de cierre offline

Corte: 2026-10-12 UTC / 2026-10-11 Colombia. PR única #79.
Base de revisión: `8ca80e0f221ad2b0b276f6557d16ee00fe0ee381`.
HEAD revisado antes de correcciones: `faeee6acc27288272f5bb53ba144f1503a273900`.
El SHA de entrega corregida se fija en la orden QA de GitHub, no en una referencia circular en este archivo.

## Cobertura y decisión

Una revisión de contexto fresco inspeccionó el diff de 83 archivos por dominios:
entrypoints/CLI, flock/leases y supervisión de descendientes, marcadores de
fixture, esquema/migraciones, RAW y SQL, provenance nativa, custodia de runs,
cursores, planners, reintentos/recuperación, Yahoo fanout, aceptación y CI.
La revisión reprodujo cuatro bloqueos y una regresión de error CLI; el autor
reprodujo además el falso PASS entre snapshots. Las correcciones están en
esta misma rama, sin micro-PR ni cambios productivos.

| Hallazgo | Efecto anterior | Corrección y regresión observada |
|---|---|---|
| P0: lease copiada por fork | Hijo escribe con `_parent_lock_held=True` mientras un contender posee flock; cleanup del hijo libera flock del padre | Verificador ligado al PID adquirente; solo ese PID ejecuta LOCK_UN. Dos tests de procesos reales RED→GREEN; cero recibos en el hijo rechazado |
| P1: aceptación entre snapshots | RAW PASS de una versión se adjunta a agregados de otra ya corrupta | `_inspect_fixture_snapshot` reutiliza una única transacción `mode=ro` / query_only para RAW, lineage, integridad y matriz de 46 streams; interleaving con writer sintético RED→GREEN |
| P1: Bitview CLI directa | Import `scripts` falla sin PYTHONPATH; daily.sh abortaría | Root del repositorio en sys.path antes del import; subprocess --help desde fuera del repositorio RED→GREEN |
| P1: snapshot RC=1 tras éxito | Dictionary de snapshot llega a SystemExit y aborta daily.sh | main convierte dictionary exitoso a RC0, conservando RC3 por lock; tests API CLI y proceso RED→GREEN |
| P1: Bybit OI acepta envelope de error/scope ajeno | retCode=10006 o symbol/category incorrectos producen filas y cursor | Código explícito debe ser entero 0; scope explícito debe ser BTCUSDT/linear; dos tests integrados RED→GREEN, RAW rechazado conservado y cero cursores |
| P2: parser RBN fuera de scope | NameError reemplaza rechazos controlados | parser pasado explícitamente al delegate; DB inexistente RC2 y fetch fake rechazado RC1 RED→GREEN |

Diez métodos nuevos de regresión ejercitan estos efectos, en suites existentes.
El helper lector no abre rutas ni concede permisos: exige transacción activa
y query_only. El lector público conserva su API y resultado histórico.

## Evidencia local

Python 3.12.14. Sobre el árbol corregido:

```bash
PYTHONDONTWRITEBYTECODE=1 BTC_RESEARCH_HOME=<checkout-aislado> python3 tests/run_all.py
```

RC0; `REGISTERED_SUITES=73`, `OFFLINE_SUITES_EXECUTED=72`,
`OFFLINE_SUITES_PASSED=72`, `LIVE_SUITES_SKIPPED=1`,
`LIVE_STATUS=NOT_TESTED_NOT_AUTHORIZED`.
También PASS el test de retención de artefactos que ejecuta CI y `git diff --check`.
Los logs completos permanecen locales; no se publican payloads ni evidencia de producción.
CI del SHA congelado debe observarse antes de publicar la orden independiente.

## Rulings y límites

- **Compatibilidad:** OI Bybit sin retCode conserva el contrato de fixtures
  antiguos. Un retCode presente nulo, booleano, string o no cero se rechaza;
  funding sigue exigiendo el código exitoso. Esto no valida una respuesta live.
  Coste si se interpreta como contrato productivo: un envelope sin código no
  certifica éxito upstream; la verificación externa sigue pendiente.
- **Snapshot:** el PASS describe una versión SQLite coherente en el instante
  leído. Un writer posterior puede invalidar el estado actual, pero no altera
  la evidencia de aquella versión; toda nueva operación reaudita su snapshot.
- **Minor diferido:** algunas suites importan TestCase de suites auxiliares y
  unittest las descubre otra vez. Las cifras por método no representan tests
  únicos. El gate cuenta 72 suites ejecutadas, no una cifra comercial de tests.
- Flock sigue siendo advisory. PID+inode no protege de root ni writers que
  ignoran el candado, ni demuestra cobertura host-wide de cron/worktrees.
- Entitlement/retención/cuotas, unidades Bitview, dimensiones Tier2 RBN,
  calendarios Yahoo y disponibilidad de histórico real siguen sin verificar.
  Funding P1 y reserva R10-D.2 se conservan, sin recibos retrospectivos.

**Decisión:** candidato a UNA QA offline de Hermes sobre nuevo SHA congelado,
condicionado a CI verde. No es `A_OFFLINE_ACCEPTED` hasta reporte Hermes y
revisión técnica. PR permanece draft, sin merge/producción/API/cron/publicación.
No hay P0/P1 confirmado pendiente de corrección en los dominios revisados;
esto no garantiza ausencia de defectos que descubra la QA independiente.
