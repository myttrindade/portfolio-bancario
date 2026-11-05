"""Emissão de cobranças e processamento seguro dos avisos (webhooks) do banco."""
import hashlib
import hmac
import json
import sqlite3
from datetime import date

from .clients import ErpError
from .reconcile import reconcile
from .validators import valid_cnpj


class ValidationError(Exception): ...
class InvalidSignature(Exception): ...
class NotFound(Exception): ...


def sign(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


class Service:
    def __init__(self, con: sqlite3.Connection, bank, erp, webhook_secret: str):
        self.con, self.bank, self.erp, self.secret = con, bank, erp, webhook_secret

    # ---------- emissão ----------
    def issue_charge(self, cnpj: str, payer: str, description: str, amount_cents: int, due: date) -> dict:
        if not valid_cnpj(cnpj):
            raise ValidationError("CNPJ inválido")
        if amount_cents <= 0:
            raise ValidationError("valor deve ser maior que zero")
        with self.con:
            cur = self.con.execute(
                "INSERT INTO charges(cnpj, payer, description, amount_cents, due_date) VALUES (?,?,?,?,?)",
                (cnpj, payer, description, amount_cents, due.isoformat()))
            charge = {"id": cur.lastrowid, "amount_cents": amount_cents, "due_date": due.isoformat()}
            reg = self.bank.register(charge)  # registra no banco: linha digitável + Pix
            self.con.execute("UPDATE charges SET our_number=?, txid=?, linha=?, pix_payload=? WHERE id=?",
                             (reg["our_number"], reg["txid"], reg["linha"], reg["pix_payload"], cur.lastrowid))
        return self.get_charge(cur.lastrowid)

    # ---------- webhook ----------
    def handle_webhook(self, raw_body: bytes, signature: str) -> dict:
        if not hmac.compare_digest(sign(self.secret, raw_body), signature or ""):
            raise InvalidSignature("assinatura inválida")
        p = json.loads(raw_body)
        event_id, reference = p["event_id"], p["reference"]
        amount, paid_at = int(p["amount_cents"]), date.fromisoformat(p["paid_at"])

        if self.con.execute("SELECT 1 FROM events WHERE event_id=?", (event_id,)).fetchone():
            return {"result": "already_processed", "event_id": event_id}  # banco reenviou o aviso

        ch = self.con.execute("SELECT * FROM charges WHERE our_number=? OR txid=?", (reference, reference)).fetchone()
        if not ch:  # pagamento que não sabemos de quem é: vai para a fila de exceções, nunca é descartado
            self.con.execute(
                "INSERT INTO events(event_id, kind, channel, reference, amount_cents, paid_at, note) VALUES (?,?,?,?,?,?,?)",
                (event_id, "UNMATCHED", p.get("channel"), reference, amount, paid_at.isoformat(),
                 "referência desconhecida: conferir manualmente"))
            self.con.commit()
            return {"result": "unmatched", "event_id": event_id}

        out = reconcile(ch["status"], ch["amount_cents"], date.fromisoformat(ch["due_date"]),
                        ch["paid_cents"], amount, paid_at)
        note = {"DUPLICATE": "pagamento em duplicidade: devolver ao pagador",
                "OVERPAID": f"pago a maior: crédito de {out.credit} centavos",
                "PARTIAL": f"pagamento parcial: faltam {out.remaining} centavos"}.get(out.kind, "")
        new_status = "PAID" if out.kind in ("PAID", "OVERPAID") else ("PARTIAL" if out.kind == "PARTIAL" else ch["status"])
        with self.con:  # evento + atualização da cobrança na mesma transação
            self.con.execute(
                """INSERT INTO events(event_id, charge_id, kind, channel, reference, amount_cents, applied_cents,
                   paid_at, late, erp_status, note) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (event_id, ch["id"], out.kind, p.get("channel"), reference, amount, out.applied_cents,
                 paid_at.isoformat(), int(out.late), "PENDING" if out.applied_cents else "NA", note))
            if out.kind != "DUPLICATE":
                self.con.execute("UPDATE charges SET status=?, paid_cents=?, credit_cents=? WHERE id=?",
                                 (new_status, out.paid_total, out.credit, ch["id"]))
        self.retry_erp()
        return {"result": out.kind.lower(), "event_id": event_id, "remaining_cents": out.remaining,
                "credit_cents": out.credit}

    # ---------- baixa no ERP ----------
    def retry_erp(self) -> int:
        rows = self.con.execute("SELECT * FROM events WHERE erp_status='PENDING' ORDER BY paid_at").fetchall()
        done = 0
        for e in rows:
            try:
                self.erp.settle(e["charge_id"], e["applied_cents"], e["kind"], bool(e["late"]))
            except ErpError:
                continue
            self.con.execute("UPDATE events SET erp_status='DONE' WHERE event_id=?", (e["event_id"],))
            self.con.commit()
            done += 1
        return done

    # ---------- consultas ----------
    def get_charge(self, charge_id: int) -> dict:
        row = self.con.execute("SELECT * FROM charges WHERE id=?", (charge_id,)).fetchone()
        if not row:
            raise NotFound("cobrança inexistente")
        c = dict(row)
        c["events"] = [dict(r) for r in self.con.execute("SELECT * FROM events WHERE charge_id=? ORDER BY paid_at", (charge_id,))]
        return c

    def list_charges(self) -> list[dict]:
        return [dict(r) for r in self.con.execute("SELECT * FROM charges ORDER BY id")]

    def exceptions(self) -> list[dict]:
        """Fila para o financeiro: o que a baixa automática não resolve sozinha."""
        return [dict(r) for r in self.con.execute(
            "SELECT * FROM events WHERE kind IN ('DUPLICATE','UNMATCHED','OVERPAID') ORDER BY paid_at")]
