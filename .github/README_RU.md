# Инфраструктура alittlemore.dev

[🇺🇸 English version](./README.md)

Единый репозиторий Docker Compose runtime и production deployment для
[Personal Workspace](https://github.com/alittlemore-dev/personal-workspace) и
[Competency Trainer](https://github.com/alittlemore-dev/competency-trainer). Исходный код
приложений находится в отдельных репозиториях и публикуется как контейнерные образы; этот
репозиторий отвечает за production-топологию, конфигурацию, секреты, TLS edge и жизненный цикл
релизов.

## Возможности

- Единый Compose lifecycle и общий nginx edge для обоих приложений.
- Отдельные PostgreSQL, Valkey, credentials, volumes и приватные сети для каждого приложения.
- Общие MinIO и Databasus с отдельными identities и ограниченным доступом к buckets.
- Синхронный blue/green rollout приложений с health checks и автоматическим откатом маршрутизации.
- Разделённые по сервисам конфигурация с исходными именами переменных и зашифрованные через
  SOPS/age секреты.
- Публичные HTTPS-маршруты приложений; служебные интерфейсы и Agent API доступны только через VPN.

## Запуск

Подготовьте production-конфигурацию, age identity, зашифрованные секреты, DNS и доступ к registry
по инструкции [Production deployment](../docs/production-deploy.md). При первом deployment
выпустите общий сертификат и запустите стек:

```bash
make certbot-issue
make run
```

Для последующих deployment и изменений конфигурации используется та же команда `make run`.
Чтобы остановить контейнеры без удаления named volumes:

```bash
make stop
```

## Проверки

Запустите полный локальный quality gate:

```bash
make quality
```

Сканирование application и infrastructure images запускается отдельно, поскольку требует доступа
к registry:

```bash
make security-trivy-images
```

## Документация

- [Production deployment и эксплуатация](../docs/production-deploy.md)
- [Структура зашифрованных секретов](../secrets/README.md)

