# Boletos com Pix e baixa automática por webhook

Projeto de demonstração (sandbox simulado): emite uma cobrança com **linha digitável e Pix copia-e-cola**,
recebe o aviso de pagamento do banco por **webhook assinado** e dá baixa sozinho, tratando pagamento
**a menor, a maior, em duplicidade e em atraso**.

> O banco e o ERP são simulados (`app/clients.py`). Nenhum dado é real. Em produção, `FakeItauCobranca`
> vira o cliente da API de cobrança do Itaú e `FakeErp` o cliente da API do ERP.

## Regras de conciliação (`app/reconcile.py`)

| Situação | Resultado | O que acontece |
|---|---|---|
| Valor exato | `PAID` | Baixa total no ERP |
| Valor menor | `PARTIAL` | Baixa parcial; informa quanto falta; o próximo pagamento complementa |
| Valor maior | `OVERPAID` | Quita, registra o crédito e manda para a fila do financeiro |
| Segundo pagamento depois de quitado | `DUPLICATE` | **Não** dá baixa de novo; fila para devolução |
| Depois do vencimento | multa 2% + juros 1% a.m. pro rata | O valor devido é recalculado na data do pagamento |
| Referência desconhecida | `UNMATCHED` | Nunca é descartado; vai para a fila de exceções |

## Segurança e robustez do webhook

- Assinatura HMAC-SHA256 sobre o corpo bruto, comparada em tempo constante (`401` se inválida).
- `event_id` é chave primária: o banco reenviar o mesmo aviso não gera baixa em dobro.
- Evento e atualização da cobrança gravados na mesma transação.
- Se o ERP estiver fora, a baixa fica `PENDING` e é reprocessada depois (`POST /erp/retry`).
- Valores em centavos inteiros; arredondamento com `Decimal`.

Cada linha acima tem teste em `tests/test_flow.py` (11 testes), incluindo o vetor oficial do CRC16 do Pix.

## Como rodar

```bash
pip install -r requirements.txt
python -m pytest -q
uvicorn app.main:app --reload   # http://127.0.0.1:8000/docs
```

## Fora do escopo desta demonstração (e como seria em produção)

Autenticação do painel; certificados e segredos em cofre; PostgreSQL; fila de reprocessamento com alertas;
cancelamento e baixa por data útil; cálculo de feriados; tela web; LGPD e retenção de dados.
