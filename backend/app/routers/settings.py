"""Runtime settings API."""
from __future__ import annotations

from fastapi import APIRouter, Request

from ..schemas import SettingsUpdate

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("")
async def get_settings(request: Request) -> dict:
    svc = request.app.state.services
    data = await svc.settings_service.as_dict()
    data["currency"] = svc.settings.currency
    return data


@router.put("")
async def update_settings(body: SettingsUpdate, request: Request) -> dict:
    svc = request.app.state.services
    for key, value in body.model_dump(exclude_none=True).items():
        await svc.settings_service.set(key, value)
    return await get_settings(request)
