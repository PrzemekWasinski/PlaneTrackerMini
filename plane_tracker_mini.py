#!/usr/bin/env python3
"""Basic PlaneTracker Mini for Raspberry Pi Zero W + Display HAT Mini.

- Ownship position comes from gpsd on localhost:2947
- Aircraft positions come from dump1090-compatible aircraft.json
- Your GPS position stays in the center of the 320x240 display
- Nearby aircraft are drawn as dots based on lat/lon -> pixel conversion
"""

from __future__ import annotations

import json
import math
import os
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Optional

from PIL import Image, ImageDraw
from displayhatmini_lite import DisplayHATMini

SCREEN_WIDTH = 320
SCREEN_HEIGHT = 240
CENTER_X = SCREEN_WIDTH // 2
CENTER_Y = SCREEN_HEIGHT // 2
RANGE_KM = float(os.getenv("PLANE_TRACKER_RANGE_KM", "80"))
GPSD_HOST = os.getenv("GPSD_HOST", "127.0.0.1")
GPSD_PORT = int(os.getenv("GPSD_PORT", "2947"))
AIRCRAFT_SOURCE = os.getenv("AIRCRAFT_SOURCE", "/run/dump1090-mutability/aircraft.json")
REFRESH_SECONDS = float(os.getenv("PLANE_TRACKER_REFRESH_SECONDS", "1.5"))
SOCKET_TIMEOUT = 3.0
MAX_PLANES = 60

BACKGROUND = (4, 12, 8)
GRID = (20, 90, 55)
HOME = (255, 110, 110)
PLANE = (120, 255, 120)
PLANE_NEAR = (255, 240, 120)
STATUS = (150, 255, 180)
STATUS_BAD = (255, 130, 130)
TEXT_DIM = (80, 160, 110)


@dataclass
class Position:
    lat: float
    lon: float


@dataclass
class PlaneTarget:
    flight: str
    lat: float
    lon: float
    distance_km: float


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

        deadline = time.time() + SOCKET_TIMEOUT
        while time.time() < deadline:
            line = self.file.readline()
            if not line:
                raise ConnectionError("gpsd closed the connection")
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            if message.get("class") != "TPV":
                continue
            mode = int(message.get("mode", 0) or 0)
            lat = message.get("lat")
            lon = message.get("lon")
            if mode >= 2 and lat is not None and lon is not None:
                self.last_position = Position(float(lat), float(lon))
                return self.last_position

        return self.last_position


class AircraftFeed:
    def __init__(self, source: str) -> None:
        self.source = source

    def _load_payload(self) -> dict:
        if self.source.startswith(("http://", "https://")):
            request = urllib.request.Request(self.source, headers={"User-Agent": "plane-tracker-mini/1.0"})
            with urllib.request.urlopen(request, timeout=SOCKET_TIMEOUT) as response:
                return json.load(response)

        with open(self.source, "r", encoding="utf-8") as response:
            return json.load(response)

    def get_targets(self, ownship: Optional[Position]) -> list[PlaneTarget]:
        payload = self._load_payload()

        targets: list[PlaneTarget] = []
        for aircraft in payload.get("aircraft", []):
            lat = aircraft.get("lat")
            lon = aircraft.get("lon")
            if lat is None or lon is None:
                continue
            flight = (aircraft.get("flight") or aircraft.get("hex") or "?").strip().upper()[:8]

            if ownship is None:
                distance_km = float("inf")
            else:
                distance_km = calculate_distance_km(ownship.lat, ownship.lon, float(lat), float(lon))
                if distance_km > RANGE_KM:
                    continue

            targets.append(PlaneTarget(flight=flight, lat=float(lat), lon=float(lon), distance_km=distance_km))

        targets.sort(key=lambda item: item.distance_km)
        return targets[:MAX_PLANES]


class RadarDisplay:
    def __init__(self) -> None:
        self.display = DisplayHATMini()
        self.display.set_backlight(0.9)

    def render(self, ownship: Optional[Position], planes: list[PlaneTarget], gps_ok: bool, feed_ok: bool) -> None:
        image = Image.new("RGB", (SCREEN_WIDTH, SCREEN_HEIGHT), BACKGROUND)
        draw = ImageDraw.Draw(image)

        self._draw_grid(draw)
        self._draw_status(draw, ownship, len(planes), gps_ok, feed_ok)
        self._draw_home(draw)
        if ownship is not None:
            self._draw_planes(draw, ownship, planes)

        self.display.display(image)

    def _draw_status(self, draw: ImageDraw.ImageDraw, ownship: Optional[Position], count: int, gps_ok: bool, feed_ok: bool) -> None:
        draw.text((8, 6), f"Planes {count:02d}", fill=STATUS)
        draw.text((8, 20), "GPS OK" if gps_ok else "GPS WAIT", fill=STATUS if gps_ok else STATUS_BAD)
        draw.text((80, 20), "ADS-B OK" if feed_ok else "ADS-B WAIT", fill=STATUS if feed_ok else STATUS_BAD)
        draw.text((200, 20), f"{int(RANGE_KM)} km", fill=TEXT_DIM)
        if ownship is not None:
            draw.text((8, 34), f"{ownship.lat:7.3f} {ownship.lon:7.3f}", fill=TEXT_DIM)
        else:
            draw.text((8, 34), "waiting for position", fill=TEXT_DIM)

    def _draw_grid(self, draw: ImageDraw.ImageDraw) -> None:
        for radius in (30, 60, 90):
            draw.ellipse((CENTER_X - radius, CENTER_Y - radius, CENTER_X + radius, CENTER_Y + radius), outline=GRID, width=1)
        draw.line((CENTER_X, 12, CENTER_X, SCREEN_HEIGHT - 12), fill=GRID)
        draw.line((12, CENTER_Y, SCREEN_WIDTH - 12, CENTER_Y), fill=GRID)

    def _draw_home(self, draw: ImageDraw.ImageDraw) -> None:
        r = 4
        draw.ellipse((CENTER_X - r, CENTER_Y - r, CENTER_X + r, CENTER_Y + r), outline=HOME, fill=HOME)

    def _draw_planes(self, draw: ImageDraw.ImageDraw, ownship: Position, planes: list[PlaneTarget]) -> None:
        for plane in planes:
            x, y = coords_to_xy(plane.lat, plane.lon, ownship.lat, ownship.lon, RANGE_KM)
            color = PLANE_NEAR if plane.distance_km < 20.0 else PLANE
            draw.ellipse((x - 2, y - 2, x + 2, y + 2), outline=color, fill=color)


def coords_to_xy(lat: float, lon: float, center_lat: float, center_lon: float, range_km: float) -> tuple[int, int]:
    km_per_px = (range_km * 2.0) / SCREEN_WIDTH
    dy_km = (lat - center_lat) * 111.0
    dx_km = (lon - center_lon) * 111.0 * math.cos(math.radians(center_lat))
    x = CENTER_X + int(dx_km / km_per_px)
    y = CENTER_Y - int(dy_km / km_per_px)
    return x, y


def calculate_distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    earth_radius_km = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    return earth_radius_km * (2 * math.atan2(math.sqrt(a), math.sqrt(1 - a)))


def main() -> None:
    gps = GPSDClient(GPSD_HOST, GPSD_PORT)
    feed = AircraftFeed(AIRCRAFT_SOURCE)
    radar = RadarDisplay()

    ownship: Optional[Position] = None
    planes: list[PlaneTarget] = []
    gps_ok = False
    feed_ok = False

    while True:
        try:
            ownship = gps.get_position()
            gps_ok = ownship is not None
        except (OSError, ConnectionError, json.JSONDecodeError):
            gps.close()
            gps_ok = False

        try:
            planes = feed.get_targets(ownship)
            feed_ok = True
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
            planes = []
            feed_ok = False

        radar.render(ownship, planes, gps_ok, feed_ok)
        time.sleep(REFRESH_SECONDS)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass



