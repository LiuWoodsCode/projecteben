#!/usr/bin/env python3

import asyncio
import locale
import os
import re
from dataclasses import dataclass
from typing import Optional

from dbus_next.aio import MessageBus
from dbus_next.constants import BusType


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Remembrance:
    id: str
    title: str
    message: str
    date: str
    default_regions: tuple[str, ...]


REMEMBRANCES = (
    # United States
    Remembrance(
        id="september_11",
        title="September 11 Remembrance",
        message="Honor the memory of those lost on September 11, 2001.",
        date="09-11",
        default_regions=("US",),
    ),

    # Japan / United States
    Remembrance(
        id="kyoto_animation",
        title="Kyoto Animation Remembrance",
        message="Honor the memory of those lost in the Kyoto Animation arson attack.",
        date="07-18",
        default_regions=("JP", "US"),
    ),

    # Japan
    Remembrance(
        id="great_east_japan_earthquake",
        title="Great East Japan Earthquake Remembrance",
        message=(
            "Remember those lost in the Great East Japan Earthquake "
            "and tsunami."
        ),
        date="03-11",
        default_regions=("JP",),
    ),
    Remembrance(
        id="hiroshima",
        title="Hiroshima Remembrance",
        message="Remember the victims of the atomic bombing of Hiroshima.",
        date="08-06",
        default_regions=("JP",),
    ),
    Remembrance(
        id="nagasaki",
        title="Nagasaki Remembrance",
        message="Remember the victims of the atomic bombing of Nagasaki.",
        date="08-09",
        default_regions=("JP",),
    ),

    # United Kingdom
    Remembrance(
        id="grenfell",
        title="Grenfell Tower Remembrance",
        message="Remember those lost in the Grenfell Tower fire.",
        date="06-14",
        default_regions=("GB",),
    ),
    Remembrance(
        id="manchester_arena",
        title="Manchester Arena Remembrance",
        message="Remember those lost in the Manchester Arena attack.",
        date="05-22",
        default_regions=("GB",),
    ),
    Remembrance(
        id="london_7_7",
        title="7 July Remembrance",
        message="Remember those lost in the 7 July 2005 London attacks.",
        date="07-07",
        default_regions=("GB",),
    ),

    # New Zealand
    Remembrance(
        id="christchurch",
        title="Christchurch Remembrance",
        message="Remember those lost in the Christchurch mosque attacks.",
        date="03-15",
        default_regions=("NZ",),
    ),

    # Australia
    Remembrance(
        id="port_arthur",
        title="Port Arthur Remembrance",
        message="Remember those lost in the Port Arthur tragedy.",
        date="04-28",
        default_regions=("AU",),
    ),
    Remembrance(
        id="black_saturday",
        title="Black Saturday Remembrance",
        message="Remember those lost in the Black Saturday bushfires.",
        date="02-07",
        default_regions=("AU",),
    ),

    # Canada
    Remembrance(
        id="ecole_polytechnique",
        title="École Polytechnique Remembrance",
        message="Remember the victims of the École Polytechnique massacre.",
        date="12-06",
        default_regions=("CA",),
    ),
    Remembrance(
        id="air_india_182",
        title="Victims of Terrorism Remembrance",
        message=(
            "Remember the victims of terrorism, including those lost "
            "on Air India Flight 182."
        ),
        date="06-23",
        default_regions=("CA",),
    ),

    # Norway
    Remembrance(
        id="norway_2011",
        title="22 July Remembrance",
        message="Remember those lost in the attacks of July 22, 2011.",
        date="07-22",
        default_regions=("NO",),
    ),

    # France
    Remembrance(
        id="paris_2015",
        title="Paris Attacks Remembrance",
        message="Remember those lost in the November 2015 Paris attacks.",
        date="11-13",
        default_regions=("FR",),
    ),

    # Spain
    Remembrance(
        id="madrid_2004",
        title="Madrid Remembrance",
        message="Remember those lost in the 2004 Madrid train bombings.",
        date="03-11",
        default_regions=("ES",),
    ),

    # South Korea
    Remembrance(
        id="sewol",
        title="Sewol Ferry Remembrance",
        message="Remember those lost in the Sewol ferry disaster.",
        date="04-16",
        default_regions=("KR",),
    ),
    Remembrance(
        id="itaewon",
        title="Itaewon Remembrance",
        message="Remember those lost in the Itaewon tragedy.",
        date="10-29",
        default_regions=("KR",),
    ),

    # 2004 Indian Ocean tsunami
    Remembrance(
        id="indian_ocean_tsunami",
        title="Indian Ocean Tsunami Remembrance",
        message="Remember those lost in the 2004 Indian Ocean tsunami.",
        date="12-26",
        default_regions=("ID", "TH", "LK", "IN", "MV"),
    ),

    # India
    Remembrance(
        id="mumbai_2008",
        title="Mumbai Attacks Remembrance",
        message="Remember those lost in the 2008 Mumbai attacks.",
        date="11-26",
        default_regions=("IN",),
    ),

    # Lebanon
    Remembrance(
        id="beirut_port",
        title="Beirut Port Remembrance",
        message="Remember those lost in the Beirut port explosion.",
        date="08-04",
        default_regions=("LB",),
    ),

    # Mexico
    Remembrance(
        id="mexico_city_earthquake",
        title="Mexico City Earthquake Remembrance",
        message="Remember those lost in the 1985 Mexico City earthquake.",
        date="09-19",
        default_regions=("MX",),
    ),
)


# ---------------------------------------------------------------------------
# Region detection
# ---------------------------------------------------------------------------

def extract_region(locale_name: Optional[str]) -> Optional[str]:
    """
    Extract a two-letter region from locale strings such as:

        en_US.UTF-8
        en-US
        ja_JP
        ko_KR.UTF-8
    """

    if not locale_name:
        return None

    # Remove encoding/modifier.
    cleaned = locale_name.split(".", 1)[0].split("@", 1)[0]

    match = re.search(r"[_-]([A-Za-z]{2})$", cleaned)
    if match:
        return match.group(1).upper()

    return None


def get_region() -> Optional[str]:
    """
    Try several common Linux locale sources.
    """

    # Environment variables are normally the most useful because they
    # represent the user's current session rather than the machine default.
    for variable in ("LC_ALL", "LC_MESSAGES", "LANG"):
        region = extract_region(os.environ.get(variable))
        if region:
            return region

    # Python locale configuration.
    try:
        language, _encoding = locale.getlocale()
        region = extract_region(language)
        if region:
            return region
    except Exception:
        pass

    # Linux distributions using /etc/locale.conf.
    try:
        with open("/etc/locale.conf", "r", encoding="utf-8") as file:
            for line in file:
                if line.startswith("LANG="):
                    value = line.partition("=")[2].strip().strip('"')
                    region = extract_region(value)
                    if region:
                        return region
    except OSError:
        pass

    return None


# ---------------------------------------------------------------------------
# D-Bus notifications
# ---------------------------------------------------------------------------

async def send_notification(
    notifications_interface,
    title: str,
    message: str,
) -> None:
    await notifications_interface.call_notify(
        "Remembrance",
        0,      # replaces_id
        "",     # app_icon
        title,
        message,
        [],     # actions
        {},     # hints
        -1,     # timeout: let notification server decide
    )


async def main() -> None:
    region = get_region()

    if region is None:
        print("Unable to determine system region.")
        print("No remembrance notifications will be sent.")
        return

    print(f"Detected region: {region}")

    enabled = [
        remembrance
        for remembrance in REMEMBRANCES
        if region in remembrance.default_regions
    ]

    if not enabled:
        print(f"No default remembrance notifications configured for {region}.")
        return

    bus = await MessageBus(bus_type=BusType.SESSION).connect()

    introspection = await bus.introspect(
        "org.freedesktop.Notifications",
        "/org/freedesktop/Notifications",
    )

    proxy = bus.get_proxy_object(
        "org.freedesktop.Notifications",
        "/org/freedesktop/Notifications",
        introspection,
    )

    notifications = proxy.get_interface(
        "org.freedesktop.Notifications"
    )

    print(
        f"Sending {len(enabled)} default remembrance "
        f"notification(s)..."
    )

    # Prototype behavior:
    #
    # We intentionally DO NOT check remembrance.date yet.
    # Every notification enabled by default in the detected region fires
    # immediately.
    for remembrance in enabled:
        print(
            f"  {remembrance.date}: "
            f"{remembrance.title}"
        )

        await send_notification(
            notifications,
            remembrance.title,
            remembrance.message,
        )

        # Small gap so notification daemons are less likely to collapse
        # several messages into one visual burst.
        await asyncio.sleep(0.3)

    bus.disconnect()


if __name__ == "__main__":
    asyncio.run(main())