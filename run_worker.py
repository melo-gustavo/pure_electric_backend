import asyncio
import os

from prometheus_client import start_http_server

from workers.order import start_worker

if __name__ == "__main__":
    start_http_server(int(os.getenv("WORKER_METRICS_PORT", "9200")))
    asyncio.run(start_worker())
