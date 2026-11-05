"""Regra de conciliação: dado o que foi cobrado e o que chegou, decide o que fazer.

Função pura (sem banco de dados, sem rede), por isso é fácil de testar e de auditar.
Multa de 2% + juros de 1% ao mês (pro rata por dia) após o vencimento.
"""
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

MULTA = Decimal("0.02")
JUROS_MES = Decimal("0.01")


def _cents(x: Decimal) -> int:
    return int(x.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def amount_due(principal_cents: int, due: date, pay_date: date) -> int:
    if pay_date <= due:
        return principal_cents
    days = (pay_date - due).days
    p = Decimal(principal_cents)
    return principal_cents + _cents(p * MULTA) + _cents(p * JUROS_MES / 30 * days)


@dataclass
class Outcome:
    kind: str          # PAID | PARTIAL | OVERPAID | DUPLICATE
    late: bool
    applied_cents: int  # quanto vai para a baixa no ERP (0 se duplicado)
    paid_total: int
    remaining: int
    credit: int


def reconcile(status: str, principal: int, due: date, paid_so_far: int, amount: int, pay_date: date) -> Outcome:
    if status == "PAID":  # já quitado: qualquer novo valor é pagamento em duplicidade
        return Outcome("DUPLICATE", pay_date > due, 0, paid_so_far, 0, 0)
    owed = amount_due(principal, due, pay_date)
    total = paid_so_far + amount
    late = pay_date > due
    if total < owed:
        return Outcome("PARTIAL", late, amount, total, owed - total, 0)
    if total == owed:
        return Outcome("PAID", late, amount, total, 0, 0)
    return Outcome("OVERPAID", late, amount, total, 0, total - owed)
