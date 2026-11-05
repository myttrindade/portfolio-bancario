import os

from fastapi import Depends, FastAPI, Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .clients import BankError
from .demo import build_demo_service
from .service import Conflict, NotFound, PermissionDenied, Service, ValidationError


class SyncIn(BaseModel):
    cnpj: str


class BatchIn(BaseModel):
    bill_ids: list[int]
    idempotency_key: str


def create_app(service: Service | None = None) -> FastAPI:
    svc = service or build_demo_service(os.getenv("DB_PATH", ":memory:"))[0]
    app = FastAPI(title="Pagamentos em lote com DDA (sandbox)")

    def user(x_user: str = Header(..., description="Quem está operando (em produção viria do login/2FA)")):
        return x_user

    for exc, code in [(ValidationError, 400), (PermissionDenied, 403), (NotFound, 404), (Conflict, 409), (BankError, 502)]:
        app.add_exception_handler(exc, lambda r, e, c=code: JSONResponse({"error": str(e)}, status_code=c))

    @app.post("/dda/sync")
    def sync(body: SyncIn, u: str = Depends(user)):
        return svc.sync_dda(u, body.cnpj)

    @app.get("/bills")
    def bills(status: str | None = None):
        return svc.list_bills(status)

    @app.post("/batches", status_code=201)
    def create_batch(body: BatchIn, u: str = Depends(user)):
        return svc.get_batch(svc.create_batch(u, body.bill_ids, body.idempotency_key))

    @app.get("/batches/{batch_id}")
    def get_batch(batch_id: int):
        return svc.get_batch(batch_id)

    @app.post("/batches/{batch_id}/approve")
    def approve(batch_id: int, u: str = Depends(user)):
        return svc.approve(batch_id, u)

    @app.post("/batches/{batch_id}/reject")
    def reject(batch_id: int, u: str = Depends(user)):
        svc.reject(batch_id, u)
        return svc.get_batch(batch_id)

    @app.post("/erp/retry")
    def erp_retry(u: str = Depends(user)):
        return {"settled": svc.retry_erp(u)}

    @app.get("/audit")
    def audit():
        return svc.audit_log()

    return app


app = create_app()
