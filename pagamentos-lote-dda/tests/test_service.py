from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.clients import BankError
from app.demo import CNPJS, build_demo_service, make_dda_bill
from app.main import create_app
from app.service import Conflict, NotFound, PermissionDenied, ValidationError
from app.validators import build_linha, valid_cnpj, validate_linha


@pytest.fixture
def env():
    svc, bank, erp = build_demo_service()
    svc.sync_dda("ana", CNPJS[0])
    return svc, bank, erp


def open_ids(svc):
    return [b["id"] for b in svc.list_bills("OPEN")]


def test_validators():
    linha = build_linha("341", date(2026, 11, 10), 12345, "0" * 24 + "1")
    assert validate_linha(linha)
    trocado = str((int(linha[12]) + 1) % 10)
    assert not validate_linha(linha[:12] + trocado + linha[13:])   # dígito do campo 2 adulterado
    trocado = str((int(linha[-1]) + 1) % 10)
    assert not validate_linha(linha[:-1] + trocado)                # valor adulterado (DV geral falha)
    assert valid_cnpj(CNPJS[0]) and not valid_cnpj("11111111111111") and not valid_cnpj("123")


def test_sync_is_idempotent_and_rejects_bad_boleto(env):
    svc, bank, _ = env
    assert len(svc.list_bills()) == 4
    bank.dda[CNPJS[0]].append({**make_dda_bill(99, 5000, date(2026, 12, 1), "Fraude Demo"), "barcode": "1" * 47})
    r = svc.sync_dda("ana", CNPJS[0])
    assert r == {"added": 0, "duplicated": 4, "invalid": 1}
    assert len(svc.list_bills()) == 4
    with pytest.raises(ValidationError):
        svc.sync_dda("ana", "00.000.000/0000-00")


def test_batch_rules(env):
    svc, *_ = env
    ids = open_ids(svc)
    bid = svc.create_batch("ana", ids[:2], "k1")
    assert svc.create_batch("ana", ids[:2], "k1") == bid          # reenvio não duplica
    with pytest.raises(Conflict):
        svc.create_batch("ana", ids[:1], "k2")                    # boleto já está em lote
    with pytest.raises(NotFound):
        svc.create_batch("ana", [9999], "k3")


def test_four_eyes_then_paid_and_settled(env):
    svc, bank, erp = env
    bid = svc.create_batch("ana", open_ids(svc), "k1")
    with pytest.raises(PermissionDenied):
        svc.approve(bid, "ana")
    batch = svc.approve(bid, "bruno")
    assert batch["status"] == "PAID" and bank.calls == 1
    assert all(p["erp_status"] == "DONE" and p["receipt"] for p in batch["payments"])
    assert len(erp.settled) == 4
    with pytest.raises(Conflict):
        svc.approve(bid, "bruno")                                  # não aprova duas vezes
    assert [a["action"] for a in svc.audit_log()][-1] == "ERP_SETTLED"


def test_partial_rejection_returns_bill_to_queue(env):
    svc, bank, erp = env
    ids = open_ids(svc)
    bank.reject.add(svc.list_bills()[0]["barcode"])
    bid = svc.create_batch("ana", ids, "k1")
    batch = svc.approve(bid, "bruno")
    assert batch["status"] == "PARTIAL"
    assert len(svc.list_bills("OPEN")) == 1 and len(erp.settled) == 3


def test_erp_down_keeps_pending_and_retry_does_not_repay(env):
    svc, bank, erp = env
    erp.fail = True
    bid = svc.create_batch("ana", open_ids(svc), "k1")
    batch = svc.approve(bid, "bruno")
    assert batch["status"] == "PAID" and all(p["erp_status"] == "PENDING" for p in batch["payments"])
    erp.fail = False
    assert svc.retry_erp("sistema") == 4
    assert bank.calls == 1 and len(erp.settled) == 4


def test_bank_timeout_retry_never_pays_twice(env):
    svc, bank, erp = env
    bid = svc.create_batch("ana", open_ids(svc), "k1")
    bank.drop_response_once = True                                 # banco pagou, mas a resposta se perdeu
    with pytest.raises(BankError):
        svc.approve(bid, "bruno")
    assert svc.get_batch(bid)["status"] == "APPROVING"
    batch = svc.approve(bid, "bruno")                              # nova tentativa
    assert batch["status"] == "PAID" and bank.calls == 1 and len(erp.settled) == 4


def test_api_flow():
    svc, *_ = build_demo_service()
    c = TestClient(create_app(svc))
    ana, bruno = {"X-User": "ana"}, {"X-User": "bruno"}
    assert c.post("/dda/sync", json={"cnpj": CNPJS[1]}, headers=ana).json()["added"] == 4
    ids = [b["id"] for b in c.get("/bills", params={"status": "OPEN"}).json()]
    b = c.post("/batches", json={"bill_ids": ids, "idempotency_key": "x"}, headers=ana).json()
    assert c.post(f"/batches/{b['id']}/approve", headers=ana).status_code == 403
    done = c.post(f"/batches/{b['id']}/approve", headers=bruno).json()
    assert done["status"] == "PAID"
    assert c.post("/batches", json={"bill_ids": ids, "idempotency_key": "y"}, headers=ana).status_code == 409
    assert c.get("/audit").status_code == 200
