import os
from datetime import date

from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .clients import FakeErp, FakeItauCobranca
from .db import connect
from .service import InvalidSignature, NotFound, Service, ValidationError


class ChargeIn(BaseModel):
    cnpj: str
    payer: str
    description: str
    amount_cents: int
    due_date: date


def create_app(service: Service | None = None) -> FastAPI:
    svc = service or Service(connect(os.getenv("DB_PATH", ":memory:")), FakeItauCobranca(), FakeErp(),
                             os.getenv("WEBHOOK_SECRET", "demo-secret"))
    app = FastAPI(title="Boletos com baixa automática (sandbox)")

    for exc, code in [(ValidationError, 400), (InvalidSignature, 401), (NotFound, 404)]:
        app.add_exception_handler(exc, lambda r, e, c=code: JSONResponse({"error": str(e)}, status_code=c))

    @app.post("/charges", status_code=201)
    def issue(body: ChargeIn):
        return svc.issue_charge(body.cnpj, body.payer, body.description, body.amount_cents, body.due_date)

    @app.get("/charges")
    def charges():
        return svc.list_charges()

    @app.get("/charges/{charge_id}")
    def charge(charge_id: int):
        return svc.get_charge(charge_id)

    @app.post("/webhooks/bank")
    async def webhook(request: Request, x_signature: str = Header("")):
        return svc.handle_webhook(await request.body(), x_signature)  # corpo BRUTO: a assinatura cobre os bytes exatos

    @app.post("/erp/retry")
    def erp_retry():
        return {"settled": svc.retry_erp()}

    @app.get("/exceptions")
    def exceptions():
        return svc.exceptions()

    return app


app = create_app()
