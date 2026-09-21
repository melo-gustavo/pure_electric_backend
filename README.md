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
| Frontend | **React** | Escolha para a interface de consulta. **Não implementada nesta entrega** (item não obrigatório no desafio) — ver [Pendências](#10-pendências). |

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
workers/           Consumidor da fila de pedidos + sistema interno simulado
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

- `docker compose up -d` sobe o Postgres (`db`) e o RabbitMQ (`queue`).
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
| `messages_ready = 0` e `consumers = 0` | Fila vazia e worker fora do ar. Se o pedido segue `RECEIVED`, a mensagem já foi consumida e descartada; não há reprocessamento automático (ver *Reprocessamento manual* em [Pendências](#10-pendências)). |
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
  `Order not found, skipping`.

E um alerta sobre esse log: `Order not found, skipping` significa que a mensagem foi
**confirmada e descartada** — não há dead-letter queue. Um caso concreto: a fila é
`durable`, então ela sobrevive a um `alembic downgrade base` (que derruba a tabela
`orders`), e as mensagens seguem apontando para pedidos que não existem mais. Nada
quebra, mas o pedido nunca vai ser processado e não há rastro além do log.

---

## 4. API

| Método | Rota | Descrição |
|---|---|---|
| `POST` | `/orders` | Recebe um pedido. **201** se criado, **200** se o `external_id` já existia. |
| `GET` | `/orders` | Lista os pedidos com o status atual. |
| `GET` | `/orders/{order_id}` | Consulta um pedido por ID. **404** se não existir. |
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
   │
   ▼
worker (run_worker.py)
   ├─ busca o pedido por external_id           → não achou: log + ack
   ├─ status PROCESSED/FAILED → ignora (reentrega)
   ├─ status PROCESSING       → ignora (já em andamento)
   ├─ UPDATE status = PROCESSING
   ├─ chama o sistema interno simulado (call_payment_mock)
   │     ├─ sucesso → UPDATE status = PROCESSED
   │     └─ falha   → UPDATE status = FAILED
   └─ exceção inesperada → log + UPDATE status = FAILED
```

A mensagem trafega com `id`, `externalId`, `customer`, `amount`, `status` e
`createdAt`. O worker **não** confia no status da mensagem: ele relê o pedido no
banco antes de agir, porque a fila pode reentregar mensagens.

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
3. **O worker relê o estado antes de processar.** Se a mesma mensagem chegar duas
   vezes (reentrega do broker, retry manual, dois consumidores), o pedido é
   ignorado quando já está `PROCESSED`, `FAILED` ou `PROCESSING`. A decisão é
   tomada no banco, não na mensagem.

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

O envio para o sistema interno é simulado por `call_payment_mock`, que introduz
latência de 500 ms e alterna entre sucesso e falha. O tratamento atual:

- **Broker reiniciando entre publish e consumo:** a fila é `durable` e a mensagem é
  publicada como `PERSISTENT`, então ela sobrevive ao reinício e é entregue quando
  o worker voltar.
- **Sistema interno retornando falha:** o pedido vai para `FAILED`, registra o
  motivo em `failure_reason` e carimba `processed_at`. A mensagem é confirmada
  (`message.process()`), evitando redelivery infinita de um erro de negócio.
- **Exceção inesperada no worker:** capturada, logada com `logger.exception`, e o
  pedido é marcado `FAILED` com `processed_at` e o erro em `failure_reason`
  (truncado em 500 caracteres). A mensagem não volta para a fila.
- **Sistema interno pendurado:** o worker fica bloqueado no `await`. Como o mock
  roda no mesmo processo do consumidor, isso reduz o consumo daquela fila —
  mitigação real depende de timeout na chamada, que ainda não existe.
- **RabbitMQ indisponível no momento do publish:** a exceção sobe para o cliente
  (500) depois do pedido já ter sido gravado. O pedido fica em `RECEIVED` e **não**
  é processado automaticamente. É a falha de dual-write, descrita nas pendências.
- **Worker cai entre `PROCESSING` e o status final:** o pedido fica preso em
  `PROCESSING` e a mensagem já foi confirmada; nenhuma reentrega acontece. Precisa
  de um lease/varredura de pedidos presos.

Não há retry com backoff nem dead-letter queue nesta entrega — estão priorizados
nas pendências.

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
6. **Observabilidade:** correlation id (`external_id`) propagado em todos os logs,
   métricas por fila (profundidade, taxa de falha, latência) e tracing distribuído.
7. **Escala:** múltiplos consumidores por fila com prefetch ajustado. Para preservar
   ordem por pedido, particionar por `external_id` (hash exchange) ou usar filas
   shardeadas.
8. **Versionamento do payload** (campo `schema_version` no envelope) para que
   integrações antigas continuem funcionando durante a migração.

---

## 9. Trade-offs (limite de 4 horas)

O que foi deliberadamente simplificado:

- **Sistema interno como função no mesmo processo** (`call_payment_mock`) em vez de
  serviço separado. O enunciado permite, mas acopla CPU/latência do mock ao worker.
- **Falha simulada por sorteio** (`random.randint`) em vez de uma regra
  determinística. Simples de escrever, porém impede teste determinístico do
  caminho de falha.
- **Sem retry, sem dead-letter queue e sem reprocessamento** — itens desejáveis e
  bônus do desafio, sacrificados para fechar o fluxo essencial.
- **Sem autenticação.** O webhook não valida assinatura/HMAC e não há auth nos
  endpoints de consulta e CRUD.
- **API e worker rodam direto na máquina** (só Postgres e RabbitMQ em containers),
  sem Dockerfile para a aplicação. Mais rápido de iterar, menos próximo de produção.
- **Sem Makefile/justfile.** Os comandos são executados direto com `uv`, `docker
  compose` e `alembic`, documentados na seção [Como rodar](#3-como-rodar);
  automação foi considerada fora do timebox.
- **Sem paginação nem filtro por status** em `GET /orders`.
- **Modelo de itens do pedido removido.** Chegou a existir `OrderItem`
  (`order_items`, com `product_id`, `quantity` e `unit_price`) mais o vínculo
  `orders.user_id`. Nenhum endpoint criava itens e o payload do desafio não tem
  itens, então a tabela ficaria permanentemente vazia: código não exercitado, com
  um `selectinload` extra em toda leitura e um `lazy load` que estouraria
  `MissingGreenlet` assim que o primeiro item existisse. Foi removido, junto com as
  rotas e a migração correspondente, em vez de manter estrutura sem uso.
- **Interface visual não implementada**, apesar de a escolha de stack apontar para
  React.

---

## 10. Pendências

O que ficou de fora e como seria implementado:

| # | Pendência | Como implementaria |
|---|---|---|
| 1 | Dual-write entre commit e publish | Padrão **outbox**: gravar o evento numa tabela `outbox` na mesma transação do pedido e um publisher separado lê e publica, marcando como enviado. Alternativa imediata: `try/except` no publish com log e endpoint de reprocessamento. |
| 2 | Retry com backoff | Fila `orders.retry` com TTL e `x-dead-letter-exchange`: em falha, republica com `x-retry-count` e atraso exponencial; após N tentativas, vai para a DLQ. |
| 3 | Pedido preso em `PROCESSING` | Coluna `processing_started_at` como lease e um job periódico que devolve para a fila pedidos em `PROCESSING` há mais de X minutos. |
| 4 | Reprocessamento manual | `POST /orders/{id}/reprocess`, permitindo apenas partindo de `FAILED`, com transição para `RECEIVED` e novo publish. |
| 5 | Timeout na chamada ao sistema interno | Passar `timeout` explícito no cliente HTTP e tratar o timeout como falha recuperável (com retry), não como `FAILED` definitivo. |
| 6 | Filtros e paginação | `GET /orders?status=FAILED&limit=&offset=` com índices em `status` e `created_at`. |
| 7 | Downgrade das migrações | Os enums nativos (`order_status`, `document_type`) não são removidos no `downgrade`, então `alembic downgrade base` seguido de `upgrade head` falha com "type already exists". Corrigir com `sa.Enum(..., name=...).drop(op.get_bind())` e/ou `naming_convention` no metadata para nomear constraints geradas. |
| 8 | Correlação nos logs | Os logs já são JSON com os campos de `extra`. Falta propagar um `correlation_id` (`external_id`) por `contextvars` para que apareça em toda linha da requisição e do worker, inclusive nas que não o passam em `extra`. |
| 9 | Testes do worker | Testar `process_order` diretamente com a `session_factory` dos testes e `call_payment_mock` mockado, cobrindo `RECEIVED → PROCESSING → PROCESSED`, o caminho de `FAILED` e o cenário de mensagem duplicada. Exige tornar a falha determinística (pendência do mock aleatório). |
| 10 | Reuso do canal do RabbitMQ | `get_channel()` abre uma conexão nova a cada `publish` e não fecha; manter um canal com lock ou usar `connect_robust` em bloco para evitar acúmulo de conexões. |
| 11 | Autenticação e proteção de dados | Assinatura HMAC no webhook, autenticação nos endpoints de consulta e mascaramento de CPF/CNPJ nas respostas. |
| 12 | Testes de contrato e dead-letter queue | Contrato entre API e worker validando o envelope da mensagem; DLQ para mensagens não processáveis. |
| 13 | Interface de consulta (React) | SPA consumindo `GET /orders` com filtro por status e atualização periódica, exibindo a transição dos estados. |

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
- Apoio na redação deste README.

**Como o código foi revisado e validado:**

- `uv run pytest` — 38 testes passando, sem depender de serviços externos (CRUD, idempotência incluindo a recuperação
  do `IntegrityError`, formatos de payload, 404/422 e a camada de publicação na fila).
- `uv run ruff check` e `uv run ruff format --check` sem pendências.
- `uv run alembic check` — nenhuma divergência entre os models e o schema do banco.
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
