# Pure Electric — Desafio Técnico Full Stack

API que recebe pedidos de canais externos via webhook, garante que o mesmo pedido
não seja processado duas vezes, encaminha o processamento de forma assíncrona para
um sistema interno simulado e expõe a consulta dos pedidos e seus status.

```
RECEIVED ──► PROCESSING ──► PROCESSED
                  │
                  └──────► FAILED
```

---

## 1. Stack e decisões técnicas

| Camada                 | Escolha                            | Por quê                                                                                                                                                                                                                                                        |
| ---------------------- | ---------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Linguagem              | **Python 3.14**                    | Linguagem de propósito geral com ecossistema maduro para web, filas e dados, e alta oferta de mão de obra. O modelo assíncrono (`asyncio`) atende bem um fluxo I/O-bound como este.                                                                            |
| Framework              | **FastAPI**                        | ASGI com `async` nativo, validação de payload via Pydantic v2 e OpenAPI gerado automaticamente — cobre o item "documentação OpenAPI" sem código extra. A injeção de dependência (`Depends`) permitiu trocar a sessão do banco nos testes sem alterar as rotas. |
| Gerenciador de pacotes | **uv**                             | Resolução e instalação rápidas, `uv.lock` determinístico e aderência a PEP 621, o que torna simples migrar para outro gerenciador depois.                                                                                                                      |
| Banco                  | **PostgreSQL 18**                  | O fluxo depende de transações confiáveis e de uma restrição `UNIQUE` como última linha de defesa da idempotência. Entrega ACID, `SELECT ... FOR UPDATE`, `UUID` nativo (usado como PK) e enum nativo.                                                          |
| ORM / migrações        | **SQLAlchemy 2.0 async + Alembic** | Estilo tipado (`Mapped[...]`), sessão assíncrona com `asyncpg` e migrações versionadas com numeração sequencial.                                                                                                                                               |
| Mensageria             | **RabbitMQ 4 (AMQP)**              | Desacopla o recebimento HTTP do processamento. Filas `durable` + mensagens `PERSISTENT` sobrevivem a reinício do broker, o ack é por mensagem (`message.process()`) e o broker é o caminho natural para os itens bônus de retry e dead-letter queue.           |
| Testes                 | **pytest + pytest-asyncio**        | Suíte contra SQLite em memória, sem depender de Postgres nem de RabbitMQ (o `publish` é mockado em `tests/conftest.py`).                                                                                                                                       |
| Lint / format          | **ruff**                           | Lint e formatação em uma ferramenta só, com configuração no `pyproject.toml`.                                                                                                                                                                                  |
| Frontend               | **React + Vite**                   | Interface de compra e consulta de pedidos com atualização periódica (repositório `pure_electric_frontend`).                                                                                                                                                    |

**Dependência entre camadas:** `routes → repositories → models`. `schemas` e `utils`
são importados onde necessário. Nenhuma rota escreve SQL, nenhum repository conhece
HTTP além de levantar `HTTPException` no status correto.

---

## 2. Estrutura

```
main.py            App FastAPI, lifespan com seeding de usuários e produtos, mount de /images, registro dos routers
routes/            Camada HTTP chama o repository include_router.py - Tupla ROUTERS — único ponto de registro de routers
repositories/      Acesso a dados e regras transacionais e HTTPException
models/            ORM SQLAlchemy 2.0 (Mapped[...] + mapped_column)
schemas/           Pydantic v2 (Create / Update / Out)
enums/             StrEnum compartilhado (OrderStatus, DocumentType)
queues/            Conexão e publish no RabbitMQ
workers/           Consumidor da fila de pedidos (claim, retry, estados finais)
integrations/      Sistema interno simulado (separado da API e do worker)
databases/         Engine e sessão async do Postgres
seeders/           Dados iniciais (users e products), idempotentes por chave natural
utils/             Logger JSON estruturado e hash de senha (bcrypt)
images/            Imagens do catálogo servidas em /images (StaticFiles)
run_worker.py      Entry point do worker (processo separado da API)
migrations/        Alembic (env async) + versões numeradas 0001_, 0002_ ...
tests/             Suíte pytest com SQLite em memória
```

---

## 3. Como rodar

**Pré-requisitos:** [uv](https://docs.astral.sh/uv/) e Docker.

```bash
cp .env.example .env          # preencha as credenciais
docker compose up -d
uv sync
uv run alembic upgrade head
```

**API** (terminal 1):

```bash
uv run fastapi dev main.py --host=0.0.0.0
# Documentação interativa: http://0.0.0.0:8000/docs
```

**Worker** (terminal 2 — processo separado, é ele que consome a fila):

```bash
uv run python run_worker.py
```

**Testes, lint e formatação:**

```bash
uv run pytest
uv run ruff check . --exclude .venv --exclude migrations
uv run ruff format . --exclude .venv --exclude migrations --check
```

### Detalhes de infraestrutura

- `docker compose up -d` sobe Postgres (`db`), RabbitMQ (`queue`) e a stack de
  observabilidade — Prometheus, Loki, Promtail e Grafana.
- O Postgres do compose é publicado na porta **5433** (e não 5432) para não
  conflitar com um Postgres local. Ajuste `DATABASE_PORT` no `.env`.
- RabbitMQ: `5672` (AMQP) e `15672` (management UI, `http://localhost:15672`).
- No startup a API executa os seeders: insere 5 usuários e 7 produtos fixos se ainda
  não existirem (produtos são identificados pelo `name`). Se o banco estiver indisponível, o erro é logado e a API sobe mesmo assim.

### Solução de problemas

**O pedido fica em `RECEIVED` e nunca muda de status.**

A causa mais comum é o worker não estar rodando. **API e worker são processos
separados**: subir a API não sobe o worker. Dá para confirmar pela fila:

```bash
docker exec pure_electric_queue rabbitmqctl list_queues \
  name messages_ready messages_unacknowledged consumers
```

| Saída                                  | Significado                                                                                                                                                         |
| -------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `messages_ready > 0` e `consumers = 0` | As mensagens foram publicadas, mas ninguém está consumindo. Basta subir o worker — elas continuam na fila e são processadas.                                        |
| `messages_ready = 0` e `consumers = 0` | Fila vazia e worker fora do ar. Se o pedido segue `RECEIVED`, a mensagem já foi consumida e descartada; use `POST /orders/{id}/reprocess` para recolocá-lo na fila. |
| `consumers >= 1`                       | O worker está conectado; confira o log dele.                                                                                                                        |

Também é possível inspecionar em `http://localhost:15672` → Queues → `orders`,
olhando as colunas _Ready_ e _Consumers_.

Duas observações sobre o worker:

- Ele é um processo de vida longa. Imprime `Worker listening on queue: orders` uma
  única vez e depois fica bloqueado aguardando mensagens (`await asyncio.Future()`
  no fim de `start_worker`). **Não aparecer log novo não é sinal de travamento** —
  só encerre com `Ctrl+C` quando quiser parar.
- O worker precisa enxergar o mesmo `.env` e o mesmo banco que a API, senão
  encontrará o pedido como inexistente e descartará a mensagem com
  `Order not claimable (missing or already handled), skipping`.

E um alerta sobre esse log: `Order not claimable (missing or already handled), skipping` significa que a mensagem foi
**confirmada e descartada** — não há dead-letter queue. Um caso concreto: a fila é
`durable`, então ela sobrevive a um `alembic downgrade base` (que derruba a tabela
`orders`), e as mensagens seguem apontando para pedidos que não existem mais. Nada
quebra, mas o pedido nunca vai ser processado e não há rastro além do log.

---

## 4. API

| Método                  | Rota                           | Descrição                                                                                                                                                                                                                                                   |
| ----------------------- | ------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `POST`                  | `/orders`                      | Recebe um pedido. **201** se criado, **200** se o `external_id` já existia.                                                                                                                                                                                 |
| `GET`                   | `/orders`                      | Página de pedidos, mais recentes primeiro. Query: `status` (opcional, repetível: `?status=RECEIVED&status=PROCESSING`), `limit` (1–200, padrão 50) e `offset` (≥ 0). Resposta: `{items, total, limit, offset}`; `total` é a contagem com o filtro aplicado. |
| `GET`                   | `/orders/{order_id}`           | Consulta um pedido por ID. **404** se não existir.                                                                                                                                                                                                          |
| `POST`                  | `/orders/{order_id}/reprocess` | Recoloca um pedido `FAILED` (ou `RECEIVED` preso) na fila. **202**; **409** em qualquer outro status; **404** se não existir.                                                                                                                               |
| `GET/POST/PATCH/DELETE` | `/users`, `/users/{id}`        | CRUD de usuários (senha sempre em bcrypt, nunca retornada).                                                                                                                                                                                                 |
| `GET/POST/PATCH/DELETE` | `/products`, `/products/{id}`  | CRUD do catálogo de produtos. O campo `image` guarda o caminho relativo (ex.: `/images/escape.webp`).                                                                                                                                                       |
| `GET`                   | `/images/{arquivo}`            | Imagem estática do produto (arquivos de `images/`).                                                                                                                                                                                                         |

### Receber um pedido

```bash
curl -X POST http://127.0.0.1:8000/orders \
  -H 'Content-Type: application/json' \
  -d '{"external_id": "ORDER-123", "customer": "Cliente Exemplo", "amount": 150.00}'
```

```json
{
  "id": "01a0c10d-f7dd-761e-8515-db704200d232",
  "external_id": "ORDER-123",
  "customer": "Cliente Exemplo",
  "amount": "150.00",
  "status": "RECEIVED",
  "failure_reason": null,
  "created_at": "2026-09-20T23:11:51.123456Z",
  "processed_at": null
}
```

Enviar o mesmo `external_id` novamente devolve **200** com o pedido original. O
payload novo é ignorado de propósito: o pedido de referência já existe e não é
sobrescrito por uma reentrega.

> **Formato dos campos:** o `external_id` é aceito tanto em snake_case quanto em
> camelCase (`externalId`), como no payload do enunciado. As respostas sempre saem
> em snake_case. Na documentação interativa (`/docs`) o campo aparece como
> `externalId`.

### Consultar status

```bash
curl http://127.0.0.1:8000/orders/01a0c10d-f7dd-761e-8515-db704200d232
```

---

## 5. Fluxo de processamento

```
POST /orders
   │
   ├─ valida payload (Pydantic)              → 422 se inválido
   ├─ SELECT ... FOR UPDATE por external_id
   │     ├─ existe → 200 (payload ignorado, nada é enfileirado)
   │     └─ não existe → INSERT status=RECEIVED → 201
   ├─ publish na fila "orders" (durable, PERSISTENT)
   │     └─ broker fora → loga, pedido segue RECEIVED, resposta 201
   ▼
worker (run_worker.py)
   ├─ claim atômico: UPDATE ... SET PROCESSING WHERE status = RECEIVED
   │     └─ 0 linhas (não existe / já reivindicado / já finalizado) → ack e ignora
   ├─ integrations.internal_system.send_order, com timeout
   │     ├─ sucesso                   → PROCESSED
   │     ├─ rejeição (permanente)     → FAILED, sem retry
   │     ├─ timeout / indisponível    → retry com backoff exponencial (1s, 2s...)
   │     │                              esgotadas as tentativas → FAILED
   │     └─ exceção inesperada        → FAILED (motivo em failure_reason)
   ▼
POST /orders/{id}/reprocess: FAILED → RECEIVED e novo publish
```

A mensagem trafega com `id`, `externalId`, `customer`, `amount`, `status` e
`createdAt`. O worker **não** confia no status da mensagem: a decisão é tomada pelo
claim atômico no banco, porque a fila pode reentregar mensagens.

### Sistema interno simulado

`integrations/internal_system.py` é separado da API e do worker (o worker só o
enxerga por `send_order` e pelas exceções `InternalSystemRejected` e
`InternalSystemUnavailable`). O comportamento é reproduzível pelo `externalId`:

| `externalId` contém | Resultado                                                                                                                                  |
| ------------------- | ------------------------------------------------------------------------------------------------------------------------------------------ |
| `FAIL`              | rejeitado → `FAILED` imediato                                                                                                              |
| `DOWN`              | indisponível → retries → `FAILED`                                                                                                          |
| `SLOW`              | nunca responde → timeout → retries → `FAILED`                                                                                              |
| qualquer outro      | sucesso, ou rejeição aleatória com probabilidade `INTERNAL_SYSTEM_FAILURE_RATE` (padrão 0.3, usado na demo pela interface, que gera UUIDs) |

```bash
curl -X POST localhost:8000/orders -H 'Content-Type: application/json' \
  -d '{"externalId": "ORDER-FAIL-1", "customer": "Cliente", "amount": 10}'
```

Configuração (env): `PAYMENT_MOCK_DELAY_SECONDS` (latência simulada),
`INTERNAL_SYSTEM_FAILURE_RATE`, `INTERNAL_SYSTEM_TIMEOUT_SECONDS` (10),
`INTERNAL_SYSTEM_MAX_ATTEMPTS` (3), `INTERNAL_SYSTEM_BACKOFF_SECONDS` (1) e
`WORKER_PREFETCH` (5).

---

## 6. Idempotência

Como garantir que o mesmo `externalId` não seja processado duas vezes — três
camadas independentes:

1. **Restrição `UNIQUE` em `orders.external_id`.** É a garantia final. Mesmo que
   toda a lógica de aplicação falhe, o banco recusa a segunda linha. O índice é
   criado na migração `0002`.
2. **`SELECT ... FOR UPDATE` antes do `INSERT`** (`OrderRepository.create_idempotent`).
   Serializa as requisições concorrentes que disputam o mesmo `external_id`:
   quando a linha já existe, a segunda requisição espera o lock e cai no caminho
   "já existe" em vez de duplicar o pedido.
3. **Claim atômico no worker** (`claim_for_processing`): um único
   `UPDATE ... SET status='PROCESSING' WHERE status='RECEIVED'`. Se a mesma
   mensagem chegar duas vezes (reentrega do broker, reprocessamento, dois
   consumidores simultâneos), só uma execução afeta 1 linha e chama o sistema
   interno; as demais são ignoradas. Coberto por teste com dois consumidores
   concorrentes.

---

## 7. Indisponibilidade e lentidão do sistema interno

- **Lentidão:** a chamada tem timeout (`asyncio.wait_for`,
  `INTERNAL_SYSTEM_TIMEOUT_SECONDS`). Um sistema que não responde não prende o
  worker: estourado o tempo, conta como falha transitória.
- **Indisponibilidade / timeout:** retry com backoff exponencial até
  `INTERNAL_SYSTEM_MAX_ATTEMPTS`. Se o sistema voltar no meio, o pedido termina
  `PROCESSED`; se não, `FAILED` com o motivo em `failure_reason`.
- **Rejeição de negócio:** permanente, vai direto a `FAILED` sem retry.
- **Recuperação:** `POST /orders/{id}/reprocess` devolve `FAILED` para `RECEIVED` e
  republica; o claim atômico impede processamento em duplicidade.
- **Broker fora no momento do publish:** o pedido já está gravado; o erro é logado,
  a API responde 201 e o pedido fica `RECEIVED`, recuperável via `/reprocess`.
- **Broker reiniciando entre publish e consumo:** fila `durable` + mensagem
  `PERSISTENT` sobrevivem ao reinício.
- **Exceção inesperada no worker:** logada, pedido marcado `FAILED`.
- **Limitações assumidas:** o backoff é feito em memória no próprio consumidor (ocupa
  um slot de prefetch durante a espera); se o worker morrer entre `PROCESSING` e o
  estado final, o pedido fica preso em `PROCESSING` (ver pendências).

---

## 8. Evolução da arquitetura (múltiplos sistemas externos)

Para integrar ERP, transportadora e gateway de pagamento de forma independente:

1. **Uma fila e um worker por integração** (`erp`, `shipping`,
   `payment`), cada uma com sua própria dead-letter queue. Um sistema lento
   deixa de bloquear os outros, porque hoje existe uma fila única.
2. **Cada integração como um cliente HTTP com contrato próprio:** timeout
   explícito, retry com backoff exponencial, jitter e circuit breaker. O mock atual
   viraria um serviço separado, de fato desacoplado.
3. **Idempotência na saída:** chave de idempotência por integração, para que o
   sistema externo também saiba deduplicar o envio.
4. **Observabilidade:** métricas (Prometheus) e busca de logs (Loki) já existem
   nesta entrega como bônus, para múltiplos sistemas faltaria métricas
   por fila.
5. **Escala:** múltiplos consumidores por fila com prefetch ajustado.
6. **Versionamento do payload** (campo `schema_version`) para que
   integrações antigas continuem funcionando durante a migração.

---

## 9. Trade-offs (limite de 4 horas)

O que foi deliberadamente simplificado:

- **Sistema interno como módulo no mesmo processo** (`integrations/internal_system.py`)
  em vez de serviço HTTP separado. A fronteira é a mesma (função assíncrona e
  exceções tipadas), então trocá-lo por um cliente HTTP não altera o worker.
- **Retry em memória** (backoff dentro do consumidor) em vez de filas de retry com
  TTL. Os dados não sobrevivem a queda do worker e ocupa o consumidor.
- **Sem dead-letter queue.** Mensagem que causa erro fora do fluxo tratado é
  descartada pelo broker.
- **Sem autenticação.** O webhook não valida assinatura e não há auth nos
  endpoints de consulta e CRUD.
- **Sem Makefile/justfile.** Comandos diretos com `uv`, `docker compose` e `alembic`.
- **Sem contador de tentativas persistido:** o número de tentativas só aparece nos
  logs, não no pedido.

---

## 10. Pendências

O que ficou de fora e como seria implementado:

| #   | Pendência                               | Como implementaria                                                                                                                                                             |
| --- | --------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| 1   | Dual-write entre commit e publish       | Padrão **outbox**: gravar o evento numa tabela `outbox` na mesma transação do pedido e um publisher separado lê e publica. Hoje mitigado por `/reprocess`, mas não automático. |
| 2   | Retry durável e dead-letter queue       | Fila `orders.retry` com TTL e `x-dead-letter-exchange` em vez do backoff em memória; após N tentativas, DLQ com alerta.                                                        |
| 3   | Pedido preso em `PROCESSING`            | Coluna `processing_started_at` como lease e job periódico que devolve para a fila pedidos em `PROCESSING` há mais de X minutos.                                                |
| 4   | Circuit breaker                         | Abrir o circuito após falhas consecutivas para não gastar tentativas contra um sistema fora do ar.                                                                             |
| 5   | Downgrade das migrações                 | Os enums nativos (`order_status`, `document_type`) não são removidos no `downgrade`; corrigir com `sa.Enum(..., name=...).drop(op.get_bind())`.                                |
| 6   | Correlação nos logs                     | Os logs são JSON com `external_id` em `extra`; falta propagar um `correlation_id` por `contextvars` para toda linha da requisição e do worker.                                 |
| 7   | Reuso do canal do RabbitMQ              | `get_channel()` abre uma conexão nova a cada `publish` e não fecha; manter um canal compartilhado.                                                                             |
| 8   | Autenticação e proteção de dados        | HMAC no webhook, auth nos endpoints de consulta, mascaramento de CPF/CNPJ.                                                                                                     |
| 9   | Testes de contrato e de integração real | Contrato do envelope da mensagem; testes contra Postgres/RabbitMQ reais (a suíte usa SQLite e mocka o publish).                                                                |

---

## 11. Uso de Inteligência Artificial

**Ferramenta utilizada:** assistente de IA com agente de código em CLI.

**Onde houve apoio:**

- Redação do README.md.
- Redação de docstrings e dos schemas Pydantic.
- Mapeamento de models para migrations.
- Mudança de convensão de hash migration para order migration.
- Revisão de código para descobrir possíveis falhas.
- Escrita de testes.
- Realização de trabalho braçal com ideias bem explicitadas pelo PROMPT.

**Como o código foi revisado e validado:**

- Leitura linha a linha do código gerado, com as decisões de arquitetura descritas
  nas seções acima sendo de escolha própria, não da ferramenta.
- `uv run pytest` - Criação de 55 testes abordando as principais partes do sistema para garantir robustez.
- Utilização de `uv run ruff check` e `uv run ruff format --check` para validar linter do código e possíveis warnings/erros que possa vir apresentar.
- `uv run alembic check` — nenhuma divergência entre os models e o schema do banco.
- Execução manual do fluxo completo com Postgres e RabbitMQ reais: `POST /orders`
  (201, `RECEIVED`) → worker consumindo → `GET /orders/{id}` com status final
  `PROCESSED`/`FAILED`, `processed_at` carimbado e `failure_reason` preenchido só no
  `FAILED`; reenvio do mesmo `external_id` retornando 200 com o pedido original.
- Payload inválido retornando 422.
- Stack de observabilidade validada de ponta a ponta com os serviços reais.

---

## 12. Deploy (Railway)

O backend sobe no **Railway** como dois serviços do mesmo repositório (API e worker),
mais Postgres, RabbitMQ e stack de observabilidade. O `Dockerfile` da raiz é usado pelos dois serviços do backend.

---

### O que ficou de fora (mesmo como bônus)

- **Tracing distribuído.** Dá pra ver "quanto tempo a API levou" e "quanto tempo o
  worker levou" separadamente, mas não um único trace amarrando request →
  publish → consumo → chamada ao sistema interno. Precisaria de OpenTelemetry e um
  correlation/trace id propagado pela mensagem da fila (relacionado à pendência #7
  da seção 10).
- **Alerting.** Sem regras do Prometheus/Alertmanager; hoje é só visualização sob
  demanda.
- **Métricas de infraestrutura** (RabbitMQ, Postgres): daria para ligar o plugin
  `rabbitmq_prometheus` e um `postgres_exporter`, mas fora do escopo do bônus.
