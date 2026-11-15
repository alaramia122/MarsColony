# MarsColony

Многопользовательская стратегическая игра-симулятор о развитии марсианской колонии.

## Состояние

Этапы 0–4 интегрированы в `main`: игровое ядро и экономика, участники и роли, проекты, браузерные клиенты, схематическая карта, каталог инфраструктуры, пользовательские изображения и AI Gateway.

**Автоматическая сборка не означает завершённую приёмку.** Остаются:
- LAN-приёмка на двух независимых устройствах ([Issue #9](https://github.com/alaramia122/MarsColony/issues/9));
- реальный запрос к внешнему AI provider с пользовательскими credentials;
- браузерные E2E-сценарии;
- плейтест Ares-1 ([Issue #3](https://github.com/alaramia122/MarsColony/issues/3)).

Карта пока схематическая. Полный научно-технический цикл и расширенные конструкторы объектов/процессов — следующие этапы, а не готовые функции.

## Документация

- [Текущее состояние](docs/PROJECT_STATE.md)
- [GDD](docs/GDD.md)
- [Руководство игрока](docs/PLAYER_GUIDE.md)
- [Техническое задание](docs/TECHNICAL_SPEC.md)
- [Экономическая модель](docs/ECONOMY_MODEL.md)
- [Сценарий Ares-1](docs/SCENARIO_ARES_01.md)
- [План разработки](docs/DEVELOPMENT_PLAN.md)
- [Карта, технологии и творчество игроков](docs/MAP_TECHNOLOGY_CREATION.md)
- [Гексагональная карта и согласование размещения](docs/HEX_MAP_PLACEMENT_WORKFLOW.md)
- [Проектирование этапа 3](docs/STAGE3_DESIGN.md)
- [Проектирование этапа 4](docs/STAGE4_DESIGN.md)

## Стек

- Backend: Python, FastAPI, PostgreSQL.
- Frontend: React, TypeScript, Vite.
- Синхронизация: HTTP API и WebSocket.
- Развёртывание: Docker Compose, Nginx.
- ИИ: Yandex AI Studio и OpenAI-compatible API через AI Gateway.

## Локальный запуск

Требуется запущенный Docker Desktop с Docker Compose v2.

1. Скопируйте `.env.example` в `.env`.
2. Установите локальный пароль PostgreSQL в `.env`.
3. Запустите из корня репозитория:

```bash
docker compose up --build -d
docker compose ps
```

Адреса:
- Web UI: http://localhost:8080
- Backend API: http://localhost:8000
- OpenAPI: http://localhost:8000/docs

Логи: `docker compose logs -f api web db`. Остановка: `docker compose down`. Данные PostgreSQL хранятся в Docker volume. Команда `docker compose down -v` удаляет этот volume и все данные БД.

Docker Hub login обычно не требуется для публичных базовых образов. Важно, чтобы Docker Desktop/Engine был запущен и команда `docker compose` могла обращаться к нему.

## Структура

- `backend/` — FastAPI, доменная логика и PostgreSQL persistence.
- `frontend/` — React/TypeScript/Vite.
- `data/` — сценарии, стартовые профили и каталоги сущностей.
- `docs/` — GDD, требования, экономика, план и ADR.
- `tests/` — сценарные проверки.
- `.github/workflows/` — CI.

Секреты API и пароли не коммитить в Git. API-кредиты оплачиваются отдельно от подписки ChatGPT.
