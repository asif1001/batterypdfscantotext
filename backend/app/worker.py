"""RQ worker entrypoint. Run inside Docker, where the process can fork."""

from redis import Redis
from rq import Queue, Worker

from app.config import settings


def main() -> None:
    connection = Redis.from_url(settings.redis_url)
    worker = Worker([Queue("scans", connection=connection)], connection=connection)
    worker.work(with_scheduler=False)


if __name__ == "__main__":
    main()
