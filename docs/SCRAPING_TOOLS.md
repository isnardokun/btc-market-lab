> **SEGURIDAD (2026-10-08):** Este documento contenía credenciales que estuvieron expuestas públicamente. Se retiraron del estado actual; **rota/revoca FRED, Telegram Bot y Exa/ScrapeGraph inmediatamente**. El historial Git todavía conserva las versiones anteriores. No vuelvas a publicar valores de claves en docs, commits ni logs.

# Scraping & Data Ingestion Tools

## 1. Fuentes de datos

### Yahoo Finance (OHLCV)
- **Script:** `ingestion/ingest_price.py`
- **Wrapper:** `ingestion/ingest_all.py`
- **Symbols:** `BTC-USD`, `SPY`, `GC=F` (Gold), `BZ=F` (Brent), `CL=F` (WTI), `SI=F` (Silver)
- **API:** `yfinance` Python library
- **Frecuencia:** diario (cierre NYSE)
- **Tabla destino:** `price_btc`
- **Dependencias:** `yfinance`

```bash
pip install yfinance
python3 ingestion/ingest_price.py
```

---

### FRED (Federal Reserve Economic Data)
- **Script:** `ingestion/ingest_fred.py`
- **API:** FRED REST API (`https://api.stlouisfed.org/fred/`)
- **Auth:** `FRED_API_KEY` (query param `?api_key=`)
- **Series:** 15 series (ver `DATABASE_SCHEMA.md`)
- **Tabla destino:** `macro_fred`
- ** Reconciliation:** cada run re-ingesta últimos 60 días (detecta revisiones)
- **Dependencias:** ninguna (requests stdlib)

```bash
FRED_API_KEY="$FRED_API_KEY" python3 ingestion/ingest_fred.py
```

---

### Bitview (On-Chain BTC)
- **Script:** `ingestion/ingest.py`
- **API:** Bitcoin Data Bitcoin-Core RPC o bitview.io API
- **Tabla destino:** `daily` (8.9M filas)
- **Series:** 2,807 series disponibles (solo 12 en uso)
- **Frecuencia:** diaria por series
- **Dependencias:** ninguna específica (SQLite directo)

```bash
python3 ingestion/ingest.py
```

---

### Exa (Noticias BTC/Macro)
- **Script:** `ingestion/news_pipeline.py`
- **Wrapper CLI:** `mcporter` (MCP CLI, parte de `agent-reach`)
- **Server:** ScrapeGraph AI MCP Server v3.4.7 (`sgai-*`)
- **API Key:** configurada como secreto del servidor MCP; nunca guardar su valor en Git.
- **Venv:** `/home/ignotus/.agent-reach-venv/`
- **Endpoint:** `exa.web_search_exa` via mcporter

```bash
# Activar venv y buscar
source ~/.agent-reach-venv/bin/activate
mcporter call exa.web_search_exa query="Bitcoin price analysis October 2026" numResults=5
```

---

## 2. Estructura de ingestas

```
ingest_all.py  (orquestador)
├── ingest_price.py   → price_btc        (yfinance)
├── ingest.py         → daily            (bitview)
└── ingest_fred.py    → macro_fred       (FRED API)
    └── news_pipeline.py → logs/news_errors.log (Exa)
```

## 3. mcporter / Exa setup

### Instalación
```bash
# Venv ya existente en:
ls ~/.agent-reach-venv/

# Activar
source ~/.agent-reach-venv/bin/activate

# Ver servers configurados
mcporter list
```

### Verificar API key de Exa
```bash
mcporter list --schema 2>&1 | grep -i exa
```

### Llamar Exa directamente
```python
import subprocess, os

env = os.environ.copy()
env["VIRTUAL_ENV"] = "/home/ignotus/.agent-reach-venv"
env["PATH"] = "/home/ignotus/.agent-reach-venv/bin:" + env.get("PATH", "")

result = subprocess.run(
    ["mcporter", "call", "exa.web_search_exa",
     "query=Bitcoin ETF approval October 2026", "numResults=5"],
    capture_output=True, text=True, timeout=30, env=env
)
print(result.stdout)
```

## 4. ScrapeGraph AI MCP (local)

- **Server:** `sgai-*` (ScrapeGraph AI MCP Server v3.4.7)
- **Puerto:** localhost (verificar con `ps aux | grep sgai`)
- **Uso:** ingestion de páginas web estructuradas
- **Alternativa local:** Ollama para inferencia local de noticias

## 5. News Pipeline (detalle)

```python
# news_pipeline.py usa Exa via mcporter
def exa_search(query, n=5):
    """Consulta Exa via mcporter. Retorna lista de resultados."""
    env = os.environ.copy()
    env["VIRTUAL_ENV"] = VENV_PATH  # ~/.agent-reach-venv
    env["PATH"] = VENV_PATH + "/bin:" + env.get("PATH", "")

    result = subprocess.run(
        ["mcporter", "call", "exa.web_search_exa",
         f"query={query}", f"numResults={n}"],
        capture_output=True, text=True, timeout=30, env=env
    )
    return parse_exa_output(result.stdout)
```

**Parsing:** busca líneas `Title:` en output plain text (no JSON — mcporter devuelve texto plano).

**Clasificación:** keyword-based (BIAS_KEYWORDS, IMPACT_KEYWORDS en `news_pipeline.py`).

**Errores:** se loguean en `logs/news_errors.log` con timestamp, query, y output parcial.

## 6. Keys y configuración

### Keys actuales (en `.env` de Hermes)

| Variable | Valor | Ubicación |
|---|---|---|
| `FRED_API_KEY` | [REDACTED — usar una clave nueva en entorno] | `/home/ignotus/.hermes/.env` |
| `EXA_API_KEY` | [REDACTED — rotar y configurar en MCP] | Configuración MCP server |
| `TELEGRAM_BOT_TOKEN` | [REDACTED — rotar y configurar en Hermes] | `/home/ignotus/.hermes/.env` |
| `TELEGRAM_CHAT_ID` | [CONFIGURAR EN ENTORNO] | `/home/ignotus/.hermes/.env` |

### Para agregar keys al config

**FRED:** editar `ingestion/config.py`:
```python
FRED_API_KEY = os.environ.get("FRED_API_KEY", "")
```

**No hardcodear keys en scripts** — usar `os.environ.get()`.

## 7. Limites y rate limits

| Fuente | Límite | Notas |
|---|---|---|
| FRED API | 120 requests/sesión | API key pública gratis |
| Exa MCP | Rate limit en free tier | Loguea errores en `news_errors.log` |
| Yahoo Finance | ~2,000 requests/hora por IP | No requiere API key |
| bitview | Variable | Depende del provider |

## 8. Troubleshooting

```bash
# Ver si Exa mcporter funciona
source ~/.agent-reach-venv/bin/activate
mcporter call exa.web_search_exa query="test" numResults=1

# Ver errores recientes de news
tail -20 logs/news_errors.log

# Ver última ejecución de FRED
grep -i "fred\|error" cron.log | tail -10

# Test ingest individual
FRED_API_KEY="$FRED_API_KEY" python3 ingestion/ingest_fred.py
```
