#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import socket
import time
import urllib.error
import urllib.request
from typing import Optional

from gui.gui import (
    PlaneTarget,
    PlaneTrackerTouchUI,
    Position,
    calculate_bearing_deg,
    calculate_distance_km,
    optional_float,
    optional_int,
)


GPSD_HOST = os.getenv("GPSD_HOST", "127.0.0.1")
GPSD_PORT = int(os.getenv("GPSD_PORT", "2947"))
AIRCRAFT_SOURCE = os.getenv("AIRCRAFT_SOURCE", "/run/dump1090-mutability/aircraft.json")
SOCKET_TIMEOUT = 2.0
MAX_PLANES = 80


class GPSDClient:
    def __init__(self, host: str, port: int) -> None:
        self.host = host
        self.port = port
        self.sock: Optional[socket.socket] = None
        self.file = None
        self.last_position: Optional[Position] = None

    def connect(self) -> None:
        self.close()
        self.sock = socket.create_connection((self.host, self.port), timeout=SOCKET_TIMEOUT)
        self.sock.settimeout(SOCKET_TIMEOUT)
        self.file = self.sock.makefile("r", encoding="utf-8", errors="replace")
        self.sock.sendall(b'?WATCH={"enable":true,"json":true};\n')

    def close(self) -> None:
        if self.file is not None:
            try:
                self.file.close()
            except OSError:
                pass
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
        self.file = None
        self.sock = None

    def get_position(self) -> Optional[Position]:
        if self.sock is None or self.file is None:
            self.connect()

        deadline = time.monotonic() + SOCKET_TIMEOUT
        while time.monotonic() < deadline:
            line = self.file.readline()
            if not line:
                raise ConnectionError("gpsd closed the connection")
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            if message.get("class") != "TPV":
                continue
            if int(message.get("mode", 0) or 0) < 2:
                continue
            lat = message.get("lat")
            lon = message.get("lon")
            if lat is None or lon is None:
                continue
            self.last_position = Position(float(lat), float(lon))
            return self.last_position

        # No fresh TPV arrived during this request. Keep the cached position
        # internally, but do not report it as a new coordinate update.
        return None


class AircraftFeed:
    def __init__(self, source: str) -> None:
        self.source = source

    def _load_payload(self) -> dict:
        if self.source.startswith(("http://", "https://")):
            request = urllib.request.Request(self.source, headers={"User-Agent": "plane-tracker-mini/2.0"})
            with urllib.request.urlopen(request, timeout=SOCKET_TIMEOUT) as response:
                return json.load(response)

        with open(self.source, "r", encoding="utf-8") as response:
            return json.load(response)

    def get_targets(self, ownship: Optional[Position], range_km: float) -> list[PlaneTarget]:
        payload = self._load_payload()
        targets: list[PlaneTarget] = []

        for aircraft in payload.get("aircraft", []):
            lat = aircraft.get("lat")
            lon = aircraft.get("lon")
            if lat is None or lon is None:
                continue

            lat_f = float(lat)
            lon_f = float(lon)
            flight = (aircraft.get("flight") or aircraft.get("hex") or "?").strip().upper()[:8]
            hex_ident = (aircraft.get("hex") or "?").strip().upper()[:8]
            barometric_altitude = aircraft.get("alt_baro")
            on_ground = isinstance(barometric_altitude, str) and barometric_altitude.lower() == "ground"
            altitude = None if on_ground else optional_int(
                barometric_altitude if barometric_altitude is not None else aircraft.get("alt_geom")
            )
            nav_modes_value = aircraft.get("nav_modes")
            nav_modes = tuple(str(mode).lower() for mode in nav_modes_value) if isinstance(nav_modes_value, list) else ()

            if ownship is None:
                distance_km = float("inf")
                bearing_deg = 0.0
            else:
                distance_km = calculate_distance_km(ownship.lat, ownship.lon, lat_f, lon_f)
                bearing_deg = calculate_bearing_deg(ownship.lat, ownship.lon, lat_f, lon_f)
                if distance_km > range_km:
                    continue

            targets.append(
                PlaneTarget(
                    flight=flight,
                    hex_ident=hex_ident,
                    lat=lat_f,
                    lon=lon_f,
                    distance_km=distance_km,
                    bearing_deg=bearing_deg,
                    altitude_ft=altitude,
                    speed_kt=optional_int(aircraft.get("gs", aircraft.get("tas"))),
                    track_deg=optional_int(aircraft.get("track")),
                    seen_seconds=optional_float(aircraft.get("seen")),
                    squawk=str(aircraft["squawk"]) if aircraft.get("squawk") is not None else None,
                    category=str(aircraft["category"]) if aircraft.get("category") is not None else None,
                    vertical_rate_fpm=optional_int(
                        aircraft.get("baro_rate") if aircraft.get("baro_rate") is not None else aircraft.get("geom_rate")
                    ),
                    nav_altitude_ft=optional_int(
                        aircraft.get("nav_altitude_mcp")
                        if aircraft.get("nav_altitude_mcp") is not None
                        else aircraft.get("nav_altitude_fms")
                    ),
                    emergency=str(aircraft["emergency"]).lower() if aircraft.get("emergency") is not None else None,
                    indicated_speed_kt=optional_int(aircraft.get("ias")),
                    heading_deg=optional_int(
                        aircraft.get("true_heading")
                        if aircraft.get("true_heading") is not None
                        else aircraft.get("mag_heading")
                    ),
                    nav_qnh_hpa=optional_float(aircraft.get("nav_qnh")),
                    nav_modes=nav_modes,
                    rssi_dbfs=optional_float(aircraft.get("rssi")),
                    on_ground=on_ground,
                    messages=optional_int(aircraft.get("messages")),
                )
            )

        targets.sort(key=lambda item: item.distance_km)
        return targets[:MAX_PLANES]


class ProductionDataSource:
    def __init__(self) -> None:
        self.gps = GPSDClient(GPSD_HOST, GPSD_PORT)
        self.feed = AircraftFeed(AIRCRAFT_SOURCE)

    def get_position(self) -> Optional[Position]:
        try:
            return self.gps.get_position()
        except (OSError, ConnectionError, json.JSONDecodeError):
            self.gps.close()
            raise OSError("GPS unavailable")

    def get_targets(self, ownship: Optional[Position], range_km: float) -> list[PlaneTarget]:
        try:
            return self.feed.get_targets(ownship, range_km)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
            raise OSError("ADS-B feed unavailable") from exc

    def close(self) -> None:
        self.gps.close()


def main() -> int:
    PlaneTrackerTouchUI(ProductionDataSource()).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
