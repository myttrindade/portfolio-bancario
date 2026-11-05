"""Dados fictícios para demonstração e testes (nenhum CNPJ ou boleto é real)."""
from datetime import date, timedelta

from .clients import FakeErp, FakeItauSandbox
from .db import connect
from .service import Service
from .validators import build_linha, make_cnpj

CNPJS = [make_cnpj("112223330001"), make_cnpj("445556660001")]
_FORNECEDORES = ["Energia Demo S/A", "Saneamento Demo", "Elevadores Atlas Demo", "Limpeza Brilho Demo"]


def make_dda_bill(n: int, amount_cents: int, due: date, beneficiary: str) -> dict:
    return {"barcode": build_linha("341", due, amount_cents, f"{n:025d}"),
            "beneficiary": beneficiary, "amount_cents": amount_cents, "due_date": due.isoformat()}


def build_demo_service(db_path: str = ":memory:"):
    bank, erp = FakeItauSandbox(), FakeErp()
    n = 0
    for cnpj in CNPJS:
        bank.dda[cnpj] = []
        for i, nome in enumerate(_FORNECEDORES, 1):
            n += 1
            bank.dda[cnpj].append(make_dda_bill(n, 15000 * i + n, date(2026, 11, 10) + timedelta(days=i), nome))
    return Service(connect(db_path), bank, erp), bank, erp
