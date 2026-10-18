# Docker Deployment

## Local Development

```bash
docker compose up --build
```

Services:
- `db` — PostgreSQL 16
- `redis` — Redis 7
- `rabbitmq` — RabbitMQ 3.13
- `web` — Django development server
- `scheduler` — Celery beat
- `worker` — Celery worker

## Production Considerations

- Use the `production` target in the Dockerfile.
- Set `DJANGO_DEBUG=False`.
- Provide a strong `DJANGO_SECRET_KEY` (minimum 50 characters, high entropy).
- Configure `DJANGO_ALLOWED_HOSTS`.
- Enable HTTPS and HSTS (`DJANGO_SECURE_SSL_REDIRECT=True`,
  `DJANGO_SECURE_HSTS_SECONDS=31536000`).
- Use an external PostgreSQL with backups.
- Run multiple worker replicas.
- Monitor with Prometheus and structured logs.
