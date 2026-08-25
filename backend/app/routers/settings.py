"""Runtime settings API."""
from __future__ import annotations

from fastapi import APIRouter, Request

from ..schemas import SettingsUpdateExt

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("")
async def get_settings(request: Request) -> dict:
    svc = request.app.state.services
    data = await svc.settings_service.as_dict()
    data["currency"] = svc.settings.currency
    return data


@router.put("")
async def update_settings(body: SettingsUpdateExt, request: Request) -> dict:
    from ..core.errors import BadRequest
    svc = request.app.state.services
    for key, value in body.model_dump(exclude_none=True).items():
        if key == "default_provider" and value not in svc.providers_registry.names():
            raise BadRequest(
                f"Unknown default provider '{value}'. Registered: "
                f"{', '.join(svc.providers_registry.names())}.",
                code="PROVIDER_NOT_FOUND")
        await svc.settings_service.set(key, value)
    return await get_settings(request)
