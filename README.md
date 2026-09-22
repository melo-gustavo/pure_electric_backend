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

| Camada | Escolha | Por quê |
|---|---|---|
| Linguagem | **Python 3.14** | Linguagem de propósito geral com ecossistema maduro para web, filas e dados, e alta oferta de mão de obra. O modelo assíncrono (`asyncio`) atende bem um fluxo I/O-bound como este. |
| Framework | **FastAPI** | ASGI com `async` nativo, validação de payload via Pydantic v2 e OpenAPI gerado automaticamente — cobre o item "documentação OpenAPI" sem código extra. A injeção de dependência (`Depends`) permitiu trocar a sessão do banco nos testes sem alterar as rotas. |
| Gerenciador de pacotes | **uv** | Resolução e instalação rápidas, `uv.lock` determinístico e aderência a PEP 621, o que torna simples migrar para outro gerenciador depois. |
| Banco | **PostgreSQL 18** | O fluxo depende de transações confiáveis e de uma restrição `UNIQUE` como última linha de defesa da idempotência. Entrega ACID, `SELECT ... FOR UPDATE`, `UUID` nativo (usado como PK) e enum nativo. |
| ORM / migrações | **SQLAlchemy 2.0 async + Alembic** | Estilo tipado (`Mapped[...]`), sessão assíncrona com `asyncpg` e migrações versionadas com numeração sequencial. |
| Mensageria | **RabbitMQ 4 (AMQP)** | Desacopla o recebimento HTTP do processamento. Filas `durable` + mensagens `PERSISTENT` sobrevivem a reinício do broker, o ack é por mensagem (`message.process()`) e o broker é o caminho natural para os itens bônus de retry e dead-letter queue. |
| Testes | **pytest + pytest-asyncio** | Suíte contra SQLite em memória, sem depender de Postgres nem de RabbitMQ (o `publish` é mockado em `tests/conftest.py`). |
| Lint / format | **ruff** | Lint e formatação em uma ferramenta só, com configuração no `pyproject.toml`. |
| Frontend | **React + Vite** | Interface de compra e consulta de pedidos com atualização periódica (repositório `pure_electric_frontend`). |

**Dependência entre camadas:** `routes → repositories → models`. `schemas` e `utils`
são importados onde necessário. Nenhuma rota escreve SQL, nenhum repository conhece
HTTP além de levantar `HTTPException` no status correto.

---

## 2. Estrutura

```
main.py            App FastAPI, lifespan com seeding de usuários e produtos, mount de /images, registro dos routers
routes/            Camada HTTP (fina): Depends(get_session), chama o repository
  include_router.py  Tupla ROUTERS — único ponto de registro de routers
repositories/      Acesso a dados e regras transacionais; HTTPException (404/409) mora aqui
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
uv run fastapi dev main.py
# Documentação interativa: http://127.0.0.1:8000/docs
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
  observabilidade — Prometheus, Loki, Promtail e Grafana (bônus, seção 13).
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

| Saída | Significado |
|---|---|
| `messages_ready > 0` e `consumers = 0` | As mensagens foram publicadas, mas ninguém está consumindo. Basta subir o worker — elas continuam na fila e são processadas. |
| `messages_ready = 0` e `consumers = 0` | Fila vazia e worker fora do ar. Se o pedido segue `RECEIVED`, a mensagem já foi consumida e descartada; use `POST /orders/{id}/reprocess` para recolocá-lo na fila. |
| `consumers >= 1` | O worker está conectado; confira o log dele. |

Também é possível inspecionar em `http://localhost:15672` → Queues → `orders`,
olhando as colunas *Ready* e *Consumers*.

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

| Método | Rota | Descrição |
|---|---|---|
| `POST` | `/orders` | Recebe um pedido. **201** se criado, **200** se o `external_id` já existia. |
| `GET` | `/orders` | Página de pedidos, mais recentes primeiro. Query: `status` (opcional, repetível: `?status=RECEIVED&status=PROCESSING`), `limit` (1–200, padrão 50) e `offset` (≥ 0). Resposta: `{items, total, limit, offset}`; `total` é a contagem com o filtro aplicado. |
| `GET` | `/orders/{order_id}` | Consulta um pedido por ID. **404** se não existir. |
| `POST` | `/orders/{order_id}/reprocess` | Recoloca um pedido `FAILED` (ou `RECEIVED` preso) na fila. **202**; **409** em qualquer outro status; **404** se não existir. |
| `GET/POST/PATCH/DELETE` | `/users`, `/users/{id}` | CRUD de usuários (senha sempre em bcrypt, nunca retornada). |
| `GET/POST/PATCH/DELETE` | `/products`, `/products/{id}` | CRUD do catálogo de produtos. O campo `image` guarda o caminho relativo (ex.: `/images/escape.webp`). |
| `GET` | `/images/{arquivo}` | Imagem estática do produto (arquivos de `images/`). |

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

| `externalId` contém | Resultado |
|---|---|
| `FAIL` | rejeitado → `FAILED` imediato |
| `DOWN` | indisponível → retries → `FAILED` |
| `SLOW` | nunca responde → timeout → retries → `FAILED` |
| qualquer outro | sucesso, ou rejeição aleatória com probabilidade `INTERNAL_SYSTEM_FAILURE_RATE` (padrão 0.3, usado na demo pela interface, que gera UUIDs) |

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

**Semântica do endpoint:** `201` quando o pedido é criado e enfileirado, `200`
quando já existia. Nunca `409` — reentrega é um cenário esperado, não um erro do
cliente.

**Corrida no INSERT:** `SELECT ... FOR UPDATE` não bloqueia nada quando a linha
*ainda não existe* — não há linha para travar. Duas requisições simultâneas com um
`external_id` novo passam as duas pelo SELECT e chegam as duas ao INSERT; nesse
caso quem decide é a restrição `UNIQUE`. O `IntegrityError` resultante é capturado:
a sessão faz `rollback` e o pedido vencedor é relido e devolvido com **200**, em vez
de propagar um erro 500. Verificado com duas sessões reais contra o Postgres,
forçando as duas a passar pelo SELECT antes de qualquer INSERT: um vencedor com
`201`, um perdedor com `200`, um único registro no banco.

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

1. **Uma fila e um worker por integração** (`orders.erp`, `orders.shipping`,
   `orders.payment`), cada uma com sua própria dead-letter queue. Um sistema lento
   deixa de bloquear os outros, porque hoje existe uma fila única.
2. **Status por etapa em vez de um status global.** Uma tabela de eventos/etapas
   (`order_events` ou `order_integrations`) guardando o estado de cada destino; o
   `status` do pedido passa a ser derivado (ex.: `PROCESSED` só quando todas as
   etapas obrigatórias concluírem).
3. **Cada integração como um cliente HTTP com contrato próprio:** timeout
   explícito, retry com backoff exponencial, jitter e circuit breaker. O mock atual
   viraria um serviço separado, de fato desacoplado.
4. **Idempotência na saída:** chave de idempotência por integração, para que o
   sistema externo também saiba deduplicar o envio.
5. **Padrão outbox** para publicar eventos na mesma transação do estado, eliminando
   a janela entre commit e publish.
6. **Observabilidade:** métricas (Prometheus) e busca de logs (Loki) já existem
   nesta entrega como bônus (seção 13); para múltiplos sistemas faltaria métricas
   por fila (profundidade, taxa de falha, latência por integração) e tracing
   distribuído com um correlation id propagado por toda a cadeia, hoje limitado ao
   `external_id` em cada log isolado.
7. **Escala:** múltiplos consumidores por fila com prefetch ajustado. Para preservar
   ordem por pedido, particionar por `external_id` (hash exchange) ou usar filas
   shardeadas.
8. **Versionamento do payload** (campo `schema_version` no envelope) para que
   integrações antigas continuem funcionando durante a migração.

---

## 9. Trade-offs (limite de 4 horas)

O que foi deliberadamente simplificado:

- **Sistema interno como módulo no mesmo processo** (`integrations/internal_system.py`)
  em vez de serviço HTTP separado. A fronteira é a mesma (função assíncrona e
  exceções tipadas), então trocá-lo por um cliente HTTP não altera o worker.
- **Retry em memória** (backoff dentro do consumidor) em vez de filas de retry com
  TTL. Simples e testável; não sobrevive a queda do worker e ocupa o consumidor.
- **Sem dead-letter queue.** Mensagem que causa erro fora do fluxo tratado é
  descartada pelo broker.
- **Sem autenticação.** O webhook não valida assinatura/HMAC e não há auth nos
  endpoints de consulta e CRUD.
- **Paginação por offset** em `GET /orders` (simples, mas pode pular/repetir itens se houver inserções entre páginas e fica mais lenta em offsets muito altos; a ordenação `created_at, id` e o índice `(status, created_at)` mitigam).
- **Sem Makefile/justfile.** Comandos diretos com `uv`, `docker compose` e `alembic`.
- **Docker Compose para infraestrutura (Postgres, RabbitMQ, e a stack de
  observabilidade — seção 13);** API e worker continuam rodando via `uv` (o
  `Dockerfile` é usado no deploy).
- **Modelo de itens do pedido removido.** Chegou a existir `OrderItem`, mas nenhum
  endpoint criava itens e o payload do desafio não os tem; foi removido em vez de
  manter estrutura sem uso.
- **Sem contador de tentativas persistido:** o número de tentativas só aparece nos
  logs, não no pedido.

---

## 10. Pendências

O que ficou de fora e como seria implementado:

| # | Pendência | Como implementaria |
|---|---|---|
| 1 | Dual-write entre commit e publish | Padrão **outbox**: gravar o evento numa tabela `outbox` na mesma transação do pedido e um publisher separado lê e publica. Hoje mitigado por `/reprocess`, mas não automático. |
| 2 | Retry durável e dead-letter queue | Fila `orders.retry` com TTL e `x-dead-letter-exchange` em vez do backoff em memória; após N tentativas, DLQ com alerta. |
| 3 | Pedido preso em `PROCESSING` | Coluna `processing_started_at` como lease e job periódico que devolve para a fila pedidos em `PROCESSING` há mais de X minutos. |
| 4 | Circuit breaker | Abrir o circuito após falhas consecutivas para não gastar tentativas contra um sistema fora do ar. |
| 5 | Paginação por cursor | Trocar `offset` por keyset (`created_at, id` do último item) para páginas estáveis e custo constante em volumes grandes. |
| 6 | Downgrade das migrações | Os enums nativos (`order_status`, `document_type`) não são removidos no `downgrade`; corrigir com `sa.Enum(..., name=...).drop(op.get_bind())`. |
| 7 | Correlação nos logs | Os logs são JSON com `external_id` em `extra`; falta propagar um `correlation_id` por `contextvars` para toda linha da requisição e do worker. |
| 8 | Reuso do canal do RabbitMQ | `get_channel()` abre uma conexão nova a cada `publish` e não fecha; manter um canal compartilhado. |
| 9 | Autenticação e proteção de dados | HMAC no webhook, auth nos endpoints de consulta, mascaramento de CPF/CNPJ. |
| 10 | Testes de contrato e de integração real | Contrato do envelope da mensagem; testes contra Postgres/RabbitMQ reais (a suíte usa SQLite e mocka o publish). |

---

## 11. Uso de Inteligência Artificial

**Ferramenta utilizada:** assistente de IA com agente de código em CLI.

**Onde houve apoio:**

- Estruturação inicial do projeto e do layout de pastas (models, schemas,
  repositories, routes, workers).
- Redação de docstrings e dos schemas Pydantic.
- Revisão crítica do código: identificação do `OrderItem` sem uso, do
  `lazy load` que estouraria `MissingGreenlet` em `OrderItemOut.product`, da
  corrida de `INSERT` na idempotência, do dual-write entre commit e publish e da
  migração com downgrade inválido.
- Implementação do bônus de observabilidade (seção 13): instrumentação
  Prometheus na API/worker, contadores de negócio (`utils/metrics.py`), log em
  arquivo para o Promtail (`utils/logger.py`) e a stack Prometheus + Loki +
  Promtail + Grafana no `docker-compose.yml`, incluindo o dashboard provisionado.
- Correção de erros do `basedpyright` (`reportArgumentType` na conversão
  `Order` → `OrderOut`, `Decimal` vs `str` em um teste, `dict`/`async_sessionmaker`
  sem parâmetros genéricos) apontados pelo editor do candidato durante a sessão.
- Diagnóstico e correção de um loop de log introduzido pela própria observabilidade:
  com `fastapi dev` (hot-reload) e log em arquivo ligados juntos, cada linha
  gravada em `logs/app.jsonl` era detectada pelo `watchfiles` como mudança,
  logada, gravada de novo, detectada de novo — loop infinito sem reiniciar o
  processo. Reproduzido isoladamente antes de corrigir, para confirmar a causa em
  vez de arriscar (ver nota na seção 13).
- Apoio na redação deste README.

**Como o código foi revisado e validado:**

- `uv run pytest` — 55 testes passando, sem depender de serviços externos (CRUD, idempotência incluindo a recuperação
  do `IntegrityError`, formatos de payload, 404/422, filtro por status, reprocessamento, broker fora do ar e o worker: sucesso, rejeição, retry com recuperação, esgotamento, timeout, mensagem duplicada e consumidores concorrentes).
- `uv run ruff check` e `uv run ruff format --check` sem pendências.
- `uv run alembic check` — nenhuma divergência entre os models e o schema do banco.
- `uv run basedpyright .` — 0 erros (avisos remanescentes são de modo estrito em
  código de terceiros/fixtures de teste, não corrigidos por estarem fora do escopo
  do que foi reportado).
- Stack de observabilidade validada de ponta a ponta com os serviços reais: pedido
  criado duas vezes refletindo em `orders_received_total=1` e
  `orders_duplicate_total=1` na API, `orders_processed_total{status="PROCESSED"}=1`
  no worker, e as mesmas linhas de log aparecendo em uma consulta ao Loki
  (`{job="pure_electric_app"}`).
- Execução manual do fluxo completo com Postgres e RabbitMQ reais: `POST /orders`
  (201, `RECEIVED`) → worker consumindo → `GET /orders/{id}` com status final
  `PROCESSED`/`FAILED`, `processed_at` carimbado e `failure_reason` preenchido só no
  `FAILED`; reenvio do mesmo `external_id` retornando 200 com o pedido original;
  payload inválido retornando 422.
- Verificação da corrida de idempotência com duas sessões concorrentes contra o
  Postgres, sincronizadas para passar as duas pelo `SELECT` antes de qualquer
  `INSERT`: um vencedor com 201, um perdedor com 200 e um único registro no banco.
  A primeira tentativa, com 10 requisições HTTP concorrentes, **não** reproduziu a
  corrida (o event loop serializou as requisições), o que foi confirmado pela
  ausência do log de recuperação — a verificação só passou a valer depois de forçar
  o interleaving na camada de sessão.
- Leitura linha a linha do código gerado, com as decisões de arquitetura descritas
  nas seções acima sendo de escolha própria, não da ferramenta.

---

## 12. Deploy (Railway + Vercel)

O backend sobe no **Railway** como dois serviços do mesmo repositório (API e worker),
mais Postgres e RabbitMQ; o frontend sobe na **Vercel**. O `Dockerfile` da raiz é usado
pelos dois serviços do backend.

**Railway** (projeto único):

1. **Postgres:** *New → Database → PostgreSQL*.
2. **RabbitMQ:** *New → Docker Image* com `rabbitmq:4-management-alpine` e as variáveis
   `RABBITMQ_DEFAULT_USER`, `RABBITMQ_DEFAULT_PASS` e `RABBITMQ_DEFAULT_VHOST=/`. Não
   exponha porta pública; a API e o worker falam com ele pela rede privada
   (`<nome-do-serviço>.railway.internal:5672`).
3. **API:** *New → GitHub Repo* (este repositório). Sem alterar o start command: o
   `CMD` do Dockerfile roda `alembic upgrade head` e depois o `fastapi run`. Em
   *Settings → Networking* gere um domínio público.
4. **Worker:** outro serviço do mesmo repositório, com *Settings → Deploy → Custom Start
   Command* `python run_worker.py` e **sem** domínio público.
5. **Variáveis** (nos dois serviços):

   | Variável | Valor |
   |---|---|
   | `DATABASE_USER` | `${{Postgres.PGUSER}}` |
   | `DATABASE_PASSWORD` | `${{Postgres.PGPASSWORD}}` |
   | `DATABASE_NAME` | `${{Postgres.PGDATABASE}}` |
   | `DATABASE_HOST` | `${{Postgres.PGHOST}}` |
   | `DATABASE_PORT` | `${{Postgres.PGPORT}}` |
   | `RABBITMQ_URL` | `amqp://USER:SENHA@<rabbitmq>.railway.internal:5672/` |
   | `PAYMENT_MOCK_DELAY_SECONDS` | `5` (só no worker) |
   | `INTERNAL_SYSTEM_FAILURE_RATE` | `0.3` (só no worker) |
   | `CORS_ORIGINS` | URL da Vercel, ex.: `https://meu-app.vercel.app` (só na API) |

   `Postgres` e `<rabbitmq>` são os nomes dos serviços criados nos passos 1 e 2.

**Vercel:** importe o repositório do frontend (preset Vite) e defina `VITE_API_URL` com o
domínio público da API no Railway, sem barra final. A variável é lida no *build*, então
mudar o valor exige um novo deploy. O `vercel.json` reescreve todas as rotas para o
`index.html`, o que mantém `/products` e `/orders` funcionando ao recarregar a página.

Depois do primeiro deploy do frontend, volte ao Railway e ajuste `CORS_ORIGINS` para a
URL final da Vercel; várias origens são separadas por vírgula.

---

## 13. Observabilidade (bônus): métricas e logs

Os logs estruturados (`utils/logger.py`) já existiam e cobrem o item desejável
"logs estruturados com correlação pedido/evento" — cada linha JSON carrega
`external_id`/`order_id` em `extra`. O que foi adicionado aqui é o bônus
"observabilidade, métricas e/ou tracing": um jeito de **ver** esses dados agregados,
em vez de só grep no stdout.

**O que cada peça faz** (Grafana sozinho não faz nada — ele só visualiza; quem
guarda os dados são as duas fontes abaixo):

| Peça | Papel |
|---|---|
| **Prometheus** | Coleta métricas numéricas via *scrape* HTTP: taxa de requisições, latência da API (`prometheus-fastapi-instrumentator`) e contadores de negócio — `orders_received_total`, `orders_duplicate_total` (hits de idempotência), `orders_processed_total{status}` (`utils/metrics.py`). |
| **Loki + Promtail** | Indexam os logs JSON que a aplicação já grava em `logs/app.jsonl` (`utils/logger.py`, controlado por `LOG_DIR`). Promtail faz o *tail* do arquivo e envia para o Loki; dá para buscar por `external_id`/`order_id` depois, sem depender do terminal onde o processo rodou. |
| **Grafana** | Um dashboard já provisionado (`observability/grafana/dashboards/pure-electric.json`) — taxa de requisições HTTP, p95 de latência, pedidos recebidos/processados por status, e um painel de logs ao vivo. |

### Como subir

```bash
docker compose up -d          # inclui prometheus, loki, promtail e grafana
uv run fastapi run main.py    # ⚠️ não use "fastapi dev" — ver nota abaixo
uv run python run_worker.py
```

- Grafana: **http://localhost:3001** (login `admin`/`admin` por padrão — vem do
  `.env`, `GRAFANA_ADMIN_USER`/`GRAFANA_ADMIN_PASSWORD` — ou acesso anônimo como
  *Viewer*, já habilitado via `GRAFANA_ANONYMOUS_ENABLED`) — dashboard "Pure
  Electric — Orders" fica pronto, sem precisar configurar nada.
- Prometheus: http://localhost:9090/targets (para conferir se os dois *scrape
  targets* estão `UP`).
- API: métricas cruas em http://localhost:8000/metrics; worker em
  http://localhost:9200/metrics (porta configurável por `WORKER_METRICS_PORT`).

> **Por que `fastapi run` e não `fastapi dev` aqui:** testando isso, descobri que
> `fastapi dev` faz *bind* só em `127.0.0.1` por padrão. Como Prometheus roda dentro
> do Docker e alcança o host via `host.docker.internal` (gateway da bridge, não
> `localhost`), ele não consegue fechar a conexão nesse modo — o *scrape target* da
> API fica `down` com "connection refused" (confirmei isso ao subir a stack: o alvo
> do worker, que já escuta em `0.0.0.0`, sobe `UP` na hora; o da API, não). `fastapi
> run` (modo produção, usado no Dockerfile/deploy) já faz bind em `0.0.0.0` e
> resolve. Para manter `fastapi dev` com hot-reload e ainda assim ser raspado, use
> `uv run fastapi dev main.py --host 0.0.0.0`.
>
> **Pegadinha nº 2, achada usando essa combinação:** com hot-reload ligado, cada
> linha que a aplicação grava em `logs/app.jsonl` é, ela mesma, uma mudança de
> arquivo dentro do diretório observado pelo `watchfiles` — que loga a detecção,
> essa linha de log é gravada de novo em `logs/app.jsonl`, que é detectada de novo,
> em loop infinito (sem reiniciar o processo de verdade — `watchfiles` só loga; o
> filtro de reload do uvicorn não considera `.jsonl`, então nenhum estado se perde,
> mas o terminal e o arquivo de log crescem sem parar). Corrigido subindo o nível do
> logger `watchfiles` para `WARNING` em `setup_logging()` (`utils/logger.py`), o
> mesmo padrão já usado para silenciar `sqlalchemy.engine`.

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

