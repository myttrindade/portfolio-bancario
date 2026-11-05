"""Regras de negócio: sincronização DDA, lote com dupla aprovação, baixa no ERP."""
import sqlite3

from .clients import BankError, ErpError
from .validators import amount_from_linha, valid_cnpj, validate_linha


class ValidationError(Exception): ...
class PermissionDenied(Exception): ...
class NotFound(Exception): ...
class Conflict(Exception): ...


class Service:
    def __init__(self, con: sqlite3.Connection, bank, erp):
        self.con, self.bank, self.erp = con, bank, erp

    def _audit(self, actor: str, action: str, detail: str = "") -> None:
        self.con.execute("INSERT INTO audit(actor, action, detail) VALUES (?,?,?)", (actor, action, detail))

    # ---------- DDA ----------
    def sync_dda(self, actor: str, cnpj: str) -> dict:
        if not valid_cnpj(cnpj):
            raise ValidationError("CNPJ inválido")
        added = duplicated = invalid = 0
        with self.con:
            for b in self.bank.list_dda(cnpj):
                linha_ok = validate_linha(b["barcode"]) and amount_from_linha(b["barcode"]) == b["amount_cents"]
                if not linha_ok:  # linha com DV errado ou valor diferente do informado: não entra
                    invalid += 1
                    self._audit(actor, "DDA_INVALID", b["barcode"])
                    continue
                cur = self.con.execute(
                    "INSERT OR IGNORE INTO bills(cnpj, barcode, beneficiary, amount_cents, due_date) VALUES (?,?,?,?,?)",
                    (cnpj, b["barcode"], b["beneficiary"], b["amount_cents"], b["due_date"]))
                added, duplicated = (added + 1, duplicated) if cur.rowcount else (added, duplicated + 1)
            self._audit(actor, "DDA_SYNC", f"{cnpj} +{added} dup={duplicated} inv={invalid}")
        return {"added": added, "duplicated": duplicated, "invalid": invalid}

    def list_bills(self, status: str | None = None) -> list[dict]:
        q, args = "SELECT * FROM bills", ()
        if status:
            q, args = q + " WHERE status=?", (status,)
        return [dict(r) for r in self.con.execute(q + " ORDER BY due_date, id", args)]

    # ---------- lote ----------
    def create_batch(self, actor: str, bill_ids: list[int], idempotency_key: str) -> int:
        if not bill_ids or not idempotency_key:
            raise ValidationError("informe boletos e idempotency_key")
        row = self.con.execute("SELECT id FROM batches WHERE idempotency_key=?", (idempotency_key,)).fetchone()
        if row:  # reenvio da mesma requisição: devolve o lote existente, não cria outro
            return row["id"]
        ids = sorted(set(bill_ids))
        marks = ",".join("?" * len(ids))
        bills = self.con.execute(f"SELECT * FROM bills WHERE id IN ({marks})", ids).fetchall()
        if len(bills) != len(ids):
            raise NotFound("boleto inexistente")
        if any(b["status"] != "OPEN" for b in bills):
            raise Conflict("há boleto que já está em outro lote ou já foi pago")
        with self.con:
            cur = self.con.execute(
                "INSERT INTO batches(idempotency_key, created_by, status, total_cents) VALUES (?,?,?,?)",
                (idempotency_key, actor, "PENDING_APPROVAL", sum(b["amount_cents"] for b in bills)))
            bid = cur.lastrowid
            self.con.execute(f"UPDATE bills SET status='IN_BATCH', batch_id=? WHERE id IN ({marks})", [bid, *ids])
            self._audit(actor, "BATCH_CREATED", f"lote {bid} com {len(ids)} boletos")
        return bid

    def approve(self, batch_id: int, approver: str) -> dict:
        b = self._batch(batch_id)
        if b["status"] not in ("PENDING_APPROVAL", "APPROVING"):  # APPROVING = retry seguro após falha
            raise Conflict(f"lote está {b['status']}")
        if approver == b["created_by"]:
            raise PermissionDenied("quem criou o lote não pode aprová-lo (dupla aprovação)")
        with self.con:
            self.con.execute("UPDATE batches SET status='APPROVING', approved_by=? WHERE id=?", (approver, batch_id))
            self._audit(approver, "BATCH_APPROVED", str(batch_id))
        bills = self.con.execute("SELECT * FROM bills WHERE batch_id=?", (batch_id,)).fetchall()
        # a mesma chave por lote garante que uma nova tentativa nunca paga duas vezes
        results = self.bank.pay_batch(f"batch-{batch_id}", [dict(x) for x in bills])
        by_code = {x["barcode"]: x for x in bills}
        with self.con:
            for r in results:
                bill = by_code[r["barcode"]]
                if r["status"] == "PAID":
                    self.con.execute("UPDATE bills SET status='PAID' WHERE id=?", (bill["id"],))
                    self.con.execute(
                        "INSERT INTO payments(batch_id, bill_id, status, bank_ref, receipt, erp_status) VALUES (?,?,?,?,?,'PENDING')",
                        (batch_id, bill["id"], "PAID", r["bank_ref"], r["receipt"]))
                else:  # recusado: o boleto volta para a fila
                    self.con.execute("UPDATE bills SET status='OPEN', batch_id=NULL WHERE id=?", (bill["id"],))
                    self.con.execute(
                        "INSERT INTO payments(batch_id, bill_id, status, reason) VALUES (?,?,?,?)",
                        (batch_id, bill["id"], "REJECTED", r.get("reason", "")))
            paid = sum(1 for r in results if r["status"] == "PAID")
            status = "PAID" if paid == len(results) else "PARTIAL" if paid else "FAILED"
            self.con.execute("UPDATE batches SET status=? WHERE id=?", (status, batch_id))
            self._audit(approver, "BATCH_RESULT", f"lote {batch_id}: {status}")
        self.retry_erp(approver)
        return self.get_batch(batch_id)

    def reject(self, batch_id: int, actor: str) -> None:
        b = self._batch(batch_id)
        if b["status"] != "PENDING_APPROVAL":
            raise Conflict(f"lote está {b['status']}")
        with self.con:
            self.con.execute("UPDATE bills SET status='OPEN', batch_id=NULL WHERE batch_id=?", (batch_id,))
            self.con.execute("UPDATE batches SET status='REJECTED' WHERE id=?", (batch_id,))
            self._audit(actor, "BATCH_REJECTED", str(batch_id))

    # ---------- baixa no ERP ----------
    def retry_erp(self, actor: str) -> int:
        """Dá baixa nos pagamentos já confirmados pelo banco e ainda pendentes no ERP."""
        rows = self.con.execute(
            """SELECT p.id, p.receipt, b.cnpj, b.barcode, b.amount_cents FROM payments p
               JOIN bills b ON b.id = p.bill_id WHERE p.status='PAID' AND p.erp_status='PENDING'""").fetchall()
        done = 0
        for r in rows:
            try:
                self.erp.settle(r["cnpj"], r["barcode"], r["amount_cents"], r["receipt"])
            except ErpError:
                continue  # continua PENDING; a próxima rodada tenta de novo, sem pagar de novo
            with self.con:
                self.con.execute("UPDATE payments SET erp_status='DONE' WHERE id=?", (r["id"],))
                self._audit(actor, "ERP_SETTLED", r["barcode"])
            done += 1
        return done

    # ---------- consultas ----------
    def _batch(self, batch_id: int):
        row = self.con.execute("SELECT * FROM batches WHERE id=?", (batch_id,)).fetchone()
        if not row:
            raise NotFound("lote inexistente")
        return row

    def get_batch(self, batch_id: int) -> dict:
        b = dict(self._batch(batch_id))
        b["payments"] = [dict(r) for r in self.con.execute("SELECT * FROM payments WHERE batch_id=?", (batch_id,))]
        return b

    def audit_log(self) -> list[dict]:
        return [dict(r) for r in self.con.execute("SELECT * FROM audit ORDER BY id")]


__all__ = ["Service", "BankError", "ValidationError", "PermissionDenied", "NotFound", "Conflict"]
