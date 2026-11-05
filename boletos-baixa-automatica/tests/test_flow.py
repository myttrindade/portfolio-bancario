import json
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.clients import FakeErp, FakeItauCobranca
from app.db import connect
from app.main import create_app
from app.pix import crc16
from app.reconcile import amount_due
from app.service import InvalidSignature, Service, sign
from app.validators import make_cnpj, valid_cnpj, validate_linha

SECRET = "segredo-de-teste"
CNPJ = make_cnpj("112223330001")
DUE = date(2026, 11, 10)


@pytest.fixture
def env():
    erp = FakeErp()
    svc = Service(connect(), FakeItauCobranca(), erp, SECRET)
    charge = svc.issue_charge(CNPJ, "Maria (apto 12)", "Condomínio nov/2026", 100000, DUE)
    return svc, erp, charge


def hook(svc, charge, amount, paid_at="2026-11-09", event_id=None, secret=SECRET):
    body = json.dumps({"event_id": event_id or f"evt-{amount}-{paid_at}", "reference": charge["our_number"],
                       "amount_cents": amount, "paid_at": paid_at, "channel": "boleto"}).encode()
    return svc.handle_webhook(body, sign(secret, body))


def test_issue_charge_has_valid_boleto_and_pix(env):
    _, _, c = env
    assert valid_cnpj(CNPJ) and validate_linha(c["linha"]) and c["status"] == "REGISTERED"
    assert c["pix_payload"][-4:] == crc16(c["pix_payload"][:-4])
    assert crc16("123456789") == "29B1"  # vetor de teste oficial do CRC16-CCITT-FALSE


def test_exact_payment_settles_in_erp(env):
    svc, erp, c = env
    assert hook(svc, c, 100000)["result"] == "paid"
    assert svc.get_charge(c["id"])["status"] == "PAID"
    assert erp.settlements == [{"charge_id": c["id"], "amount_cents": 100000, "kind": "PAID", "late": False}]


def test_partial_then_complement(env):
    svc, erp, c = env
    r = hook(svc, c, 40000)
    assert r["result"] == "partial" and r["remaining_cents"] == 60000
    assert hook(svc, c, 60000, event_id="e2")["result"] == "paid"
    got = svc.get_charge(c["id"])
    assert got["status"] == "PAID" and got["paid_cents"] == 100000 and len(erp.settlements) == 2


def test_overpayment_creates_credit(env):
    svc, _, c = env
    r = hook(svc, c, 103000)
    assert r["result"] == "overpaid" and r["credit_cents"] == 3000
    assert svc.get_charge(c["id"])["credit_cents"] == 3000
    assert svc.exceptions()[0]["kind"] == "OVERPAID"


def test_late_payment_adds_fine_and_interest(env):
    svc, _, c = env
    assert amount_due(100000, DUE, date(2026, 11, 25)) == 102500   # 2% multa + 15 dias de juros pro rata
    r = hook(svc, c, 100000, paid_at="2026-11-25")                  # pagou sem encargos
    assert r["result"] == "partial" and r["remaining_cents"] == 2500
    assert hook(svc, c, 2500, paid_at="2026-11-25", event_id="e2")["result"] == "paid"


def test_duplicate_payment_is_not_applied_and_is_flagged(env):
    svc, erp, c = env
    hook(svc, c, 100000)
    r = hook(svc, c, 100000, event_id="outro-evento")
    assert r["result"] == "duplicate"
    assert len(erp.settlements) == 1 and svc.get_charge(c["id"])["paid_cents"] == 100000
    assert [e["kind"] for e in svc.exceptions()] == ["DUPLICATE"]


def test_same_event_twice_is_processed_once(env):
    svc, erp, c = env
    hook(svc, c, 100000, event_id="evt-1")
    assert hook(svc, c, 100000, event_id="evt-1")["result"] == "already_processed"
    assert len(erp.settlements) == 1 and svc.exceptions() == []


def test_bad_signature_is_rejected(env):
    svc, _, c = env
    with pytest.raises(InvalidSignature):
        hook(svc, c, 100000, secret="chave-errada")
    assert svc.get_charge(c["id"])["status"] == "REGISTERED"


def test_unknown_reference_goes_to_exception_queue(env):
    svc, *_ = env
    body = json.dumps({"event_id": "x", "reference": "99999999", "amount_cents": 5000,
                       "paid_at": "2026-11-09", "channel": "pix"}).encode()
    assert svc.handle_webhook(body, sign(SECRET, body))["result"] == "unmatched"
    assert svc.exceptions()[0]["kind"] == "UNMATCHED"


def test_erp_down_then_retry(env):
    svc, erp, c = env
    erp.fail = True
    hook(svc, c, 100000)
    assert svc.get_charge(c["id"])["events"][0]["erp_status"] == "PENDING" and erp.settlements == []
    erp.fail = False
    assert svc.retry_erp() == 1 and len(erp.settlements) == 1


def test_api_webhook_signature():
    svc = Service(connect(), FakeItauCobranca(), FakeErp(), SECRET)
    client = TestClient(create_app(svc))
    c = client.post("/charges", json={"cnpj": CNPJ, "payer": "João", "description": "Taxa",
                                      "amount_cents": 50000, "due_date": "2026-11-10"}).json()
    body = json.dumps({"event_id": "a1", "reference": c["txid"], "amount_cents": 50000,
                       "paid_at": "2026-11-09", "channel": "pix"})
    assert client.post("/webhooks/bank", content=body, headers={"X-Signature": "falsa"}).status_code == 401
    ok = client.post("/webhooks/bank", content=body, headers={"X-Signature": sign(SECRET, body.encode())})
    assert ok.json()["result"] == "paid" and client.get(f"/charges/{c['id']}").json()["status"] == "PAID"
