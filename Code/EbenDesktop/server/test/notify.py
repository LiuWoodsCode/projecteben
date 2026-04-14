import asyncio
import json

from dbus_next import BusType, Message, MessageType
from dbus_next.aio import MessageBus

NOTIFICATIONS_INTERFACE = "org.freedesktop.Notifications"
MONITOR_MATCHES = [
    "type='method_call',interface='org.freedesktop.Notifications'",
    "type='signal',interface='org.freedesktop.Notifications'",
]

NOTIFY_METHODS = {
    "GetCapabilities",
    "Notify",
    "CloseNotification",
    "GetServerInformation",
}

NOTIFY_SIGNALS = {
    "NotificationClosed",
    "ActionInvoked",
    "ActivationToken",
}


def serialize_value(value):
    if isinstance(value, MessageType):
        return value.name.lower()

    value_type = type(value)
    if value_type.__name__ == "Variant" and hasattr(value, "value"):
        return serialize_value(value.value)

    if isinstance(value, bytes):
        return {
            "type": "bytes",
            "data": value.hex(),
        }

    if isinstance(value, dict):
        return {str(key): serialize_value(item) for key, item in value.items()}

    if isinstance(value, (list, tuple)):
        return [serialize_value(item) for item in value]

    return value


def parse_notification_body(member, body):
    if member == "GetCapabilities":
        return {"arguments": []}

    if member == "Notify":
        app_name, replaces_id, app_icon, summary, notif_body, actions, hints, expire_timeout = body
        return {
            "arguments": {
                "app_name": app_name,
                "replaces_id": replaces_id,
                "app_icon": app_icon,
                "summary": summary,
                "body": notif_body,
                "actions": actions,
                "hints": hints,
                "expire_timeout": expire_timeout,
            }
        }

    if member == "CloseNotification":
        (notification_id,) = body
        return {"arguments": {"id": notification_id}}

    if member == "GetServerInformation":
        return {"arguments": []}

    if member == "NotificationClosed":
        notification_id, reason = body
        return {
            "arguments": {
                "id": notification_id,
                "reason": reason,
            }
        }

    if member == "ActionInvoked":
        notification_id, action_key = body
        return {
            "arguments": {
                "id": notification_id,
                "action_key": action_key,
            }
        }

    if member == "ActivationToken":
        notification_id, activation_token = body
        return {
            "arguments": {
                "id": notification_id,
                "activation_token": activation_token,
            }
        }

    return {"arguments": body}


def is_relevant_message(message):
    if message.interface != NOTIFICATIONS_INTERFACE:
        return False

    if message.message_type == MessageType.METHOD_CALL:
        return message.member in NOTIFY_METHODS

    if message.message_type == MessageType.SIGNAL:
        return message.member in NOTIFY_SIGNALS

    return False


def message_handler(message):
    if not is_relevant_message(message):
        return

    body = serialize_value(message.body)
    payload = {
        "interface": message.interface,
        "member": message.member,
        "kind": message.message_type.name.lower()
        if isinstance(message.message_type, MessageType)
        else message.message_type,
        "source": {
            "destination": message.destination,
            "path": message.path,
            "sender": message.sender,
            "signature": message.signature,
            "serial": message.serial,
            "reply_serial": message.reply_serial,
            "error_name": message.error_name,
        },
        "body": body,
    }
    payload.update(parse_notification_body(message.member, body))

    print(json.dumps(payload, ensure_ascii=False, sort_keys=True), flush=True)

async def main():
    bus = await MessageBus(bus_type=BusType.SESSION).connect()

    bus.add_message_handler(message_handler)

    # Become a monitor connection so we can observe other clients' method calls.
    reply = await bus.call(
        Message(
            destination="org.freedesktop.DBus",
            path="/org/freedesktop/DBus",
            interface="org.freedesktop.DBus.Monitoring",
            member="BecomeMonitor",
            signature="asu",
            body=[MONITOR_MATCHES, 0],
        )
    )

    if reply.message_type == MessageType.ERROR:
        raise RuntimeError(f"D-Bus monitor setup failed: {reply.error_name}: {reply.body}")

    print("Listening for notifications...")
    await asyncio.Future()

asyncio.run(main())