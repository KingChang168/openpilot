#!/usr/bin/env python3
"""Publish Taiwan freeway TDX events for HUD mode."""

import math
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass

import requests

from openpilot.cereal import messaging
from openpilot.common.realtime import Ratekeeper
from openpilot.common.swaglog import cloudlog

EVENTS_URL = "https://tisvcloud.freeway.gov.tw/history/motc20/LiveEvents.xml"
TRAFFIC_URL = "https://tisvcloud.freeway.gov.tw/history/motc20/LiveTraffic.xml"
UPDATE_INTERVAL = 60.0
MAX_GPS_ACCURACY = 50.0
EVENT_RADIUS_METERS = 3000.0
ON_EVENT_RADIUS_METERS = 300.0
MAX_BEARING_DIFF_DEG = 100.0


@dataclass(frozen=True)
class RoadEvent:
  event_type: int
  description: str
  latitude: float
  longitude: float
  direction: str
  road_name: str
  section_id: str
  update_time: str


@dataclass(frozen=True)
class TrafficStatus:
  speed: float
  travel_time: float
  update_time: str


def _angle_diff(first: float, second: float) -> float:
  diff = abs(first - second) % 360.0
  return diff if diff <= 180.0 else 360.0 - diff


def _distance_meters(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
  lat_scale = 111320.0
  lon_scale = lat_scale * math.cos(math.radians((lat1 + lat2) / 2.0))
  return math.hypot((lat2 - lat1) * lat_scale, (lon2 - lon1) * lon_scale)


def _bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
  dy = lat2 - lat1
  dx = (lon2 - lon1) * math.cos(math.radians((lat1 + lat2) / 2.0))
  return math.degrees(math.atan2(dx, dy)) % 360.0


def _clean_xml(text: str) -> str:
  text = re.sub(r'\sxmlns(:xsi)?="[^"]+"', "", text)
  return re.sub(r'\sxsi:schemaLocation="[^"]+"', "", text)


def _find_text(node: ET.Element, path: str, default: str = "") -> str:
  value = node.findtext(path)
  return (value or default).strip()


def _parse_float(value: str, default: float = 0.0) -> float:
  try:
    return float(value)
  except ValueError:
    return default


def _parse_int(value: str, default: int = 0) -> int:
  try:
    return int(value)
  except ValueError:
    return default


def _parse_point(value: str) -> tuple[float, float] | None:
  match = re.search(r"POINT\s*\(\s*([-+0-9.]+)\s+([-+0-9.]+)\s*\)", value, re.IGNORECASE)
  if match is None:
    return None
  return float(match.group(2)), float(match.group(1))


class TdxClient:
  def __init__(self) -> None:
    self.events: list[RoadEvent] = []
    self.traffic: dict[str, TrafficStatus] = {}
    self.last_update = 0.0
    self.last_update_time = ""

  def update(self) -> None:
    now = time.monotonic()
    if now - self.last_update < UPDATE_INTERVAL:
      return
    self.last_update = now

    try:
      events = self._fetch_events()
      traffic, update_time = self._fetch_traffic()
      self.events = events
      self.traffic = traffic
      self.last_update_time = update_time
    except (requests.RequestException, ET.ParseError, ValueError) as error:
      cloudlog.warning("tdxd update failed: %s", error)

  def _fetch_events(self) -> list[RoadEvent]:
    response = requests.get(EVENTS_URL, timeout=8)
    response.raise_for_status()
    response.encoding = "utf-8"
    root = ET.fromstring(_clean_xml(response.text))

    events: list[RoadEvent] = []
    for event in root.findall(".//LiveEvent"):
      point = _parse_point(_find_text(event, "Positions"))
      if point is None:
        continue

      events.append(RoadEvent(
        event_type=_parse_int(_find_text(event, "EventType")),
        description=_find_text(event, "Description", "TDX event"),
        latitude=point[0],
        longitude=point[1],
        direction=_find_text(event, ".//Direction"),
        road_name=_find_text(event, ".//Road"),
        section_id=_find_text(event, "SectionID"),
        update_time=_find_text(event, "LastUpdateTime"),
      ))
    return events

  def _fetch_traffic(self) -> tuple[dict[str, TrafficStatus], str]:
    response = requests.get(TRAFFIC_URL, timeout=8)
    response.raise_for_status()
    response.encoding = "utf-8"
    root = ET.fromstring(_clean_xml(response.text))
    update_time = _find_text(root, "UpdateTime")

    traffic: dict[str, TrafficStatus] = {}
    for item in root.findall(".//LiveTraffic"):
      section_id = _find_text(item, "SectionID")
      if not section_id:
        continue
      traffic[section_id] = TrafficStatus(
        speed=_parse_float(_find_text(item, "TravelSpeed")),
        travel_time=_parse_float(_find_text(item, "TravelTime")),
        update_time=_find_text(item, "DataCollectTime", update_time),
      )
    return traffic, update_time

  def nearby_events(self, latitude: float, longitude: float, bearing: float) -> list[tuple[float, RoadEvent]]:
    nearby: list[tuple[float, RoadEvent]] = []
    for event in self.events:
      distance = _distance_meters(latitude, longitude, event.latitude, event.longitude)
      if distance > EVENT_RADIUS_METERS:
        continue
      if distance > ON_EVENT_RADIUS_METERS:
        event_bearing = _bearing_deg(latitude, longitude, event.latitude, event.longitude)
        if _angle_diff(bearing, event_bearing) > MAX_BEARING_DIFF_DEG:
          continue
      nearby.append((distance, event))
    return sorted(nearby, key=lambda item: item[0])


def _valid_gps(gps) -> bool:
  return bool(gps.hasFix and (gps.horizontalAccuracy <= 0 or gps.horizontalAccuracy <= MAX_GPS_ACCURACY))


def _publish(pm: messaging.PubMaster, client: TdxClient, gps) -> None:
  message = messaging.new_message("tdx")
  tdx = message.tdx
  traffic_status = tdx.init("trafficStatus")
  road_event = tdx.init("roadEvent")
  tdx.lastUpdateTime = client.last_update_time

  road_event.isActive = False
  road_event.description = ""
  road_event.distance = 0.0
  road_event.latitude = 0.0
  road_event.longitude = 0.0
  road_event.eventType = 0
  road_event.updateTime = client.last_update_time

  traffic_status.isValid = False
  traffic_status.roadName = ""
  traffic_status.distance = 0.0
  traffic_status.speed = 0.0
  traffic_status.travelTime = 0.0
  traffic_status.updateTime = client.last_update_time

  if gps is not None:
    nearby = client.nearby_events(gps.latitude, gps.longitude, gps.bearingDeg)
    if nearby:
      current = [item for item in nearby if item[0] <= ON_EVENT_RADIUS_METERS]
      selected = current or nearby
      location = "目前" if current else "前方"
      distance, event = selected[0]

      road_event.isActive = True
      road_event.description = f"{location}:" + "/".join(
        f"{event.event_type}|{event.description}" for _, event in selected[:4]
      )
      road_event.distance = distance
      road_event.latitude = event.latitude
      road_event.longitude = event.longitude
      road_event.eventType = event.event_type
      road_event.updateTime = event.update_time

      if event.section_id in client.traffic:
        traffic = client.traffic[event.section_id]
        traffic_status.isValid = True
        traffic_status.roadName = event.road_name
        traffic_status.distance = distance
        traffic_status.speed = traffic.speed
        traffic_status.travelTime = traffic.travel_time
        traffic_status.updateTime = traffic.update_time

  pm.send("tdx", message)


def main() -> None:
  pm = messaging.PubMaster(["tdx"])
  sm = messaging.SubMaster(["gpsLocationExternal"], ignore_alive=["gpsLocationExternal"])
  client = TdxClient()
  rk = Ratekeeper(1, print_delay_threshold=None)

  while True:
    sm.update(0)
    client.update()

    gps = sm["gpsLocationExternal"] if _valid_gps(sm["gpsLocationExternal"]) else None
    _publish(pm, client, gps)
    rk.keep_time()


if __name__ == "__main__":
  main()
