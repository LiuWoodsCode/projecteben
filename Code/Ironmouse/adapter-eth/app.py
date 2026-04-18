"""Flask API for managing the Pi Zero USB Wi-Fi adapter."""

from __future__ import annotations

from flask import Flask, jsonify, render_template, request, send_from_directory

from config import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    DEFAULT_USB_INTERFACE,
    DEFAULT_USB_PROFILE,
    DEFAULT_WIFI_INTERFACE,
)
from nmcli import NetworkManagerService, NmcliError


def create_app() -> Flask:
    app = Flask(__name__)
    service = NetworkManagerService(
        wifi_interface=DEFAULT_WIFI_INTERFACE,
        usb_interface=DEFAULT_USB_INTERFACE,
        usb_profile=DEFAULT_USB_PROFILE,
    )

    @app.errorhandler(NmcliError)
    def handle_nmcli_error(error: NmcliError):
        return jsonify({"ok": False, "error": str(error)}), 400

    @app.errorhandler(404)
    def handle_not_found(_error):
        return jsonify({"ok": False, "error": "Not found"}), 404

    @app.get("/")
    def index():
        return render_template("index.html", rpi_model=service.rpi_model())

    @app.get("/download/client")
    def download_client():
        return send_from_directory(
            app.static_folder or "static",
            "client.zip",
            as_attachment=True,
            download_name="ironmouse-client.zip",
        )

    @app.get("/api/health")
    def health():
        return jsonify({"ok": True, "service": "pi-zero-wifi-adapter"})

    @app.get("/api/status")
    def status():
        return jsonify({"ok": True, "status": service.status()})

    @app.get("/api/connections")
    def connections():
        return jsonify({"ok": True, "connections": service.list_wifi_connections()})

    @app.get("/api/connections/<path:identifier>")
    def get_connection(identifier: str):
        return jsonify({"ok": True, "connection": service.get_connection(identifier)})

    @app.post("/api/connections")
    def add_connection():
        payload = request.get_json(silent=True) or {}
        connection = service.add_connection(
            ssid=str(payload.get("ssid", "")),
            password=payload.get("password"),
            hidden=bool(payload.get("hidden", False)),
            autoconnect=bool(payload.get("autoconnect", True)),
            connection_name=payload.get("name"),
            activate=bool(payload.get("activate", False)),
            priority=payload.get("priority"),
            replace_existing=bool(payload.get("replace_existing", False)),
        )
        return jsonify({"ok": True, "connection": connection}), 201

    @app.patch("/api/connections/<path:identifier>")
    def patch_connection(identifier: str):
        payload = request.get_json(silent=True) or {}
        connection = service.update_connection(identifier, payload)
        return jsonify({"ok": True, "connection": connection})

    @app.delete("/api/connections/<path:identifier>")
    def delete_connection(identifier: str):
        service.delete_connection(identifier)
        return jsonify({"ok": True})

    @app.post("/api/connections/<path:identifier>/up")
    def activate_connection(identifier: str):
        return jsonify({"ok": True, "connection": service.activate_connection(identifier)})

    @app.post("/api/connections/<path:identifier>/down")
    def deactivate_connection(identifier: str):
        return jsonify({"ok": True, "connection": service.deactivate_connection(identifier)})

    @app.get("/api/scan")
    def scan():
        return jsonify({"ok": True, "networks": service.scan_wifi()})

    @app.get("/api/wifi/radio")
    def get_wifi_radio():
        return jsonify({"ok": True, "wifi_radio": service.get_wifi_radio()})

    @app.post("/api/wifi/radio")
    def set_wifi_radio():
        payload = request.get_json(silent=True) or {}
        if "enabled" not in payload:
            raise NmcliError("The 'enabled' field is required")
        return jsonify({"ok": True, "wifi_radio": service.set_wifi_radio(bool(payload["enabled"]))})

    @app.post("/api/usb/share")
    def usb_share():
        connection = service.ensure_usb_sharing()
        return jsonify({"ok": True, "connection": connection})

    return app


app = create_app()


if __name__ == "__main__":
    app.run(host=DEFAULT_HOST, port=DEFAULT_PORT)

