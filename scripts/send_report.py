#!/usr/bin/env python3
"""Hermes entry point: deliver an approved portable HTML report via Telegram.

Uses sendDocument (outbound HTTPS only). No webhook, polling or publicly exposed
web server. Requires TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in the environment.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import tempfile
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
PORTABLE_DIR = ROOT / "reports" / "portable"
DELIVERY_DIR = ROOT / "reports" / "deliveries"
MAX_FILE_SIZE = 45 * 1024 * 1024


def sha256_bytes(content):
    return hashlib.sha256(content).hexdigest()


def validate_attachment(path: Path) -> tuple[dict, bytes]:
    path = path.resolve()
    if path.parent != PORTABLE_DIR.resolve() or path.suffix.lower() != ".html":
        raise ValueError("Solo pueden enviarse HTML desde reports/portable/")
    if not path.is_file():
        raise FileNotFoundError(path)
    content = path.read_bytes()
    if not content or len(content) > MAX_FILE_SIZE:
        raise ValueError("Adjunto vacío o superior a 45 MiB")
    manifest_path = path.with_name(path.name + ".manifest.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("portable_sha256") != sha256_bytes(content):
        raise ValueError("El HTML no coincide con el manifest validado")
    if manifest.get("offline") is not True or float(manifest.get("approved_score", 0)) < 95:
        raise ValueError("El adjunto no tiene aprobación válida")
    if not path.name.endswith(manifest.get("report_date", "") + ".html"):
        raise ValueError("Fecha inconsistente entre nombre y manifest")
    return manifest, content


def create_multipart(chat_id, filename, content, caption):
    boundary = "hermes" + secrets.token_hex(12)
    body = bytearray()
    for name, value in (("chat_id", chat_id), ("caption", caption)):
        body += f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode()
    body += (f"--{boundary}\r\nContent-Disposition: form-data; "
             f"name=\"document\"; filename=\"{filename}\"\r\n"
             "Content-Type: text/html\r\n\r\n").encode()
    body += content + f"\r\n--{boundary}--\r\n".encode()
    return bytes(body), f"multipart/form-data; boundary={boundary}"


def send_document(token, chat_id, path, content, caption):
    payload, content_type = create_multipart(chat_id, path.name, content, caption)
    req = Request(
        f"https://api.telegram.org/bot{token}/sendDocument",
        data=payload,
        headers={"Content-Type": content_type, "User-Agent": "Hermes-MercadosDaily/1.0"},
        method="POST",
    )
    try:
        with urlopen(req, timeout=45) as response:
            result = json.loads(response.read())
    except HTTPError as exc:
        # Never output the URL: it includes the secret bot token.
        raise RuntimeError(f"Telegram HTTP {exc.code}") from None
    except (URLError, TimeoutError) as exc:
        raise RuntimeError(f"Telegram connection failed: {type(exc).__name__}") from None
    if not result.get("ok"):
        raise RuntimeError("Telegram rechazó sendDocument")
    return result.get("result", {}).get("message_id")


def write_receipt(destination, payload):
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".delivery-", dir=destination.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump(payload, out, ensure_ascii=False, indent=2)
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, destination)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def main():
    parser = argparse.ArgumentParser(description="Enviar el último reporte HTML portátil a Telegram")
    parser.add_argument("--file", type=Path, help="Archivo de reports/portable/ (por defecto el más reciente)")
    parser.add_argument("--dry-run", action="store_true", help="Validar sin enviar")
    parser.add_argument("--force", action="store_true", help="Reenviar incluso si existe recibo")
    args = parser.parse_args()

    if args.file:
        path = args.file
    else:
        candidates = sorted(PORTABLE_DIR.glob("daily_report_????-??-??.html"))
        if not candidates:
            parser.exit(1, "No hay HTML portátil aprobado para enviar\n")
        path = candidates[-1]

    try:
        manifest, content = validate_attachment(path)
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        parser.exit(1, f"Adjunto rechazado: {exc}\n")

    if args.dry_run:
        print(f"DRY RUN OK: {path.name}, {len(content)} bytes, SHA256 {sha256_bytes(content)}")
        return

    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        parser.exit(1, "Configura TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID en el entorno\n")

    chat_fingerprint = hashlib.sha256(chat_id.encode()).hexdigest()[:12]
    receipt = DELIVERY_DIR / (
        f"{manifest['report_date']}_{chat_fingerprint}_{sha256_bytes(content)[:16]}.json"
    )
    if receipt.exists() and not args.force:
        print(f"NO ENVIADO: ya existe recibo para {path.name}. Usa --force para reenviar.")
        return

    caption = f"Mercados Daily Pro | {manifest['report_date']} | Reporte HTML validado"
    try:
        message_id = send_document(token, chat_id, path, content, caption)
    except RuntimeError as exc:
        parser.exit(1, f"Envío fallido: {exc}\n")
    write_receipt(receipt, {
        "report_date": manifest["report_date"],
        "portable_sha256": sha256_bytes(content),
        "telegram_message_id": message_id,
        "chat_fingerprint": chat_fingerprint,
    })
    print(f"ENVIADO: {path.name}, mensaje Telegram #{message_id}")


if __name__ == "__main__":
    main()
