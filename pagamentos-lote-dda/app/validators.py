"""Validações de CNPJ e de boleto bancário (linha digitável de 47 dígitos)."""
import re
from datetime import date

_BASE = date(1997, 10, 7)  # data-base do fator de vencimento


def mod10(num: str) -> int:
    total, weight = 0, 2
    for ch in reversed(num):
        p = int(ch) * weight
        total += p // 10 + p % 10
        weight = 1 if weight == 2 else 2
    return (10 - total % 10) % 10


def mod11_barcode(num43: str) -> int:
    total, weight = 0, 2
    for ch in reversed(num43):
        total += int(ch) * weight
        weight = 2 if weight == 9 else weight + 1
    dv = 11 - total % 11
    return 1 if dv in (0, 10, 11) else dv


def due_factor(due: date) -> int:
    f = (due - _BASE).days
    while f > 9999:  # o fator reinicia em 1000 depois de 21/02/2025
        f -= 9000
    return f


def build_linha(bank: str, due: date, amount_cents: int, free25: str) -> str:
    """Monta uma linha digitável válida (usada pelo sandbox e pelos testes)."""
    assert len(bank) == 3 and len(free25) == 25
    fator, valor = f"{due_factor(due):04d}", f"{amount_cents:010d}"
    dv = mod11_barcode(f"{bank}9{fator}{valor}{free25}")
    c1, c2, c3 = f"{bank}9{free25[:5]}", free25[5:15], free25[15:25]
    return f"{c1}{mod10(c1)}{c2}{mod10(c2)}{c3}{mod10(c3)}{dv}{fator}{valor}"


def validate_linha(s: str) -> bool:
    if not re.fullmatch(r"\d{47}", s or ""):
        return False
    c1, c2, c3 = s[0:9], s[10:20], s[21:31]
    if (mod10(c1), mod10(c2), mod10(c3)) != (int(s[9]), int(s[20]), int(s[31])):
        return False
    free = s[4:9] + c2 + c3
    return mod11_barcode(f"{s[0:3]}{s[3]}{s[33:37]}{s[37:47]}{free}") == int(s[32])


def amount_from_linha(s: str) -> int:
    return int(s[37:47])


def _cnpj_dv(base: str) -> str:
    w = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2] if len(base) == 12 else [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    r = sum(int(a) * b for a, b in zip(base, w)) % 11
    return "0" if r < 2 else str(11 - r)


def make_cnpj(base12: str) -> str:
    d1 = _cnpj_dv(base12)
    return base12 + d1 + _cnpj_dv(base12 + d1)


def valid_cnpj(cnpj: str) -> bool:
    d = re.sub(r"\D", "", cnpj or "")
    return len(d) == 14 and len(set(d)) > 1 and make_cnpj(d[:12]) == d
