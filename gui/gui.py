#!/usr/bin/env python3
from __future__ import annotations

import math
import os
import sys
import time
from collections import deque
from dataclasses import dataclass
from typing import Optional

import pygame


SCREEN_WIDTH = int(os.getenv("PLANE_TRACKER_SCREEN_WIDTH", "480"))
SCREEN_HEIGHT = int(os.getenv("PLANE_TRACKER_SCREEN_HEIGHT", "320"))
FULLSCREEN = os.getenv("PLANE_TRACKER_FULLSCREEN", "1") != "0"
ASSET_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets")

GPS_REFRESH_SECONDS = float(os.getenv("PLANE_TRACKER_GPS_REFRESH_SECONDS", "10"))
AIRCRAFT_REFRESH_SECONDS = float(os.getenv("PLANE_TRACKER_AIRCRAFT_REFRESH_SECONDS", "1"))
STATUS_FLASH_SECONDS = 0.35
GPS_STALE_SECONDS = float(os.getenv("PLANE_TRACKER_GPS_STALE_SECONDS", "1200"))
ADSB_STALE_SECONDS = float(os.getenv("PLANE_TRACKER_ADSB_STALE_SECONDS", "60"))
TRACK_HISTORY_POINTS = max(1, int(os.getenv("PLANE_TRACKER_TRACK_HISTORY_POINTS", "120")))
TRACK_HISTORY_SECONDS = max(1.0, float(os.getenv("PLANE_TRACKER_TRACK_HISTORY_SECONDS", "600")))

RANGE_STEPS_KM = (5, 10, 20, 40, 80, 160, 320, 640, 1280)
DEFAULT_RANGE_KM = float(os.getenv("PLANE_TRACKER_RANGE_KM", "80"))

BG = (0, 0, 0)
PANEL = (0, 0, 0)
PANEL_2 = (14, 14, 14)
LINE = (72, 72, 72)
GRID = (190, 190, 190)
GRID_DIM = (62, 62, 62)
TEXT = (235, 235, 235)
TEXT_DIM = (180, 180, 180)
GREEN = (56, 224, 145)
TRAIL = (24, 105, 70)
AMBER = (242, 187, 72)
RED = (242, 82, 82)
CYAN = (72, 207, 224)
BLUE = (75, 133, 235)
HOME = (255, 0, 0)
BLACK = (0, 0, 0)


@dataclass
class Position:
    lat: float
    lon: float


@dataclass
class PlaneTarget:
    flight: str
    hex_ident: str
    lat: float
    lon: float
    distance_km: float
    bearing_deg: float
    altitude_ft: Optional[int]
    speed_kt: Optional[int]
    track_deg: Optional[int]
    seen_seconds: Optional[float]
    squawk: Optional[str] = None
    category: Optional[str] = None
    vertical_rate_fpm: Optional[int] = None
    nav_altitude_ft: Optional[int] = None
    emergency: Optional[str] = None
    indicated_speed_kt: Optional[int] = None
    heading_deg: Optional[int] = None
    nav_qnh_hpa: Optional[float] = None
    nav_modes: tuple[str, ...] = ()
    rssi_dbfs: Optional[float] = None
    on_ground: bool = False
    messages: Optional[int] = None


@dataclass
class Button:
    label: str
    rect: pygame.Rect
    action: str
    active: bool = False


class DemoDataSource:
    def __init__(self) -> None:
        self.home = Position(51.4700, -0.4543)
        self.active_aircraft_count = 5
        self.adsb_messages_updated = False

    def get_position(self) -> Optional[Position]:
        drift_km = (time.monotonic() % 120) * 0.002
        lat, lon = offset_position(self.home.lat, self.home.lon, drift_km, 72.0)
        return Position(lat, lon)

    def get_targets(self, ownship: Optional[Position], range_km: float) -> list[PlaneTarget]:
        self.adsb_messages_updated = True
        if ownship is None:
            return []

        now = time.monotonic()
        targets: list[PlaneTarget] = []
        demo_specs = [
            ("BAW42", "4008F3", 13.5, 40.0, 238, 31000, 410, -640),
            ("EZY91K", "406B59", 27.0, 132.0, 186, 22000, 355, 1280),
            ("RYR7GH", "4CA8E7", 44.0, 284.0, 95, 38000, 465, 0),
            ("VIR12", "407D5C", 8.0, 210.0, 18, 4200, 190, -960),
            ("DLH3PR", "3C65C1", 61.0, 18.0, 204, 35000, 430, 0),
        ]

        for index, (flight, hex_ident, base_dist, bearing, track, altitude, speed, vertical_rate) in enumerate(demo_specs):
            animated_bearing = (bearing + (now * (2 + index)) % 360) % 360
            distance = base_dist
            lat, lon = offset_position(ownship.lat, ownship.lon, distance, animated_bearing)
            targets.append(
                PlaneTarget(
                    flight=flight,
                    hex_ident=hex_ident,
                    lat=lat,
                    lon=lon,
                    distance_km=distance,
                    bearing_deg=animated_bearing,
                    altitude_ft=altitude,
                    speed_kt=speed,
                    track_deg=track,
                    seen_seconds=0.4,
                    squawk="7000",
                    category="A3",
                    vertical_rate_fpm=vertical_rate,
                    nav_altitude_ft=altitude if vertical_rate == 0 else altitude + (2000 if vertical_rate > 0 else -2000),
                    emergency="none",
                    indicated_speed_kt=max(120, speed - 70),
                    heading_deg=track + 3,
                    nav_qnh_hpa=1013.2,
                    rssi_dbfs=-24.5,
                    messages=int(now * 10) + index,
                )
            )

        return targets


class PlaneTrackerTouchUI:
    def __init__(self, data_source: Optional[object] = None) -> None:
        pygame.init()
        pygame.display.set_caption("PlaneTracker Mini")
        self.screen = pygame.display.set_mode((SCREEN_WIDTH, SCREEN_HEIGHT))

        self.clock = pygame.time.Clock()
        self.font_xs = pygame.font.SysFont("dejavusansmono", 8)
        self.font_sm = pygame.font.SysFont("dejavusansmono", 10)
        self.font_md = pygame.font.SysFont("dejavusansmono", 12, bold=True)
        self.font_lg = pygame.font.SysFont("dejavusansmono", 16, bold=True)

        # Reserve one third of the display for information, then give the radar
        # equal width and height. On the target 480x320 display this is 320x320.
        preferred_panel_width = max(120, SCREEN_WIDTH // 3)
        radar_size = min(SCREEN_HEIGHT, SCREEN_WIDTH - preferred_panel_width)
        self.radar_rect = pygame.Rect(0, 0, radar_size, radar_size)
        self.side_rect = pygame.Rect(self.radar_rect.right, 0, SCREEN_WIDTH - self.radar_rect.width, SCREEN_HEIGHT)

        self.range_index = closest_range_index(DEFAULT_RANGE_KM)
        self.show_labels = True
        self.running = True
        self.selected: Optional[PlaneTarget] = None
        self.manual_selection_ident: Optional[str] = None
        self.plane_points: list[tuple[PlaneTarget, int, int]] = []

        self.previous_ownship: Optional[Position] = None
        self.ownship: Optional[Position] = None
        self.ownship_heading_deg: Optional[float] = None
        self.planes: list[PlaneTarget] = []
        self.track_history: dict[str, deque[tuple[float, float, float]]] = {}
        self.gps_ok = False
        self.adsb_ok = False
        self.gps_connected = False
        self.adsb_connected = False
        self.last_gps_update: Optional[float] = None
        self.last_adsb_message_update: Optional[float] = None
        self.gps_flash_until = 0.0
        self.adsb_flash_until = 0.0
        self.last_message_counts: dict[str, int] = {}
        self.last_gps_attempt = 0.0
        self.last_aircraft_attempt = 0.0
        self.last_aircraft_count = 0

        self.data_source = data_source or DemoDataSource()
        self.buttons = self._build_buttons()
        self.button_images = self._load_button_images()

    @property
    def range_km(self) -> float:
        return float(RANGE_STEPS_KM[self.range_index])

    def run(self) -> None:
        while self.running:
            self._handle_events()
            self._refresh_data()
            self._draw()
            pygame.display.flip()
            self.clock.tick(30)

        close = getattr(self.data_source, "close", None)
        if close is not None:
            close()
        pygame.quit()

    def _build_buttons(self) -> list[Button]:
        margin = 8
        gap = 4
        x = self.side_rect.x + margin
        y = self.side_rect.bottom - margin - 34
        available_width = self.side_rect.width - (margin * 2) - (gap * 3)
        button_width = available_width // 4
        final_button_width = available_width - button_width * 3
        button_height = 34
        return [
            Button("zoom_in", pygame.Rect(x, y, button_width, button_height), "zoom_in"),
            Button("zoom_out", pygame.Rect(x + (button_width + gap), y, button_width, button_height), "zoom_out"),
            Button("labels", pygame.Rect(x + (button_width + gap) * 2, y, button_width, button_height), "toggle_labels", active=True),
            Button("exit", pygame.Rect(x + (button_width + gap) * 3, y, final_button_width, button_height), "exit"),
        ]

    def _load_button_images(self) -> dict[str, pygame.Surface]:
        filenames = {
            "zoom_in": "zoom_in.png",
            "zoom_out": "zoom_out.png",
            "hide_labels": "hide_labels.png",
            "show_labels": "show_labels.png",
            "exit": "off.png",
        }
        return {
            name: pygame.image.load(os.path.join(ASSET_DIR, filename)).convert_alpha()
            for name, filename in filenames.items()
        }

    def _handle_events(self) -> None:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                self.running = False
            elif event.type == pygame.KEYDOWN:
                if event.key in (pygame.K_ESCAPE, pygame.K_q):
                    self.running = False
                elif event.key in (pygame.K_PLUS, pygame.K_EQUALS):
                    self._zoom(-1)
                elif event.key in (pygame.K_MINUS, pygame.K_UNDERSCORE):
                    self._zoom(1)
                elif event.key == pygame.K_t:
                    self.show_labels = not self.show_labels
            elif event.type == pygame.MOUSEBUTTONDOWN:
                self._handle_touch(event.pos)

    def _handle_touch(self, pos: tuple[int, int]) -> None:
        for button in self.buttons:
            if button.rect.collidepoint(pos):
                if button.action == "zoom_in":
                    self._zoom(-1)
                elif button.action == "zoom_out":
                    self._zoom(1)
                elif button.action == "toggle_labels":
                    self.show_labels = not self.show_labels
                elif button.action == "exit":
                    self.running = False
                return

        if self.radar_rect.collidepoint(pos):
            self.selected = self._nearest_plane(pos)
            self.manual_selection_ident = self.selected.hex_ident if self.selected is not None else None

    def _zoom(self, direction: int) -> None:
        self.range_index = max(0, min(len(RANGE_STEPS_KM) - 1, self.range_index + direction))
        self.last_aircraft_attempt = 0.0

    def _nearest_plane(self, pos: tuple[int, int]) -> Optional[PlaneTarget]:
        best: Optional[PlaneTarget] = None
        best_dist = 18.0
        px, py = pos
        for plane, x, y in self.plane_points:
            dist = math.hypot(px - x, py - y)
            if dist <= best_dist:
                best = plane
                best_dist = dist
        return best

    def _refresh_data(self) -> None:
        now = time.monotonic()

        if now - self.last_gps_attempt >= GPS_REFRESH_SECONDS:
            self.last_gps_attempt = now
            try:
                position = self.data_source.get_position()
                self.gps_connected = True
                if position is not None:
                    self._update_ownship_heading(position)
                    self.ownship = position
                    self.last_gps_update = now
                    self.gps_flash_until = now + STATUS_FLASH_SECONDS
            except OSError:
                self.gps_connected = False

        if now - self.last_aircraft_attempt >= AIRCRAFT_REFRESH_SECONDS:
            self.last_aircraft_attempt = now
            try:
                self.planes = self.data_source.get_targets(self.ownship, self.range_km)
                self.adsb_connected = True
                self.last_aircraft_count = int(
                    getattr(self.data_source, "active_aircraft_count", len(self.planes))
                )
                self.adsb_ok = True
                self._update_track_history(now)
                self._update_adsb_flash(
                    now,
                    getattr(self.data_source, "adsb_messages_updated", None),
                )
                if self.manual_selection_ident is not None:
                    self.selected = next(
                        (plane for plane in self.planes if plane.hex_ident == self.manual_selection_ident),
                        None,
                    )
                    if self.selected is None:
                        self.manual_selection_ident = None
                if self.manual_selection_ident is None:
                    self.selected = min(self.planes, key=lambda plane: plane.distance_km, default=None)
            except OSError:
                self.adsb_connected = False

        self._update_connection_status(now)

    def _update_adsb_flash(self, now: float, feed_updated: Optional[bool] = None) -> None:
        current_counts = {
            plane.hex_ident: plane.messages
            for plane in self.planes
            if plane.messages is not None
        }
        targets_updated = any(
            ident not in self.last_message_counts or count > self.last_message_counts[ident]
            for ident, count in current_counts.items()
        )
        messages_updated = feed_updated if feed_updated is not None else targets_updated
        if messages_updated:
            self.last_adsb_message_update = now
            self.adsb_flash_until = now + STATUS_FLASH_SECONDS
        self.last_message_counts = current_counts

    def _update_connection_status(self, now: float) -> None:
        self.gps_ok = (
            self.gps_connected
            and self.last_gps_update is not None
            and now - self.last_gps_update <= GPS_STALE_SECONDS
        )
        self.adsb_ok = (
            self.adsb_connected
            and self.last_adsb_message_update is not None
            and now - self.last_adsb_message_update <= ADSB_STALE_SECONDS
        )

    def _update_track_history(self, now: float) -> None:
        active_idents = set()
        for plane in self.planes:
            active_idents.add(plane.hex_ident)
            history = self.track_history.setdefault(plane.hex_ident, deque(maxlen=TRACK_HISTORY_POINTS))
            point = (plane.lat, plane.lon, now)
            if not history or (history[-1][0], history[-1][1]) != (plane.lat, plane.lon):
                history.append(point)
            else:
                history[-1] = point
            while len(history) > 1 and now - history[0][2] > TRACK_HISTORY_SECONDS:
                history.popleft()

        expired = [
            ident
            for ident, history in self.track_history.items()
            if ident not in active_idents and (not history or now - history[-1][2] > TRACK_HISTORY_SECONDS)
        ]
        for ident in expired:
            del self.track_history[ident]

    def _update_ownship_heading(self, position: Position) -> None:
        if self.ownship is not None:
            distance_m = calculate_distance_km(self.ownship.lat, self.ownship.lon, position.lat, position.lon) * 1000
            if distance_m >= 1.0:
                self.ownship_heading_deg = calculate_bearing_deg(self.ownship.lat, self.ownship.lon, position.lat, position.lon)
                self.previous_ownship = self.ownship

    def _draw(self) -> None:
        self.screen.fill(BG)
        self._draw_radar()
        self._draw_side_panel()

    def _draw_radar(self) -> None:
        pygame.draw.rect(self.screen, BLACK, self.radar_rect)
        pygame.draw.rect(self.screen, LINE, self.radar_rect, 1)

        center = self.radar_rect.center
        radius_max = radar_display_radius(self.radar_rect)
        ring_count = 6
        ring_labels: list[tuple[str, tuple[int, int]]] = []
        label_angle = math.radians(50)
        for ring_index in range(1, ring_count):
            fraction = ring_index / ring_count
            radius = int(radius_max * fraction)
            color = GRID if ring_index == ring_count else GRID_DIM
            pygame.draw.circle(self.screen, color, center, radius, 1)
            distance_km = self.range_km * fraction
            # Sine/cosine keeps every label both on its ring and on one
            # straight radial guide, without cumulative diagonal drift.
            label_x = center[0] + int(radius * math.cos(label_angle)) + 3
            label_y = center[1] - int(radius * math.sin(label_angle)) - 5
            if ring_index < ring_count:
                ring_labels.append((format_ring_distance(distance_km), (label_x, label_y)))
        pygame.draw.line(self.screen, GRID_DIM, (center[0], self.radar_rect.y + 12), (center[0], self.radar_rect.bottom - 8), 1)
        pygame.draw.line(self.screen, GRID_DIM, (self.radar_rect.x + 8, center[1]), (self.radar_rect.right - 8, center[1]), 1)
        draw_text_fit(self.screen, self.font_xs, "N", (center[0] - 1.5, self.radar_rect.y + 2), 12, TEXT_DIM)

        for label, position in ring_labels:
            label_surface = self.font_xs.render(label, True, TEXT_DIM)
            label_rect = label_surface.get_rect(topleft=position).inflate(2, 0)
            pygame.draw.rect(self.screen, BLACK, label_rect)
            self.screen.blit(label_surface, position)

        pygame.draw.circle(self.screen, HOME, center, 3)
        self.plane_points = []
        if self.ownship is not None:
            self._draw_track_history()
            for plane in self.planes:
                point = coords_to_radar_xy(plane, self.ownship, self.radar_rect, self.range_km)
                if point is None:
                    continue
                self.plane_points.append((plane, point[0], point[1]))
                self._draw_plane(plane, point)

        if self.ownship is None:
            text = self.font_sm.render("NO GPS!", True, AMBER)
            self.screen.blit(text, text.get_rect(center=(center[0], center[1] + 28)))

    def _draw_track_history(self) -> None:
        if self.ownship is None:
            return

        visible_idents = {plane.hex_ident for plane in self.planes}
        for ident, history in self.track_history.items():
            if ident not in visible_idents:
                continue
            previous: Optional[tuple[int, int]] = None
            for lat, lon, _timestamp in history:
                point = position_to_radar_xy(lat, lon, self.ownship, self.radar_rect, self.range_km)
                if point is not None and previous is not None:
                    pygame.draw.line(self.screen, TRAIL, previous, point, 1)
                previous = point

    def _draw_plane(self, plane: PlaneTarget, point: tuple[int, int]) -> None:
        x, y = point
        selected = self.selected is not None and self.selected.hex_ident == plane.hex_ident
        color = GREEN
        if selected:
            pygame.draw.circle(self.screen, RED, (x, y), 9, 1)

        self._draw_plane_marker(x, y, plane.track_deg, color)
        if self.show_labels:
            label = format_plane_label(plane)
            draw_text_fit(self.screen, self.font_xs, label, (x + 12, y - 6), 64, TEXT)

    def _draw_plane_marker(self, x: int, y: int, track_deg: Optional[int], color: tuple[int, int, int]) -> None:
        angle = math.radians((track_deg if track_deg is not None else 0) - 90)
        nose = (x + int(math.cos(angle) * 6), y + int(math.sin(angle) * 6))
        left = (x + int(math.cos(angle + 2.45) * 4), y + int(math.sin(angle + 2.45) * 4))
        right = (x + int(math.cos(angle - 2.45) * 4), y + int(math.sin(angle - 2.45) * 4))
        pygame.draw.polygon(self.screen, color, (nose, left, right))

    def _draw_side_panel(self) -> None:
        pygame.draw.rect(self.screen, PANEL, self.side_rect)
        pygame.draw.rect(self.screen, LINE, self.side_rect, 1)

        x = self.side_rect.x + 10
        y = self.side_rect.y + 8
        width = self.side_rect.width - 20

        draw_text_fit(self.screen, self.font_lg, time.strftime("%H:%M:%S"), (x, y + 4), 92, TEXT)
        status_width = 42
        self._draw_status_dots(self.side_rect.right - 8 - status_width, y + 3)
        y += 31

        self._draw_metric_row(
            x,
            y,
            (("Active", str(self.last_aircraft_count)), ("Range", f"{self.range_km:g}km"), ("Labels", "On" if self.show_labels else "Off")),
            width,
        )
        y += 30

        latitude = f"{self.ownship.lat:.5f}" if self.ownship is not None else "-"
        longitude = f"{self.ownship.lon:.5f}" if self.ownship is not None else "-"
        coordinate_width = (width - 8) // 2
        draw_text_fit(self.screen, self.font_xs, "LAT", (x, y), coordinate_width, TEXT_DIM)
        draw_text_fit(self.screen, self.font_xs, "LON", (x + coordinate_width + 8, y), coordinate_width, TEXT_DIM)
        draw_text_fit(self.screen, self.font_sm, latitude, (x, y + 10), coordinate_width, TEXT)
        draw_text_fit(self.screen, self.font_sm, longitude, (x + coordinate_width + 8, y + 10), coordinate_width, TEXT)
        y += 27

        pygame.draw.line(self.screen, LINE, (x, y), (self.side_rect.right - 10, y), 1)
        y += 8
        self._draw_selected_plane(x, y)

        for button in self.buttons:
            button.active = self.show_labels if button.action == "toggle_labels" else False
            self._draw_button(button)

    def _draw_status_dots(self, x: int, y: int) -> None:
        now = time.monotonic()
        self._update_connection_status(now)
        gps_text_color = GREEN if now < self.gps_flash_until else (TEXT if self.gps_ok else RED)
        adsb_text_color = GREEN if now < self.adsb_flash_until else (TEXT if self.adsb_ok else RED)
        gps_center_y = y + 4
        adsb_center_y = y + 17
        text_offset = self.font_xs.get_height() // 2
        pygame.draw.circle(self.screen, GREEN if self.gps_ok else RED, (x, gps_center_y), 4)
        draw_text_fit(self.screen, self.font_xs, "GPS", (x + 7, gps_center_y - text_offset), 30, gps_text_color)
        pygame.draw.circle(self.screen, GREEN if self.adsb_ok else RED, (x, adsb_center_y), 4)
        draw_text_fit(self.screen, self.font_xs, "ADS-B", (x + 7, adsb_center_y - text_offset), 30, adsb_text_color)

    def _draw_metric_row(self, x: int, y: int, metrics: tuple[tuple[str, str], ...], width: int) -> None:
        column_width = width // len(metrics)
        for index, (label, value) in enumerate(metrics):
            column_x = x + index * column_width
            draw_text_fit(self.screen, self.font_xs, label, (column_x, y), column_width - 3, TEXT_DIM)
            draw_text_fit(self.screen, self.font_md, value, (column_x, y + 12), column_width - 3, TEXT)

    def _draw_selected_plane(self, x: int, y: int) -> None:
        if self.selected is None:
            if self.last_aircraft_count == 0:
                draw_text_fit(self.screen, self.font_md, "No aircraft", (x, y), 128, TEXT_DIM)
            return

        plane = self.selected
        title = plane.flight if plane.flight != "?" else plane.hex_ident
        draw_text_fit(self.screen, self.font_lg, title, (x, y), 80, GREEN)
        draw_text_fit(self.screen, self.font_sm, plane.hex_ident, (x + 82, y + 3), 48, TEXT)
        y += 20
        rows = (
            ("Altitude", "GND" if plane.on_ground else format_optional(plane.altitude_ft, "ft"), "V/S", format_vertical_rate(plane.vertical_rate_fpm)),
            ("Ground spd", format_optional(plane.speed_kt, "kt"), "IAS", format_optional(plane.indicated_speed_kt, "kt")),
            ("Track", format_heading(plane.track_deg), "Heading", format_heading(plane.heading_deg)),
            ("Squawk / CAT", format_squawk_category(plane.squawk, plane.category), "Emergency", format_emergency(plane.emergency)),
            ("Selected", format_optional(plane.nav_altitude_ft, "ft"), "QNH", format_decimal(plane.nav_qnh_hpa, "hPa", 1)),
            ("Signal", format_decimal(plane.rssi_dbfs, "dB", 1), "Seen", format_decimal(plane.seen_seconds, "s", 1)),
        )
        for left_label, left_value, right_label, right_value in rows:
            self._draw_compact_pair(x, y, left_label, left_value, right_label, right_value)
            y += 24

    def _draw_compact_pair(self, x: int, y: int, left_label: str, left_value: str, right_label: str, right_value: str) -> None:
        draw_text_fit(self.screen, self.font_xs, left_label, (x, y), 62, TEXT_DIM)
        draw_text_fit(self.screen, self.font_xs, left_value, (x, y + 9), 62, TEXT)
        draw_text_fit(self.screen, self.font_xs, right_label, (x + 70, y), 58, TEXT_DIM)
        draw_text_fit(self.screen, self.font_xs, right_value, (x + 70, y + 9), 58, TEXT)

    def _draw_button(self, button: Button) -> None:
        image_name = button.action
        if button.action == "toggle_labels":
            image_name = "hide_labels" if self.show_labels else "show_labels"
        image = self.button_images[image_name]
        max_size = (min(image.get_width(), button.rect.width), min(image.get_height(), button.rect.height))
        if image.get_size() != max_size:
            image = pygame.transform.smoothscale(image, max_size)
        self.screen.blit(image, image.get_rect(center=button.rect.center))


def draw_text_fit(
    surface: pygame.Surface,
    font: pygame.font.Font,
    text: str,
    pos: tuple[int, int],
    max_width: int,
    color: tuple[int, int, int],
) -> None:
    fitted = fit_text(font, text, max_width)
    surface.blit(font.render(fitted, True, color), pos)


def fit_text(font: pygame.font.Font, text: str, max_width: int) -> str:
    if font.size(text)[0] <= max_width:
        return text
    if max_width <= font.size(".")[0]:
        return ""
    clipped = text
    while clipped and font.size(clipped + ".")[0] > max_width:
        clipped = clipped[:-1]
    return clipped + "." if clipped else ""


def closest_range_index(value: float) -> int:
    return min(range(len(RANGE_STEPS_KM)), key=lambda index: abs(RANGE_STEPS_KM[index] - value))


def optional_int(value: object) -> Optional[int]:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def optional_float(value: object) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def calculate_distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    earth_radius_km = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    return earth_radius_km * (2 * math.atan2(math.sqrt(a), math.sqrt(1 - a)))


def calculate_bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dlon = math.radians(lon2 - lon1)
    y = math.sin(dlon) * math.cos(phi2)
    x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlon)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def offset_position(lat: float, lon: float, distance_km: float, bearing_deg: float) -> tuple[float, float]:
    radius_km = 6371.0
    angular_distance = distance_km / radius_km
    bearing = math.radians(bearing_deg)
    lat1 = math.radians(lat)
    lon1 = math.radians(lon)

    lat2 = math.asin(
        math.sin(lat1) * math.cos(angular_distance)
        + math.cos(lat1) * math.sin(angular_distance) * math.cos(bearing)
    )
    lon2 = lon1 + math.atan2(
        math.sin(bearing) * math.sin(angular_distance) * math.cos(lat1),
        math.cos(angular_distance) - math.sin(lat1) * math.sin(lat2),
    )
    return math.degrees(lat2), math.degrees(lon2)


def coords_to_radar_xy(plane: PlaneTarget, ownship: Position, radar_rect: pygame.Rect, range_km: float) -> Optional[tuple[int, int]]:
    return position_to_radar_xy(plane.lat, plane.lon, ownship, radar_rect, range_km)


def position_to_radar_xy(
    lat: float,
    lon: float,
    ownship: Position,
    radar_rect: pygame.Rect,
    range_km: float,
) -> Optional[tuple[int, int]]:
    radius_max = radar_display_radius(radar_rect)
    dy_km = (lat - ownship.lat) * 111.0
    dx_km = (lon - ownship.lon) * 111.0 * math.cos(math.radians(ownship.lat))
    x = radar_rect.centerx + int((dx_km / range_km) * radius_max)
    y = radar_rect.centery - int((dy_km / range_km) * radius_max)
    if not radar_rect.inflate(-8, -8).collidepoint(x, y):
        return None
    return x, y


def radar_display_radius(radar_rect: pygame.Rect) -> int:
    return int(min(radar_rect.width, radar_rect.height) * 0.70)


def format_optional(value: Optional[int], suffix: str) -> str:
    return "-" if value is None else f"{value}{suffix}"


def format_decimal(value: Optional[float], suffix: str, precision: int) -> str:
    return "-" if value is None else f"{value:.{precision}f}{suffix}"


def format_vertical_rate(value: Optional[int]) -> str:
    if value is None:
        return "-"
    return f"{value:+d}fpm"


def format_emergency(value: Optional[str]) -> str:
    if not value or value in ("none", "-"):
        return "-"
    return value.upper()


def format_squawk_category(squawk: Optional[str], category: Optional[str]) -> str:
    return f"{squawk or '-'} / {category or '-'}"


def format_plane_label(plane: PlaneTarget) -> str:
    name = plane.flight if plane.flight != "?" else plane.hex_ident
    return name[:8]


def format_heading(value: Optional[float]) -> str:
    return "-" if value is None else f"{value:03.0f} deg"


def format_distance(value: float) -> str:
    if math.isinf(value):
        return "-"
    return f"{value:.1f} km"


def format_ring_distance(value: float) -> str:
    return f"{round(value):d}km"


def main() -> int:
    try:
        PlaneTrackerTouchUI().run()
    except KeyboardInterrupt:
        pygame.quit()
    return 0


if __name__ == "__main__":
    sys.exit(main())
