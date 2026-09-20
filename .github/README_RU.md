# Инфраструктура alittlemore.dev

[🇺🇸 English version](./README.md)

Единый репозиторий Docker Compose runtime и production deployment для
[Personal Workspace](https://github.com/alittlemore-dev/personal-workspace) и
[Competency Trainer](https://github.com/alittlemore-dev/competency-trainer),
[Auth API](https://github.com/alittlemore-dev/auth-api) и общего
[frontend](https://github.com/alittlemore-dev/frontend). Исходный код приложений находится в
отдельных репозиториях и публикуется как контейнерные образы; этот репозиторий отвечает за
production-топологию, конфигурацию, секреты, TLS edge, жизненный цикл релизов и единый
интеграционный запуск локальной разработки.

## Возможности

- Единый публичный origin и общий nginx edge для всех приложений.
- API-маршрутизация с namespaces: `/api/personal-workspace/*` и `/api/competency/*`
  преобразуются в существующий контракт `/api/*` соответствующего backend; Auth API использует `/api/auth/*`.
- Отдельные PostgreSQL, Valkey, credentials, volumes и приватные сети для каждого приложения.
- Общие MinIO и Databasus с отдельными identities и ограниченным доступом к buckets.
- Синхронный blue/green rollout приложений с health checks и автоматическим откатом маршрутизации.
- Разделённые по сервисам конфигурация с исходными именами переменных и зашифрованные через
  SOPS/age секреты.
- Публичные HTTPS API; служебные интерфейсы и Agent API доступны только через VPN.
- Один общий frontend image, который обслуживает все non-API маршруты через nginx edge.

## Локальная разработка

Расположите `infra`, `frontend`, `personal-workspace`, `competency-trainer`, `auth-api` и `i18n` рядом. Один раз добавьте
локальный центр сертификации в доверенные, затем запустите весь стек:

```bash
make dev-trust
make dev
```

Общие edge и frontend доступны по адресу `https://alittlemore.localhost`. API Personal Workspace
начинаются с `/api/personal-workspace/`, а API Competency Trainer — с `/api/competency/`; Auth API — с `/api/auth/`.

## Production-запуск

Подготовьте production-конфигурацию, age identity, зашифрованные секреты и DNS, а четыре
application-пакета в GHCR сделайте публичными по инструкции
[Production deployment](../docs/production-deploy.md). При первом deployment выпустите общий
сертификат и запустите стек:

```bash
make certbot-issue
make deploy
```

Для последующих deployment и изменений конфигурации используется та же команда `make deploy`.
`make run` сохранён как совместимый alias.
Чтобы остановить контейнеры без удаления named volumes:

```bash
make stop
```

## Проверки

Проверьте системные зависимости, затем запустите полный локальный quality gate:

```bash
make doctor
make quality
```

`make quality` сам находит SOPS 3.13.3 и age-keygen 1.3.2 в `PATH` либо устанавливает проверенные
по checksum бинарники в игнорируемый локальный cache. Передавать пути к ним вручную не требуется.
Эта команда запускает тот же полный набор проверок, что и CI, включая реальный SOPS/age round trip.

Dependabot еженедельно проверяет GitHub Actions, Compose images и базовые образы Dockerfile.
Несколько pins требуют согласованного ручного обновления: для релизов SOPS и age нужно обновлять
checksums всех платформ, а версия OpenSSL в cert-sync зависит от выбранной ветки Alpine. Проверить
их, ничего не меняя, можно командой:

```bash
make dependencies-status
```

Команда показывает и актуальные pins, и доступные обновления, а завершается с ошибкой только тогда,
когда не удалось прочитать локальный pin или получить данные upstream. Она обращается к GitHub
Releases API и официальному зеркалу Alpine aports, поэтому требует сеть и намеренно не входит в
`make quality`.

Сканирование application и infrastructure images запускается отдельно, поскольку требует доступа
к registry:

```bash
make security-images
```

`make status` без изменений состояния показывает активный slot/release и контейнеры проекта.
Полный список целей доступен через `make` или `make help`.

## Документация

- [Production deployment и эксплуатация](../docs/production-deploy.md)
- [Внутренний доступ WireGuard](../docs/wireguard-internal-access.md)
- [Структура зашифрованных секретов](../secrets/README.md)
