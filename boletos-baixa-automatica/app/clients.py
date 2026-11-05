"""Fronteiras externas. Em produção: API de cobrança do Itaú (OAuth2 + mTLS) e API do Superlógica."""
from datetime import date

from .pix import pix_copia_e_cola
from .validators import build_linha


class ErpError(Exception):
    """ERP indisponível."""


class FakeItauCobranca:
    def __init__(self, pix_key: str = "pix@condominio-demo.example"):
        self.pix_key, self._seq = pix_key, 0

    def register(self, charge: dict) -> dict:
        self._seq += 1
        our_number = f"{self._seq:08d}"
        txid = f"DEMO{charge['id']:020d}"
        linha = build_linha("341", date.fromisoformat(charge["due_date"]), charge["amount_cents"],
                            f"{our_number:0>25}")
        pix = pix_copia_e_cola(self.pix_key, charge["amount_cents"], "CONDOMINIO DEMO", "SAO PAULO", txid)
        return {"our_number": our_number, "txid": txid, "linha": linha, "pix_payload": pix}


class FakeErp:
    def __init__(self):
        self.settlements: list[dict] = []
        self.fail = False

    def settle(self, charge_id: int, amount_cents: int, kind: str, late: bool) -> None:
        if self.fail:
            raise ErpError("ERP indisponível")
        self.settlements.append({"charge_id": charge_id, "amount_cents": amount_cents, "kind": kind, "late": late})
