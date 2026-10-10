"""Populate only the isolated local stack, preserving existing aggregate rows."""

from __future__ import annotations

import asyncio
import hashlib
import math
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path


def require_local_runtime(environment: dict[str, str]) -> None:
    if (
        not environment.get("APP_DOMAIN", "").endswith(".localhost")
        or environment.get("APP_DEBUG", "").lower() != "true"
    ):
        raise RuntimeError(
            "Analytics demo data requires a debug runtime on a .localhost domain"
        )


def daily_counts(day: int, target: int, source: int) -> tuple[int, int]:
    trend = 1 + day / 150
    wave = 1 + 0.35 * math.sin(day / 7 + target)
    views = round(
        (7 + (target * 11) % 41) * trend * wave * (1, 0.8, 1.6, 0.6, 0.4, 0.2)[source]
    )
    return views, round(views * (0.35 + (target % 5) * 0.1))


async def main() -> None:
    require_local_runtime(dict(os.environ))
    # Read local mounted secrets in memory; never print connection settings.
    for name in (
        "APP_SECRET_KEY",
        "DB_PASSWORD",
        "MINIO_ACCESS_KEY",
        "MINIO_SECRET_KEY",
        "SENTRY_DSN",
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_WEBHOOK_SECRET",
        "TELEGRAM_SERVICE_SECRET",
        "TELEGRAM_PROXY_URLS",
    ):
        if path := os.environ.get(name + "_FILE"):
            os.environ[name] = Path(path).read_text().strip()
    from sqlalchemy import select
    from sqlalchemy.dialects.postgresql import insert
    from core.analytics.enums import AnalyticsKind
    from core.articles.enums import ArticleViewSourceCategory
    from core.enums import PublishStatusEnum
    from infra.postgresql.meta import engine
    from infra.postgresql.models import (
        CompetencyMatrixItemModel,
        CompetencyMatrixSheetModel,
    )
    from infra.postgresql.models.analytics import AnalyticsDailyModel

    today = datetime.now(timezone.utc).date()
    sources = list(ArticleViewSourceCategory)
    inserted = 0
    try:
        async with engine.begin() as connection:
            questions = (
                (
                    await connection.execute(
                        select(CompetencyMatrixItemModel.id)
                        .where(
                            CompetencyMatrixItemModel.slug.like("demo-%"),
                            CompetencyMatrixItemModel.publish_status
                            == PublishStatusEnum.PUBLISHED,
                        )
                        .order_by(CompetencyMatrixItemModel.id)
                    )
                )
                .scalars()
                .all()
            )
            sheets = (
                (
                    await connection.execute(
                        select(CompetencyMatrixSheetModel.key)
                        .where(CompetencyMatrixSheetModel.key.like("demo-%"))
                        .order_by(CompetencyMatrixSheetModel.key)
                    )
                )
                .scalars()
                .all()
            )
            if not questions or not sheets:
                raise RuntimeError(
                    "No demo matrix content found; analytics fixtures never use real questions"
                )
            rows = []

            def add(kind, target, date, source, **counts):
                key = f"analytics-demo-v1:{kind.value}:{target}:{date}:{source.value}"
                rows.append(
                    dict(
                        id=hashlib.md5(key.encode(), usedforsecurity=False).hexdigest(),
                        kind=kind,
                        target_id=target,
                        date=date,
                        source=source,
                        **{
                            name: counts.get(name, 0)
                            for name in (
                                "views",
                                "engaged",
                                "reactions",
                                "suggestions",
                                "visitors",
                            )
                        },
                    )
                )

            for day in range(120):
                date = today - timedelta(days=119 - day)
                for index, question in enumerate(questions):
                    for source_index, source in enumerate(sources):
                        views, engaged = daily_counts(day, index, source_index)
                        add(
                            AnalyticsKind.MATRIX,
                            question,
                            date,
                            source,
                            views=views,
                            engaged=engaged,
                        )
                for index, sheet in enumerate(sheets):
                    count = (
                        (day * 7 + index * 3) % 6
                        if day % 3 == 0
                        else int((day + index) % 7 == 0)
                    )
                    if count:
                        add(
                            AnalyticsKind.SUGGESTIONS,
                            sheet,
                            date,
                            ArticleViewSourceCategory.UNKNOWN,
                            suggestions=count,
                        )
                for source_index, source in enumerate(sources):
                    add(
                        AnalyticsKind.SITE,
                        "_audience",
                        date,
                        source,
                        visitors=round(
                            (20 + day // 4) * (1, 0.2, 1.5, 0.8, 0.5, 0.1)[source_index]
                        ),
                    )
                    for page_index, page in enumerate(
                        (
                            "articles",
                            "matrix",
                            "about",
                            "updates",
                            "contacts",
                            "sitemap",
                        )
                    ):
                        views, engaged = daily_counts(day, page_index + 3, source_index)
                        add(
                            AnalyticsKind.SITE,
                            page,
                            date,
                            source,
                            views=views,
                            engaged=engaged,
                        )
            for start in range(0, len(rows), 500):
                result = await connection.execute(
                    insert(AnalyticsDailyModel)
                    .values(rows[start : start + 500])
                    .on_conflict_do_nothing()
                    .returning(AnalyticsDailyModel.id)
                )
                inserted += len(result.scalars().all())
        print(
            f"Local demo analytics: {inserted} new rows, {len(questions)} questions, {len(sheets)} sheets, 120 days. Existing rows preserved."
        )
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
