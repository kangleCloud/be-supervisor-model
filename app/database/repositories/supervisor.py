"""Supervisor 相关仓储。"""
from __future__ import annotations

import json
import math
from datetime import datetime

from tortoise.expressions import Q
from tortoise.queryset import QuerySet

from app.database.models.supervisor import SupervisorImportStagingModel, SupervisorServiceModel


class SupervisorServiceRepository:
    """Supervisor 主表访问。"""

    @staticmethod
    def _query(using_db=None) -> QuerySet[SupervisorServiceModel]:
        queryset = SupervisorServiceModel.all()
        return queryset.using_db(using_db) if using_db is not None else queryset

    async def find_by_content_program_name(
        self,
        host_ip: str,
        content_program_name: str,
        *,
        using_db=None,
    ) -> SupervisorServiceModel | None:
        return await self._query(using_db).filter(
            host_ip=host_ip,
            content_program_name=content_program_name,
        ).first()

    async def find_by_config_path(
        self,
        host_ip: str,
        config_path: str,
        *,
        using_db=None,
    ) -> SupervisorServiceModel | None:
        return await self._query(using_db).filter(host_ip=host_ip, config_path=config_path).first()

    async def find_port_conflict(
        self,
        host_ip: str,
        port: int,
        *,
        exclude_record_id: int | None = None,
        using_db=None,
    ) -> SupervisorServiceModel | None:
        queryset = self._query(using_db).filter(host_ip=host_ip, port=port, is_archived=False)
        if exclude_record_id is not None:
            queryset = queryset.exclude(id=exclude_record_id)
        return await queryset.first()

    async def search_page(
        self,
        *,
        host: str | None,
        keyword: str | None,
        status: str | None,
        archived: str,
        page: int,
        page_size: int,
    ) -> tuple[list[SupervisorServiceModel], int, int]:
        queryset = self._query()
        if host:
            queryset = queryset.filter(host_ip=host)
        if keyword:
            keyword_filter = (
                Q(content_program_name__icontains=keyword)
                | Q(file_name__icontains=keyword)
                | Q(job_name__icontains=keyword)
                | Q(module_name__icontains=keyword)
            )
            if keyword.isdigit():
                keyword_filter = keyword_filter | Q(port=int(keyword))
            queryset = queryset.filter(keyword_filter)
        if status:
            queryset = queryset.filter(status=status)
        if archived == "false":
            queryset = queryset.filter(is_archived=False)
        elif archived == "true":
            queryset = queryset.filter(is_archived=True)

        total = await queryset.count()
        records = await queryset.order_by("-update_time", "-id").offset((page - 1) * page_size).limit(page_size)
        pages = 0 if total == 0 else math.ceil(total / page_size)
        return list(records), total, pages

    async def create(
        self,
        payload: dict[str, object],
        *,
        using_db=None,
    ) -> SupervisorServiceModel:
        return await SupervisorServiceModel.create(using_db=using_db, **payload)

    async def update_by_host_and_program(
        self,
        host_ip: str,
        content_program_name: str,
        payload: dict[str, object],
        *,
        using_db=None,
    ) -> int:
        queryset = self._query(using_db).filter(host_ip=host_ip, content_program_name=content_program_name)
        return await queryset.update(**payload)

    async def update_by_id(self, record_id: int, payload: dict[str, object], *, using_db=None) -> int:
        return await self._query(using_db).filter(id=record_id).update(**payload)

    async def delete_by_host_and_program(self, host_ip: str, content_program_name: str, *, using_db=None) -> int:
        return await self._query(using_db).filter(host_ip=host_ip, content_program_name=content_program_name).delete()

    async def delete_by_id(self, record_id: int, *, using_db=None) -> int:
        return await self._query(using_db).filter(id=record_id).delete()


class SupervisorImportStagingRepository:
    """导入暂存表访问。"""

    @staticmethod
    def _query(using_db=None) -> QuerySet[SupervisorImportStagingModel]:
        queryset = SupervisorImportStagingModel.all()
        return queryset.using_db(using_db) if using_db is not None else queryset

    async def clear_operator_host_batches(self, *, host_ip: str, operator_id: int, using_db=None) -> int:
        return await self._query(using_db).filter(host_ip=host_ip, operator_id=operator_id).delete()

    async def delete_expired_batches(self, *, expire_before: datetime, using_db=None) -> int:
        return await self._query(using_db).filter(create_time__lt=expire_before).delete()

    async def insert_batch(
        self,
        *,
        batch_id: str,
        host_ip: str,
        operator_id: int,
        operator_name: str,
        items: list[dict[str, object]],
        using_db=None,
    ) -> None:
        for item in items:
            await SupervisorImportStagingModel.create(
                using_db=using_db,
                batch_id=batch_id,
                host_ip=host_ip,
                operator_id=operator_id,
                operator_name=operator_name,
                config_path=item["config_path"],
                file_name=item["file_name"],
                content_program_name=item.get("content_program_name"),
                baseline_content=item.get("baseline_content"),
                metadata_complete=bool(item["metadata_complete"]),
                parse_warnings=json.dumps(list(item.get("parse_warnings", ())), ensure_ascii=False),
                job_name=item.get("job_name"),
                module_name=item.get("module_name"),
                java_path=item.get("java_path"),
                active_profile=item.get("active_profile"),
                port=item.get("port"),
                jar_name=item.get("jar_name"),
                xms=item.get("xms"),
                xmx=item.get("xmx"),
                run_user=item.get("run_user"),
                result=item["result"],
                message=item.get("message"),
            )

    async def get_batch(
        self,
        *,
        batch_id: str,
        host_ip: str,
        operator_id: int,
        using_db=None,
    ) -> list[SupervisorImportStagingModel]:
        rows = await self._query(using_db).filter(
            batch_id=batch_id,
            host_ip=host_ip,
            operator_id=operator_id,
        ).order_by("id")
        return list(rows)

    async def delete_batch(self, batch_id: str, *, using_db=None) -> int:
        return await self._query(using_db).filter(batch_id=batch_id).delete()

    async def delete_batch_scope(
        self,
        *,
        batch_id: str,
        host_ip: str,
        operator_id: int,
        using_db=None,
    ) -> int:
        return await self._query(using_db).filter(
            batch_id=batch_id,
            host_ip=host_ip,
            operator_id=operator_id,
        ).delete()
