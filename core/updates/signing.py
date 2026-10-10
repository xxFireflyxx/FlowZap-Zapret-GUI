"""
core/updates/signing.py
-----------------------
Подпись обновлений FlowZap (Ed25519). Без неё FlowZap доверял любому архиву
со страницы релизов — проверялось только, что файл скачался целиком (размер
и sha256 из того же GitHub), и подменённый релиз (чужой доступ к GitHub,
подмена на зеркале) установился бы.

Закрытые ключи — только у автора (release/signing.py, файлы под паролем вне
проекта); здесь — открытые. Ключей два: основной и запасной (хранится
отдельно, на случай потери основного). Подпись принимается любым из них.

Рядом с архивом в релизе лежит <архив>.sig — JSON: формат, тег, имя файла,
sha256, id ключа и подпись. Подписываются тег и имя вместе с содержимым —
старую подписанную версию не выдать за новую и один архив за другой.
"""

import base64
import hashlib
import json

SIGNATURE_SUFFIX = ".sig"
_FORMAT = 1

# Открытые ключи (32 байта, hex). Создаются release/signing.py keygen;
# меняются только вместе с выпуском новой версии FlowZap.
PUBLIC_KEYS: dict[str, str] = {
    "main":    "d5f5fe86ebb46f498963bac3173ffb1b6cb0113878f82f5c8b5dfae03f98e1c1",
    "reserve": "c433deae2bec14ac767b0242207030a3825ce1ea95a8535ed648dafbb0058ddb",
}


class SignatureError(ValueError):
    """Подпись не найдена, повреждена или не сходится — не устанавливать."""


def signed_message(tag: str, name: str, sha256_hex: str) -> bytes:
    return f"FlowZap update\n{_FORMAT}\n{tag}\n{name}\n{sha256_hex}".encode("utf-8")


def make_signature(private_key, key_id: str, tag: str, name: str, data: bytes) -> bytes:
    """Содержимое файла .sig (для release/signing.py). private_key —
    Ed25519PrivateKey из cryptography."""
    digest = hashlib.sha256(data).hexdigest()
    sig = private_key.sign(signed_message(tag, name, digest))
    return (json.dumps({
        "format": _FORMAT, "tag": tag, "name": name, "sha256": digest,
        "key": key_id, "signature": base64.b64encode(sig).decode("ascii"),
    }, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def verify(data: bytes, name: str, tag: str, sig_file: bytes) -> str:
    """Проверить архив name релиза tag по содержимому его .sig. Возвращает
    id ключа, которым подписано; иначе SignatureError с причиной."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    try:
        info = json.loads(sig_file.decode("utf-8"))
        signature = base64.b64decode(info["signature"], validate=True)
    except Exception:
        raise SignatureError("файл подписи повреждён")
    if info.get("format") != _FORMAT:
        raise SignatureError(f"неизвестный формат подписи ({info.get('format')!r})")
    if info.get("tag") != tag or info.get("name") != name:
        raise SignatureError(f"подпись от другого файла ({info.get('name')} {info.get('tag')})")
    digest = hashlib.sha256(data).hexdigest()
    if info.get("sha256") != digest:
        raise SignatureError("архив не совпадает с подписанным")

    message = signed_message(tag, name, digest)
    # Сначала ключ, указанный в подписи, потом остальные — id лишь подсказка
    order = sorted(PUBLIC_KEYS, key=lambda k: k != info.get("key"))
    for key_id in order:
        public_hex = PUBLIC_KEYS[key_id]
        if not public_hex:
            continue
        try:
            Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_hex)).verify(signature, message)
            return key_id
        except InvalidSignature:
            continue
    raise SignatureError("подпись не от автора FlowZap")
