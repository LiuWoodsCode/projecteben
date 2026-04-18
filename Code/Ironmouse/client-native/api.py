"""HTTP client for the Pi Zero adapter API."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

import requests


class AdapterApiError(RuntimeError):
    """Raised when the adapter API returns an error."""


@dataclass(slots=True)
class AdapterApi:
    base_url: str = "http://10.12.194.1:5000"
    timeout: float = 10.0
    session: requests.Session = field(default_factory=requests.Session)

    def set_base_url(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    def _request(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        response = self.session.request(
            method=method,
            url=url,
            json=payload,
            params=params,
            timeout=self.timeout,
        )
        try:
            body = response.json()
        except ValueError as error:
            raise AdapterApiError(f"Adapter returned non-JSON data from {url}") from error
        if response.status_code >= 400 or not body.get("ok", False):
            message = body.get("error") or f"HTTP {response.status_code}"
            raise AdapterApiError(message)
        return body

    def health(self) -> dict[str, Any]:
        return self._request("GET", "/api/health")

    def status(self) -> dict[str, Any]:
        return self._request("GET", "/api/status")["status"]

    def list_connections(self) -> list[dict[str, Any]]:
        return self._request("GET", "/api/connections")["connections"]

    def get_connection(self, identifier: str) -> dict[str, Any]:
        return self._request("GET", f"/api/connections/{quote(identifier, safe='')}")["connection"]

    def scan(self) -> list[dict[str, Any]]:
        return self._request("GET", "/api/scan")["networks"]

    def add_connection(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/api/connections", payload=payload)["connection"]

    def update_connection(self, identifier: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request(
            "PATCH",
            f"/api/connections/{quote(identifier, safe='')}",
            payload=payload,
        )["connection"]

    def delete_connection(self, identifier: str) -> None:
        self._request("DELETE", f"/api/connections/{quote(identifier, safe='')}")

    def activate_connection(self, identifier: str) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/api/connections/{quote(identifier, safe='')}/up",
        )["connection"]

    def deactivate_connection(self, identifier: str) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/api/connections/{quote(identifier, safe='')}/down",
        )["connection"]

    def wifi_radio(self) -> dict[str, Any]:
        return self._request("GET", "/api/wifi/radio")["wifi_radio"]

    def set_wifi_radio(self, enabled: bool) -> dict[str, Any]:
        return self._request("POST", "/api/wifi/radio", payload={"enabled": enabled})["wifi_radio"]

    def ensure_usb_sharing(self) -> dict[str, Any]:
        return self._request("POST", "/api/usb/share")["connection"]

