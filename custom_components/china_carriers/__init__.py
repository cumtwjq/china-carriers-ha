"""China Mobile and China Unicom under one Home Assistant integration."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import logging
from pathlib import Path
import re
from typing import Any

from aiohttp import ClientSession
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import (
    CONF_ACCOUNT_ID, CONF_PROVIDER, DOMAIN, PLATFORMS, PROVIDER_MOBILE,
    PROVIDER_UNICOM, RESULT_DIR,
)
from .mobile_auth_bridge import async_queue_forget
from .unicom_api import UnicomAPI
from .unicom_coordinator import UnicomBillCoordinator

_LOGGER = logging.getLogger(__name__)
def _read_mobile_result(account_id: str) -> dict[str, Any]:
    """Read only the browser result for this account."""
    try:
        if re.fullmatch(r"legacy|[0-9a-f]{16}", account_id) is None:
            raise ValueError("invalid account id")
        payload = json.loads(
            (Path(RESULT_DIR) / f"{account_id}.json").read_text(encoding="utf-8")
        )
        if payload.get("version") != 2 or not isinstance(payload.get("sensors"), dict):
            raise ValueError("unsupported browser result")
        stamp = datetime.fromisoformat(payload["updated_at"])
        if stamp.tzinfo is None or datetime.now(timezone.utc) - stamp > timedelta(minutes=70):
            return {"status": "stale", "sensors": {}}
        return payload
    except (OSError, ValueError, TypeError, KeyError):
        return {"status": "waiting_for_app", "sensors": {}}


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    provider = entry.data.get(CONF_PROVIDER)
    if provider == PROVIDER_MOBILE:
        account_id = entry.data[CONF_ACCOUNT_ID]
        coordinator = DataUpdateCoordinator(
            hass,
            logger=_LOGGER,
            name=f"{DOMAIN}_mobile_{entry.entry_id}",
            update_method=lambda: hass.async_add_executor_job(_read_mobile_result, account_id),
            update_interval=timedelta(minutes=5),
        )
        await coordinator.async_config_entry_first_refresh()
        hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
        try:
            await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        except Exception:
            hass.data[DOMAIN].pop(entry.entry_id, None)
            raise

        reauth_started = False

        def prompt_reauth() -> None:
            nonlocal reauth_started
            if coordinator.data.get("status") == "login_required" and not reauth_started:
                for flow in hass.config_entries.flow.async_progress_by_handler(DOMAIN):
                    context = flow.get("context") or {}
                    if context.get("entry_id") == entry.entry_id and str(
                        flow.get("step_id", "")
                    ).startswith("reauth"):
                        reauth_started = True
                        return
                try:
                    entry.async_start_reauth(hass)
                    reauth_started = True
                except Exception:
                    _LOGGER.exception("Could not start China Mobile reauthentication")
            elif coordinator.data.get("status") == "ok":
                reauth_started = False

        entry.async_on_unload(coordinator.async_add_listener(prompt_reauth))
        prompt_reauth()
        return True

    if provider == PROVIDER_UNICOM:
        session = ClientSession()
        try:
            api = UnicomAPI(
                session=session,
                openid=entry.data["openid"],
                usage_ticket=entry.data.get("manual_ticket", ""),
                micro_hall_user=entry.data.get("manual_cookie", ""),
                micro_hall_access_token=entry.data.get("manual_microhall_cookie", ""),
            )
            coordinator = UnicomBillCoordinator(
                hass=hass,
                api=api,
                refresh_interval=entry.data.get("refresh_interval", 15),
                version="26.7.1 / combined 0.1.0",
            )
            hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
            await coordinator.async_config_entry_first_refresh()
            if not entry.data.get("phone_number"):
                try:
                    detected = await api.get_phone_number()
                    if detected:
                        hass.config_entries.async_update_entry(
                            entry,
                            data={**entry.data, "phone_number": detected},
                            title=f"中国联通10010 · 尾号{detected[-4:]}",
                        )
                except Exception:
                    _LOGGER.debug("Unicom phone number unavailable", exc_info=True)
            await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
            return True
        except Exception:
            hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
            await session.close()
            raise
    _LOGGER.error("Unsupported carrier provider: %s", provider)
    return False


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        coordinator = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
        if entry.data.get(CONF_PROVIDER) == PROVIDER_UNICOM and coordinator:
            await coordinator.api.session.close()
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    if entry.data.get(CONF_PROVIDER) == PROVIDER_MOBILE:
        await async_queue_forget(hass, entry.data[CONF_ACCOUNT_ID])
