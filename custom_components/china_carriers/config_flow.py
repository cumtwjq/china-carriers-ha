"""Choose a carrier, then configure its original authentication method."""

from __future__ import annotations

import hashlib
import secrets
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry, OptionsFlow
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector

from .const import (
    CONF_ACCOUNT_ID, CONF_PROVIDER, DOMAIN, PROVIDER_MOBILE, PROVIDER_UNICOM,
)
from .mobile_auth_bridge import async_auth_command
from .unicom_const import (
    CONF_CREATE_INDIVIDUAL_SENSORS, CONF_OPENID, CONF_REFRESH_INTERVAL,
    DEFAULT_CREATE_INDIVIDUAL_SENSORS, DEFAULT_REFRESH_INTERVAL,
)

CONF_PHONE = "phone"
CONF_CODE = "code"


def _openid_id(openid: str) -> str:
    return "unicom:" + hashlib.sha256(openid.encode("utf-8")).hexdigest()


def _phone_schema(default: str | None = None) -> vol.Schema:
    key = vol.Required(CONF_PHONE, default=default) if default else vol.Required(CONF_PHONE)
    return vol.Schema({key: selector.TextSelector(
        selector.TextSelectorConfig(type=selector.TextSelectorType.TEL)
    )})


def _unicom_schema(data: dict[str, Any] | None = None) -> vol.Schema:
    data = data or {}
    openid = data.get(CONF_OPENID)
    openid_key = vol.Required(CONF_OPENID, default=openid) if openid else vol.Required(CONF_OPENID)
    return vol.Schema({
        openid_key: selector.TextSelector(),
        vol.Required(CONF_REFRESH_INTERVAL, default=data.get(
            CONF_REFRESH_INTERVAL, DEFAULT_REFRESH_INTERVAL
        )): vol.All(vol.Coerce(int), vol.Range(min=1, max=60)),
        vol.Optional(CONF_CREATE_INDIVIDUAL_SENSORS, default=data.get(
            CONF_CREATE_INDIVIDUAL_SENSORS, DEFAULT_CREATE_INDIVIDUAL_SENSORS
        )): bool,
    })


class MobileLoginSteps:
    _phone: str
    _account_id: str
    _reauth_entry: ConfigEntry | None = None

    async def _phone_step(
        self, step_id: str, user_input: dict[str, Any] | None = None,
        default: str | None = None,
    ) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            phone = str(user_input[CONF_PHONE]).strip()
            if len(phone) != 11 or not phone.startswith("1") or not phone.isdigit():
                errors["base"] = "invalid_phone"
            elif default and phone != default:
                errors["base"] = "phone_mismatch"
            else:
                if step_id == "mobile":
                    await self.async_set_unique_id(f"mobile:{phone}")
                    self._abort_if_unique_id_configured()
                    self._account_id = secrets.token_hex(8)
                status = await async_auth_command(
                    self.hass, self._account_id, "send_code", phone
                )
                if status in {"sent", "manual_required"}:
                    self._phone = phone
                    return await self.async_step_code()
                errors["base"] = status if status in {
                    "app_not_running", "app_timeout", "manual_required", "send_failed",
                    "login_failed", "browser_busy", "invalid_account",
                } else "login_failed"
        return self.async_show_form(
            step_id=step_id, data_schema=_phone_schema(default), errors=errors
        )

    async def async_step_code(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            code = str(user_input[CONF_CODE]).strip()
            if not code.isdigit() or not 4 <= len(code) <= 8:
                errors["base"] = "invalid_code"
            else:
                status = await async_auth_command(
                    self.hass, self._account_id, "verify", code
                )
                if status == "success":
                    return await self._finish_mobile_login()
                errors["base"] = status if status in {
                    "app_not_running", "app_timeout", "invalid_code", "login_failed",
                    "browser_busy", "invalid_account",
                } else "login_failed"
        return self.async_show_form(
            step_id="code", data_schema=vol.Schema({vol.Required(CONF_CODE): selector.TextSelector()}),
            errors=errors,
        )


class ChinaCarriersConfigFlow(MobileLoginSteps, config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> ChinaCarriersOptionsFlow:
        return ChinaCarriersOptionsFlow()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        if user_input is not None:
            if user_input[CONF_PROVIDER] == PROVIDER_MOBILE:
                return await self.async_step_mobile()
            return await self.async_step_unicom()
        return self.async_show_form(step_id="user", data_schema=vol.Schema({
            vol.Required(CONF_PROVIDER): vol.In({
                PROVIDER_MOBILE: "中国移动10086",
                PROVIDER_UNICOM: "中国联通10010",
            })
        }))

    async def async_step_mobile(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        return await self._phone_step("mobile", user_input)

    async def async_step_unicom(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            openid = str(user_input[CONF_OPENID]).strip()
            if not openid:
                errors["base"] = "openid_required"
            else:
                await self.async_set_unique_id(_openid_id(openid))
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"中国联通10010 · {_openid_id(openid)[-6:]}",
                    data={**user_input, CONF_OPENID: openid, CONF_PROVIDER: PROVIDER_UNICOM},
                )
        return self.async_show_form(
            step_id="unicom",
            data_schema=vol.Schema({vol.Required(CONF_OPENID): selector.TextSelector()}),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> FlowResult:
        self._reauth_entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        if self._reauth_entry is None or self._reauth_entry.data.get(CONF_PROVIDER) != PROVIDER_MOBILE:
            return self.async_abort(reason="unsupported_reauth")
        self._account_id = self._reauth_entry.data[CONF_ACCOUNT_ID]
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        return await self._phone_step(
            "reauth_confirm", user_input, self._reauth_entry.data[CONF_PHONE]
        )

    async def _finish_mobile_login(self) -> FlowResult:
        if self._reauth_entry is not None:
            return self.async_update_reload_and_abort(
                self._reauth_entry,
                data_updates={CONF_PHONE: self._phone, CONF_ACCOUNT_ID: self._account_id},
            )
        return self.async_create_entry(
            title=f"中国移动10086 · 尾号{self._phone[-4:]}",
            data={
                CONF_PROVIDER: PROVIDER_MOBILE,
                CONF_PHONE: self._phone,
                CONF_ACCOUNT_ID: self._account_id,
            },
        )


class ChinaCarriersOptionsFlow(MobileLoginSteps, OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        if self.config_entry.data[CONF_PROVIDER] == PROVIDER_UNICOM:
            return await self.async_step_unicom_options(user_input)
        self._account_id = self.config_entry.data[CONF_ACCOUNT_ID]
        return await self._phone_step(
            "init", user_input, self.config_entry.data[CONF_PHONE]
        )

    async def async_step_unicom_options(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            openid = str(user_input[CONF_OPENID]).strip()
            if not openid:
                errors["base"] = "openid_required"
            else:
                unique_id = _openid_id(openid)
                if any(
                    entry.entry_id != self.config_entry.entry_id and entry.unique_id == unique_id
                    for entry in self.hass.config_entries.async_entries(DOMAIN)
                ):
                    errors["base"] = "already_configured"
                else:
                    data = {**self.config_entry.data, **user_input, CONF_OPENID: openid}
                    changed = openid != self.config_entry.data[CONF_OPENID]
                    if changed:
                        # A renewed OpenID invalidates the previous account session.
                        # Detect the phone again and discard any old manual credentials.
                        for key in (
                            "phone_number", "manual_ticket", "manual_ticket_phone",
                            "manual_balance_ticket", "manual_balance_ticket_phone",
                            "manual_cookie", "manual_microhall_cookie",
                        ):
                            data.pop(key, None)
                    self.hass.config_entries.async_update_entry(
                        self.config_entry,
                        data=data,
                        unique_id=unique_id,
                        title=(
                            f"中国联通10010 · {unique_id[-6:]}"
                            if changed else self.config_entry.title
                        ),
                    )
                    await self.hass.config_entries.async_reload(self.config_entry.entry_id)
                    return self.async_create_entry(data={})
        return self.async_show_form(
            step_id="unicom_options",
            data_schema=_unicom_schema(dict(self.config_entry.data)), errors=errors,
        )

    async def _finish_mobile_login(self) -> FlowResult:
        await self.hass.config_entries.async_reload(self.config_entry.entry_id)
        return self.async_create_entry(data={})
