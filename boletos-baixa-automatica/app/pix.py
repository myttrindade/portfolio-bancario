"""Gera o Pix "copia e cola" (BR Code EMV) com CRC16-CCITT."""


def crc16(text: str) -> str:
    crc = 0xFFFF
    for byte in text.encode():
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else crc << 1
            crc &= 0xFFFF
    return f"{crc:04X}"


def _f(field_id: str, value: str) -> str:
    return f"{field_id}{len(value):02d}{value}"


def pix_copia_e_cola(chave: str, amount_cents: int, nome: str, cidade: str, txid: str) -> str:
    conta = _f("00", "br.gov.bcb.pix") + _f("01", chave)
    payload = (_f("00", "01") + _f("26", conta) + _f("52", "0000") + _f("53", "986")
               + _f("54", f"{amount_cents / 100:.2f}") + _f("58", "BR") + _f("59", nome[:25])
               + _f("60", cidade[:15]) + _f("62", _f("05", txid)) + "6304")
    return payload + crc16(payload)
