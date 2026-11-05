"""Fronteiras externas: banco e ERP.

Em produção, FakeItauSandbox seria trocado por um cliente real do Itaú
(OAuth2 + certificado mTLS guardado em cofre) e FakeErp por um cliente da API do
Superlógica. O restante do sistema só conhece estas duas interfaces.
"""


class BankError(Exception):
    """Falha de comunicação com o banco (resultado do lote desconhecido)."""


class ErpError(Exception):
    """ERP indisponível ou recusou a baixa."""


class FakeItauSandbox:
    def __init__(self):
        self.dda: dict[str, list[dict]] = {}   # cnpj -> boletos DDA
        self.reject: set[str] = set()          # barcodes que o banco recusa
        self.processed: dict[str, list[dict]] = {}
        self.calls = 0                          # quantas vezes o banco REALMENTE pagou
        self.fail_before = False                # simula queda antes de processar
        self.drop_response_once = False         # simula timeout DEPOIS de processar

    def list_dda(self, cnpj: str) -> list[dict]:
        return [dict(b) for b in self.dda.get(cnpj, [])]

    def pay_batch(self, idem_key: str, items: list[dict]) -> list[dict]:
        if self.fail_before:
            self.fail_before = False
            raise BankError("banco indisponível")
        if idem_key not in self.processed:      # idempotência: mesma chave, mesmo resultado
            self.calls += 1
            out = []
            for n, it in enumerate(items, 1):
                if it["barcode"] in self.reject:
                    out.append({"barcode": it["barcode"], "status": "REJECTED", "reason": "saldo insuficiente"})
                else:
                    ref = f"ITAU-{self.calls:04d}-{n:03d}"
                    out.append({"barcode": it["barcode"], "status": "PAID", "bank_ref": ref,
                                "receipt": f"COMPROVANTE {ref} R$ {it['amount_cents'] / 100:.2f}"})
            self.processed[idem_key] = out
        if self.drop_response_once:
            self.drop_response_once = False
            raise BankError("timeout aguardando resposta")
        return self.processed[idem_key]


class FakeErp:
    def __init__(self):
        self.settled: dict[str, dict] = {}
        self.fail = False

    def settle(self, cnpj: str, barcode: str, amount_cents: int, receipt: str) -> None:
        if self.fail:
            raise ErpError("ERP indisponível")
        self.settled[barcode] = {"cnpj": cnpj, "amount_cents": amount_cents, "receipt": receipt}
