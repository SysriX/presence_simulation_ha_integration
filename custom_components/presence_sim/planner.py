"""Tagesplan der Anwesenheitssimulation.

Ein Simulationstag läuft von 12:00 bis 12:00 am nächsten Tag, damit Abend, Zu-Bett, Nacht und Morgen
ein zusammenhängender Plan sind. Uhrzeiten vor 12:00 gehören deshalb immer zum Folgetag.

Bausteine:
  Abend    – Haupträume (role=anchor) sind vom Abendstart bis zum Zu-Bett-Gehen an, Besuchsräume
             (role=visitor) werden zwischendurch kurz besucht (gewichtet, nie zweimal hintereinander).
             Räume mit festen Zeiten nutzen diese statt der Zufallsbesuche (Betriebsart Fest / Automatisch).
  Zu Bett  – Haupträume aus, dann die Route (z. B. Flur → Bad → Schlafzimmer) nacheinander, leicht überlappend.
  Nacht    – mit einer Wahrscheinlichkeit einmal kurz Flur → Bad, gedimmt.
  Morgen   – an gewählten Wochentagen eine kurze Route, auf Wunsch nur solange es noch dunkel ist.

Betriebsart je Raum:
  random – Zufall (Haupt-/Besuchsraum)
  fixed  – feste Zeitfenster des Raums (± Streuung); Räume ohne Zeiten machen beim Abend nicht mit
  auto   – gelernt (folgt in Phase 3) → feste Zeiten, falls vorhanden → Zufall
"""

from __future__ import annotations

import random
from datetime import date, datetime, timedelta

from homeassistant.util import dt as dt_util

from .const import PLAN_HOUR


def sim_at(day: date, hm: str) -> datetime:
    """Uhrzeit im Simulationstag: vor 12:00 = Folgetag."""
    h, m = (int(x) for x in hm.split(":"))
    d = day + timedelta(days=1) if h < PLAN_HOUR else day
    return dt_util.as_local(datetime(d.year, d.month, d.day, h, m, tzinfo=dt_util.DEFAULT_TIME_ZONE))


def day_bounds(day: date) -> tuple[datetime, datetime]:
    start = dt_util.as_local(datetime(day.year, day.month, day.day, PLAN_HOUR, 0, tzinfo=dt_util.DEFAULT_TIME_ZONE))
    return start, start + timedelta(days=1)


def sim_day(now: datetime) -> date:
    """Simulationstag, zu dem ein Zeitpunkt gehört."""
    return (now - timedelta(hours=PLAN_HOUR)).date()


def _between(rng: random.Random, a: datetime, b: datetime) -> datetime:
    if b <= a:
        return a
    return a + timedelta(seconds=rng.uniform(0, (b - a).total_seconds()))


def _minutes(rng: random.Random, lo: float, hi: float) -> timedelta:
    lo, hi = min(lo, hi), max(lo, hi)
    return timedelta(minutes=rng.uniform(lo, hi))


def room_source(p: dict, r: dict) -> str | None:
    """Woher der Abend dieses Raums kommt: fixed | random | None (macht nicht mit)."""
    if p["sim_mode"] == "random":
        return "random"
    if r["slots"]:
        return "fixed"
    return "random" if p["sim_mode"] == "auto" else None


def build_plan(p: dict, day: date, sunset: datetime | None, sunrise: datetime | None,
               rng: random.Random | None = None) -> list[dict]:
    rng = rng or random.Random()
    jit = timedelta(minutes=p["jitter"])
    entries: list[dict] = []

    def add(room: str, kind: str, src: str, on: datetime, off: datetime, bri: int | None = None) -> None:
        if off - on >= timedelta(seconds=30):
            entries.append({"room": room, "kind": kind, "src": src, "on": on, "off": off, "bri": bri})

    def jitter(t: datetime, scale: float = 1.0) -> datetime:
        return t + timedelta(seconds=rng.uniform(-1, 1) * jit.total_seconds() * scale)

    rooms = [r for r in p["rooms"] if r["enabled"]]

    # ---------------------------------------------------------------- Abend
    if p["evening_start"] == "sun" and sunset:
        start = dt_util.as_local(sunset) + timedelta(minutes=p["evening_offset"])
    else:
        start = sim_at(day, p["evening_time"])
    start = jitter(start)
    b_from, b_to = sim_at(day, p["bed_from"]), sim_at(day, p["bed_to"])
    if b_to < b_from:
        b_from, b_to = b_to, b_from
    bed = _between(rng, b_from, b_to)
    if start > bed - timedelta(minutes=30):          # Sommer: Sonne geht spät unter → kurzer Abend
        start = bed - timedelta(minutes=30)

    visitors = []
    for r in rooms:
        src = room_source(p, r)
        if src == "fixed":
            for s in r["slots"]:
                on, off = sim_at(day, s["from"]), sim_at(day, s["to"])
                if off <= on:
                    off += timedelta(days=1)
                on, off = jitter(on, 0.5), jitter(off, 0.5)
                add(r["room"], "fixed", "fixed", on, max(off, on + timedelta(minutes=1)))
        elif src == "random":
            if r["role"] == "anchor":
                add(r["room"], "evening", "random", start + _minutes(rng, 0, 8), bed + _minutes(rng, 0, 2))
            else:
                visitors.append(r)

    if visitors:
        last_end = bed - timedelta(minutes=5)
        for _chain in range(max(1, p["max_parallel"])):
            t, prev = start + _minutes(rng, 0, p["gap_max"]), None
            while t < last_end:
                pool = [r for r in visitors if r["room"] != prev] or visitors
                r = rng.choices(pool, weights=[r["weight"] for r in pool])[0]
                dur = _minutes(rng, r["min_on"], r["max_on"])
                add(r["room"], "evening", "random", t, min(t + dur, last_end))
                prev = r["room"]
                t = t + dur + _minutes(rng, p["gap_min"], p["gap_max"])

    # ---------------------------------------------------------------- Zu Bett
    def route(names: list[str], kind: str, t: datetime, lo: float, hi: float,
              last: tuple[float, float] | None = None, bri: int | None = None,
              until: datetime | None = None) -> None:
        for i, room in enumerate(names):
            if until and t >= until:
                return
            a, b = last if (last and i == len(names) - 1) else (lo, hi)
            dur = _minutes(rng, a, b)
            add(room, kind, "random", t, t + dur, bri)
            t = t + dur - timedelta(seconds=rng.uniform(5, 30))

    if p["bed_enabled"] and p["bed_route"]:
        route(p["bed_route"], "bed", bed, p["bed_step_min"], p["bed_step_max"],
              (p["bed_last_min"], p["bed_last_max"]))

    # ---------------------------------------------------------------- Nacht
    if p["night_enabled"] and p["night_route"] and rng.random() * 100 < p["night_chance"]:
        n_from, n_to = sim_at(day, p["night_from"]), sim_at(day, p["night_to"])
        route(p["night_route"], "night", _between(rng, n_from, n_to),
              p["night_step_min"], p["night_step_max"], bri=p["night_brightness"])

    # ---------------------------------------------------------------- Morgen
    next_day = day + timedelta(days=1)
    if p["morning_enabled"] and p["morning_route"] and next_day.weekday() in p["morning_days"]:
        m_from, m_to = sim_at(day, p["morning_from"]), sim_at(day, p["morning_to"])
        until = dt_util.as_local(sunrise) if (p["morning_dark_only"] and sunrise) else None
        route(p["morning_route"], "morning", _between(rng, m_from, m_to),
              p["morning_step_min"], p["morning_step_max"], until=until)

    return _finish(entries, day)


def _finish(entries: list[dict], day: date) -> list[dict]:
    """Je Raum überlappende Einträge zusammenfassen, auf den Simulationstag begrenzen, sortieren."""
    lo, hi = day_bounds(day)
    by_room: dict[str, list[dict]] = {}
    for e in entries:
        e["on"], e["off"] = max(e["on"], lo), min(e["off"], hi)
        if e["off"] - e["on"] >= timedelta(seconds=30):
            by_room.setdefault(e["room"], []).append(e)
    out: list[dict] = []
    for room, es in by_room.items():
        es.sort(key=lambda e: e["on"])
        cur = es[0]
        for e in es[1:]:
            if e["on"] <= cur["off"] + timedelta(seconds=60):
                cur["off"] = max(cur["off"], e["off"])
                if cur["bri"] is not None and e["bri"] is None:
                    cur["bri"] = None           # gedimmt nur, wenn alles daran gedimmt ist
            else:
                out.append(cur)
                cur = e
        out.append(cur)
    out.sort(key=lambda e: (e["on"], e["room"]))
    res = []
    for i, e in enumerate(out):
        res.append({"id": f"{day:%m%d}-{i:02d}-{e['room']}", "room": e["room"], "kind": e["kind"], "src": e["src"],
                    "on": e["on"].replace(microsecond=0).isoformat(), "off": e["off"].replace(microsecond=0).isoformat(),
                    "bri": e["bri"], "state": "pending", "lamps": [], "note": ""})
    return res
