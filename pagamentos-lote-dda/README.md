# Pagamentos em lote com DDA e dupla aprovação

Projeto de demonstração (sandbox simulado) que reproduz o fluxo de uma administradora de condomínios:
buscar boletos via DDA por CNPJ, montar um lote, **segunda pessoa aprova**, pagar no banco e **dar baixa no ERP com o comprovante**.

> Os clientes do banco e do ERP são simulados (`app/clients.py`). Nenhum dado, CNPJ ou boleto é real.
> Em produção, `FakeItauSandbox` é trocado por um cliente da API do Itaú (OAuth2 + certificado mTLS em cofre)
> e `FakeErp` por um cliente da API do ERP. O resto do sistema não muda.

## O que ele resolve

| Risco | Como é tratado | Teste |
|---|---|---|
| Boleto falso ou adulterado | Valida DV da linha digitável (mod 10 e mod 11) e confere o valor com o informado pelo DDA | `test_sync_is_idempotent_and_rejects_bad_boleto` |
| Mesmo boleto entrando duas vezes | `barcode UNIQUE` + sincronização idempotente | idem |
| Pagamento sem controle | Dupla aprovação: quem cria o lote não pode aprová-lo | `test_four_eyes_then_paid_and_settled` |
| Pagar duas vezes após timeout | Chave de idempotência por lote; o retry devolve o resultado original | `test_bank_timeout_retry_never_pays_twice` |
| Banco pagou, ERP caiu | Pagamento fica `PENDING` no ERP e é reprocessado sem pagar de novo | `test_erp_down_keeps_pending_and_retry_does_not_repay` |
| Pagamento recusado no meio do lote | Lote vira `PARTIAL`; o boleto recusado volta para a fila | `test_partial_rejection_returns_bill_to_queue` |
| Rastreabilidade | Tabela de auditoria com quem fez o quê | `GET /audit` |

## Fluxo

```
DDA (banco) -> sync -> bills (OPEN) -> lote (PENDING_APPROVAL) -> aprovação por 2ª pessoa
   -> pagamento no banco (idempotente) -> bills PAID -> baixa no ERP com comprovante
```

## Como rodar

```bash
pip install -r requirements.txt
python -m pytest -q
uvicorn app.main:app --reload   # documentação interativa em http://127.0.0.1:8000/docs
```

Todas as chamadas de escrita exigem o cabeçalho `X-User` (em produção viria do login com 2FA).
No `/docs`, use `ana` para criar o lote e `bruno` para aprovar.

## Estrutura

- `app/validators.py`: CNPJ e linha digitável
- `app/service.py`: regras de negócio (testadas)
- `app/clients.py`: banco e ERP simulados
- `app/main.py`: API FastAPI
- `tests/`: 8 testes cobrindo os cenários acima

## Fora do escopo desta demonstração (e como seria em produção)

Autenticação real com 2FA e perfis; certificado digital em cofre (Key Vault/Secrets Manager); PostgreSQL com
migrações; fila para reprocessamento; pagamento de Pix, tributos e contas de consumo (aqui só boletos);
tela web; observabilidade e alertas; conformidade com a LGPD.
