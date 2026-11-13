"""Anwesenheitssimulation für die ganze Wohnung.

Hausweite Einstellungen (Modus Aus / Automatisch / Manuell, Sperren, Ende-Verhalten, Ton) und beliebig
viele Profile. Ein Profil legt fest, wann es automatisch startet (Abwesenheit, Alarmanlage, Wochentage,
Zeitraum), welche Räume mitmachen und wie der Tagesplan aussieht (siehe planner.py).

Ablauf:
  - Jeden Tag um 12:00 (und bei jeder Änderung) entsteht der Plan für den Simulationstag 12:00–12:00
    für das Profil, das laufen würde. Er ist auch sichtbar, solange die Simulation nicht läuft.
  - Läuft die Simulation, werden die Einträge abgearbeitet: Beginn → Lampen des Raums ein
    (nur, was aus ist; source = simulation), Ende → wieder aus (nur, was noch uns gehört).
  - Endet die Simulation (jemand kommt heim, Alarmanlage unscharf, Modus aus), gehen die eigenen Lampen
    aus (oder bleiben an, Einstellung end_action). Offene Einträge bleiben im Plan stehen.

Sperre: Solange die Simulation läuft, zeigt binary_sensor.haus_sys_presence_sim_active die Attribute
lock_motion / lock_schedule / lock_lux. Bewegungs-, Zeit- und Helligkeitssteuerung schalten dann nicht ein.
"""

from __future__ import annotations

import logging
import random
import re
import uuid
from datetime import date, datetime, timedelta
from typing import Any

from homeassistant.const import EVENT_HOMEASSISTANT_STOP, EVENT_STATE_CHANGED, SUN_EVENT_SUNRISE, SUN_EVENT_SUNSET
from homeassistant.core import Context, Event, HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.storage import Store
from homeassistant.helpers.sun import get_astral_event_date
from homeassistant.util import dt as dt_util

from .const import (
    ALARM, DEFAULT_PROFILE, DEFAULT_ROOM, DEFAULT_SETTINGS, END_ACTIONS, EVENT_SWITCHED, GLOBAL_MODES,
    LIGHT_MODES, LOGIC, MAX_LOG, MAX_PROFILES, MAX_SLOTS, MOTION_LIGHTS, OFF_GRACE_S, PRESENCE, ROLES,
    ROOM_NAMES, SIGNAL_UPDATE, SIM_MODES, SRC_ALARM, SRC_PRESENCE, START_MODES, STORE_KEY, STORE_VERSION,
    TICK_SECONDS, VERSION,
)
from .planner import build_plan, day_bounds, sim_day

_LOGGER = logging.getLogger(__name__)

RE_HM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
RE_DATE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$")
RE_ENT = re.compile(r"^[a-z_]+\.[a-z0-9_]+$")
RE_COL = re.compile(r"^#[0-9A-Fa-f]{6}$")
COLOR_MODES = {"hs", "xy", "rgb", "rgbw", "rgbww"}
BAD = ("unknown", "unavailable", "", None)
KIND_NAMES = {"evening": "Abend", "fixed": "Feste Zeit", "bed": "Zu Bett", "night": "Nacht", "morning": "Morgen"}


def _iso(t: datetime | None) -> str | None:
    return t.replace(microsecond=0).isoformat() if t else None


def _dt(s: str | None) -> datetime | None:
    return dt_util.parse_datetime(s) if s else None


class SimManager:
    def __init__(self, hass: HomeAssistant, rooms: list[str]) -> None:
        self.hass = hass
        self.rooms = rooms
        self.store: Store = Store(hass, STORE_VERSION, STORE_KEY)
        self.settings: dict = dict(DEFAULT_SETTINGS)
        self.profiles: dict[str, dict] = {}
        # Laufzustand: active, profile, since, reason, away_since, manual_away, prev_mode
        self.run: dict = {"active": False, "profile": None, "since": None, "reason": "", "away_since": None,
                          "manual_away": False, "prev_mode": "auto"}
        self.plan: dict = {"day": None, "profile": None, "created": None, "entries": []}
        self.log: list[dict] = []
        self.why: dict[str, str] = {}            # Profil-ID → warum es gerade (nicht) startet
        self.root = Context()
        self._unsubs: list = []
        self._busy = False
        self._again = False

    # ================================================================ Lebenszyklus
    async def async_start(self) -> None:
        data = await self.store.async_load() or {}
        self.settings = self._clean_settings({**DEFAULT_SETTINGS, **(data.get("settings") or {})})
        for k, v in (data.get("profiles") or {}).items():
            try:
                self.profiles[k] = self._clean({**DEFAULT_PROFILE, **v}, k)
            except ValueError as err:
                _LOGGER.warning("Anwesenheitssimulation: Profil %s verworfen (%s)", k, err)
        self.run.update(data.get("run") or {})
        self.plan = data.get("plan") or self.plan
        self.log = (data.get("log") or [])[-MAX_LOG:]
        if not self.profiles:
            p = self._clean({**DEFAULT_PROFILE, "name": "Abwesend", "rooms": self._default_rooms()})
            self.profiles[p["id"]] = p
        self._unsubs.append(async_track_time_change(self.hass, self._tick, second=TICK_SECONDS))
        self._unsubs.append(self.hass.bus.async_listen(EVENT_STATE_CHANGED, self._on_state, event_filter=self._is_src))
        self.hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, self._async_stop)
        self.hass.async_create_task(self._process())

    def _default_rooms(self) -> list[dict]:
        roles = {"wz": ("anchor", 3, 30, 90), "ku": ("visitor", 4, 5, 20), "bu": ("visitor", 2, 10, 40),
                 "fl": ("visitor", 3, 1, 4), "bz": ("visitor", 3, 3, 12)}
        out = []
        for r in self.rooms:
            role, w, a, b = roles.get(r, ("visitor", 2, 5, 15))
            out.append({**DEFAULT_ROOM, "room": r, "enabled": r in roles, "role": role, "weight": w,
                        "min_on": a, "max_on": b})
        return out

    async def _async_stop(self, _ev: Event | None = None) -> None:
        for u in self._unsubs:
            u()
        self._unsubs.clear()
        await self.store.async_save(self._data())

    def _data(self) -> dict:
        return {"settings": self.settings, "profiles": self.profiles, "run": self.run, "plan": self.plan, "log": self.log}

    def _save(self) -> None:
        self.store.async_delay_save(self._data, 2)

    @callback
    def _is_src(self, data: Any) -> bool:
        return data.get("entity_id") in (self._txt(SRC_PRESENCE), self._txt(SRC_ALARM))

    async def _on_state(self, _ev: Event) -> None:
        await self._process()

    async def _tick(self, _now: datetime) -> None:
        await self._process()

    # ================================================================ Einstellungen / Profile
    def _clean_settings(self, s: dict) -> dict:
        speakers = [e for e in dict.fromkeys(s.get("speakers") or []) if isinstance(e, str) and e.startswith("media_player.")]
        return {
            "mode": s.get("mode") if s.get("mode") in GLOBAL_MODES else "off",
            "manual_profile": str(s.get("manual_profile") or ""),
            "manual_end_on_return": bool(s.get("manual_end_on_return", True)),
            "lock_motion": bool(s.get("lock_motion", True)),
            "lock_schedule": bool(s.get("lock_schedule", True)),
            "lock_lux": bool(s.get("lock_lux", True)),
            "end_action": s.get("end_action") if s.get("end_action") in END_ACTIONS else "off",
            "sound_enabled": bool(s.get("sound_enabled")),
            "speakers": speakers[:10],
        }

    def _clean(self, p: dict, pid: str | None = None) -> dict:
        if isinstance(p.get("transition"), (int, float)) and p["transition"] < 0:
            p = {**p, "transition": 2.0}
        def num(src: dict, k, lo, hi, d):
            try:
                return max(lo, min(hi, float(src.get(k, d))))
            except (TypeError, ValueError):
                return d

        def hm(k: str) -> str:
            v = str(p.get(k) or DEFAULT_PROFILE[k])
            if not RE_HM.match(v):
                raise ValueError(f"Ungültige Uhrzeit {k}: {v!r}")
            return v

        def days(k: str) -> list[int]:
            return sorted({int(d) for d in (p.get(k) or []) if str(d).isdigit() and 0 <= int(d) <= 6})

        def route(k: str) -> list[str]:
            return [r for r in (p.get(k) or []) if r in self.rooms][:6]

        def ents(v) -> list[str]:
            return [e for e in dict.fromkeys(v or []) if isinstance(e, str) and RE_ENT.match(e)][:30]

        rooms, seen = [], set()
        for r in p.get("rooms") or []:
            r = {**DEFAULT_ROOM, **(r or {})}
            if r["room"] not in self.rooms or r["room"] in seen:
                continue
            seen.add(r["room"])
            slots = []
            for s in (r.get("slots") or [])[:MAX_SLOTS]:
                a, b = str((s or {}).get("from") or ""), str((s or {}).get("to") or "")
                if not RE_HM.match(a) or not RE_HM.match(b):
                    raise ValueError(f"Ungültige feste Zeit in {r['room']}: {a!r}–{b!r}")
                slots.append({"from": a, "to": b})
            lo, hi = int(num(r, "min_on", 1, 240, 5)), int(num(r, "max_on", 1, 480, 20))
            rooms.append({"room": r["room"], "enabled": bool(r.get("enabled", True)), "lights": ents(r.get("lights")),
                          "role": r["role"] if r["role"] in ROLES else "visitor",
                          "weight": int(num(r, "weight", 1, 5, 3)), "min_on": min(lo, hi), "max_on": max(lo, hi),
                          "slots": slots})
        df, dto = str(p.get("date_from") or ""), str(p.get("date_to") or "")
        if p.get("date_enabled") and (not RE_DATE.match(df) or not RE_DATE.match(dto)):
            raise ValueError("Ungültiger Zeitraum (erwartet JJJJ-MM-TT)")

        def pair(a: str, b: str, lo: float, hi: float, da: float, db: float) -> tuple[float, float]:
            x, y = num(p, a, lo, hi, da), num(p, b, lo, hi, db)
            return min(x, y), max(x, y)

        gmin, gmax = pair("gap_min", "gap_max", 0, 240, 5, 30)
        bmin, bmax = pair("bed_step_min", "bed_step_max", 0.5, 60, 2, 8)
        lmin, lmax = pair("bed_last_min", "bed_last_max", 1, 120, 10, 25)
        nmin, nmax = pair("night_step_min", "night_step_max", 0.5, 30, 2, 5)
        mmin, mmax = pair("morning_step_min", "morning_step_max", 1, 90, 5, 20)
        return {
            "id": pid or p.get("id") or uuid.uuid4().hex[:10],
            "name": (str(p.get("name") or "").strip() or "Profil")[:40],
            "enabled": bool(p.get("enabled", True)),
            "priority": int(num(p, "priority", 1, 99, 10)),
            "sim_mode": p.get("sim_mode") if p.get("sim_mode") in SIM_MODES else "auto",
            "presence": p.get("presence") if p.get("presence") in PRESENCE else "away",
            "away_min": int(num(p, "away_min", 0, 1440, 60)),
            "alarm": p.get("alarm") if p.get("alarm") in ALARM else "ignore",
            "logic": p.get("logic") if p.get("logic") in LOGIC else "and",
            "days": days("days"),
            "date_enabled": bool(p.get("date_enabled")),
            "date_from": df, "date_to": dto,
            "rooms": rooms,
            "max_parallel": int(num(p, "max_parallel", 1, 3, 1)),
            "gap_min": gmin, "gap_max": gmax,
            "jitter": int(num(p, "jitter", 0, 60, 15)),
            "evening_start": p.get("evening_start") if p.get("evening_start") in START_MODES else "sun",
            "evening_offset": int(num(p, "evening_offset", -120, 120, 0)),
            "evening_time": hm("evening_time"),
            "bed_enabled": bool(p.get("bed_enabled", True)),
            "bed_from": hm("bed_from"), "bed_to": hm("bed_to"),
            "bed_route": route("bed_route"),
            "bed_step_min": bmin, "bed_step_max": bmax, "bed_last_min": lmin, "bed_last_max": lmax,
            "night_enabled": bool(p.get("night_enabled", True)),
            "night_chance": int(num(p, "night_chance", 0, 100, 40)),
            "night_from": hm("night_from"), "night_to": hm("night_to"),
            "night_route": route("night_route"),
            "night_step_min": nmin, "night_step_max": nmax,
            "night_brightness": int(num(p, "night_brightness", 1, 100, 30)),
            "morning_enabled": bool(p.get("morning_enabled", True)),
            "morning_days": days("morning_days"),
            "morning_from": hm("morning_from"), "morning_to": hm("morning_to"),
            "morning_route": route("morning_route"),
            "morning_step_min": mmin, "morning_step_max": mmax,
            "morning_dark_only": bool(p.get("morning_dark_only", True)),
            "mode": p.get("mode") if p.get("mode") in LIGHT_MODES else "color_temp",
            "brightness": int(num(p, "brightness", 1, 100, 80)),
            "kelvin": int(num(p, "kelvin", 2000, 6500, 2900)),
            "color": str(p.get("color")).upper() if RE_COL.match(str(p.get("color") or "")) else "#FFB060",
            "transition": num(p, "transition", 0, 60, 2.0),
            "tv_enabled": bool(p.get("tv_enabled")),
            "tv_light": str(p.get("tv_light") or "") if RE_ENT.match(str(p.get("tv_light") or "")) else "",
            "ring_enabled": bool(p.get("ring_enabled")),
            "sound_enabled": bool(p.get("sound_enabled")),
        }

    async def async_save_settings(self, patch: dict) -> dict:
        old_mode = self.settings["mode"]
        self.settings = self._clean_settings({**self.settings, **patch})
        if self.settings["mode"] == "manual" and old_mode != "manual":
            self.run["prev_mode"] = old_mode if old_mode != "manual" else "auto"
            self.run["manual_away"] = self._home()[0] is False
        self._save()
        await self._process()
        return self.settings

    async def async_set_mode(self, mode: str) -> None:
        await self.async_save_settings({"mode": mode})

    async def async_save(self, patch: dict) -> str:
        pid = patch.get("id")
        if pid and pid not in self.profiles:
            pid = None
        if not pid and len(self.profiles) >= MAX_PROFILES:
            raise ValueError(f"Höchstens {MAX_PROFILES} Profile")
        p = self._clean({**DEFAULT_PROFILE, **(self.profiles.get(pid) or {}), **patch}, pid)
        self.profiles[p["id"]] = p
        self._save()
        await self._process(replan=self.plan.get("profile") == p["id"])
        return p["id"]

    async def async_delete(self, pid: str) -> None:
        if pid not in self.profiles:
            raise ValueError("Profil nicht gefunden")
        if len(self.profiles) == 1:
            raise ValueError("Das letzte Profil kann nicht gelöscht werden")
        if self.run["active"] and self.run["profile"] == pid:
            await self._stop("Profil gelöscht")
        self.profiles.pop(pid)
        if self.settings["manual_profile"] == pid:
            self.settings["manual_profile"] = ""
        self._save()
        await self._process()

    async def async_preview(self, profile: dict) -> list[dict]:
        """Plan für ein (ungespeichertes) Profil ausrechnen, ohne etwas zu ändern."""
        p = self._clean({**DEFAULT_PROFILE, **profile}, profile.get("id") or "preview")
        return self._entries_view(self._make_plan(p, sim_day(dt_util.now())))

    async def async_replan(self) -> None:
        await self._process(replan=True, force=True)

    async def async_start_manual(self, pid: str | None) -> None:
        if pid:
            if pid not in self.profiles:
                raise ValueError("Profil nicht gefunden")
            self.settings["manual_profile"] = pid
        await self.async_save_settings({"mode": "manual"})

    async def async_stop_manual(self) -> None:
        if self.settings["mode"] == "manual":
            await self.async_save_settings({"mode": self.run.get("prev_mode") or "off"})

    # ================================================================ Hilfen
    def _state(self, eid: str | None):
        return self.hass.states.get(eid) if eid else None

    def _txt(self, eid: str) -> str:
        s = self._state(eid)
        return s.state.strip() if s and s.state not in BAD else ""

    def _home(self) -> tuple[bool | None, str]:
        """(jemand zuhause?, Quelle). None = keine gültige Quelle."""
        e = self._txt(SRC_PRESENCE)
        s = self._state(e)
        if not s or s.state in BAD:
            return None, e
        if e.startswith("zone."):
            return (s.state.isdigit() and int(s.state) > 0), e
        return s.state in ("on", "home"), e

    def _alarm(self) -> str | None:
        s = self._state(self._txt(SRC_ALARM))
        return s.state if s and s.state not in BAD else None

    def _sun(self, day: date) -> tuple[datetime | None, datetime | None]:
        try:
            return (get_astral_event_date(self.hass, SUN_EVENT_SUNSET, day),
                    get_astral_event_date(self.hass, SUN_EVENT_SUNRISE, day + timedelta(days=1)))
        except Exception:  # noqa: BLE001
            return None, None

    def expand(self, ents: list[str]) -> list[str]:
        out: list[str] = []

        def add(e: str, depth: int) -> None:
            if e.startswith("group.") and depth < 4:
                s = self._state(e)
                for x in (s.attributes.get("entity_id") or []) if s else []:
                    add(x, depth + 1)
            elif e.split(".")[0] in ("light", "switch", "input_boolean") and e not in out:
                out.append(e)
        for e in ents:
            add(e, 0)
        return out

    def room_lamps(self, p: dict | None, room: str) -> list[str]:
        """Lampen eines Raums im Profil; leer = Bewegungslicht primär des Raums."""
        r = next((x for x in (p or {}).get("rooms", []) if x["room"] == room), None)
        if r and r["lights"]:
            return self.expand(r["lights"])
        lamps = self.expand([f"group.{room}_light_motion_primary"])
        if not lamps:
            m = self._txt(f"input_text.{room}_light_motion_primary_cfg_members")
            lamps = self.expand([x.strip() for x in m.split(",") if x.strip()])
        return lamps

    def motion_slot(self, lamp: str) -> str | None:
        for r in self.rooms:
            for L in MOTION_LIGHTS:
                b = f"{r}_light_motion_{L}"
                g = self._state(f"group.{b}")
                if g and lamp in (g.attributes.get("entity_id") or []):
                    return b
        return None

    def _source(self, b: str) -> str:
        s = self._state(f"input_select.{b}_source")
        return s.state if s else ""

    # ================================================================ Überblendzeit
    @staticmethod
    def _fade(kind: str, p: dict | None) -> float:
        """Überblendzeit des Profils: 'on' = transition, 'off' = transition_off (sonst wie 'on')."""
        p = p or {}
        v = p.get("transition_off", p.get("transition")) if kind == "off" else p.get("transition")
        try:
            return max(0.0, float(v))
        except (TypeError, ValueError):
            return 1.0

    async def _turn_off(self, ents: list[str], p: dict | None = None) -> None:
        """Lampen ausblenden, alles andere normal ausschalten."""
        lights = [e for e in ents if e.startswith("light.")]
        other = [e for e in ents if not e.startswith("light.")]
        if lights:
            await self.hass.services.async_call("light", "turn_off", {"entity_id": lights, "transition": self._fade("off", p)}, context=self._ctx())
        if other:
            await self.hass.services.async_call("homeassistant", "turn_off", {"entity_id": other}, context=self._ctx())

    def _ctx(self) -> Context:
        return Context(parent_id=self.root.id)

    # ================================================================ Aktivierung
    def conditions(self, p: dict, now: datetime) -> tuple[bool, str]:
        if not p["enabled"]:
            return False, "inaktiv"
        if now.weekday() not in p["days"]:
            return False, "heute nicht"
        if p["date_enabled"]:
            d = now.date().isoformat()
            if not (p["date_from"] <= d <= p["date_to"]):
                return False, f"nur {p['date_from']} – {p['date_to']}"
        parts: list[tuple[bool, str]] = []
        if p["presence"] == "away":
            home, _e = self._home()
            if home is None:
                parts.append((False, "keine Anwesenheitsquelle"))
            elif home:
                parts.append((False, "jemand zuhause"))
            else:
                since = _dt(self.run.get("away_since")) or now
                left = p["away_min"] * 60 - (now - since).total_seconds()
                parts.append((left <= 0, "" if left <= 0 else f"abwesend · Start in {int(left // 60) + 1} min"))
        if p["alarm"] != "ignore":
            a = self._alarm()
            if a is None:
                ok = False
            elif p["alarm"] == "armed_away":
                ok = a == "armed_away"
            elif p["alarm"] == "armed_vacation":
                ok = a == "armed_vacation"
            else:
                ok = a.startswith("armed_") or a in ("pending", "triggered")
            parts.append((ok, "" if ok else ("keine Alarmanlage" if a is None else "Alarmanlage nicht passend scharf")))
        if not parts:
            return False, "nur manuell (keine Bedingung)"
        oks = [o for o, _w in parts]
        ok = all(oks) if p["logic"] == "and" else any(oks)
        return ok, "" if ok else next(w for o, w in parts if not o)

    def _ordered(self) -> list[dict]:
        return sorted(self.profiles.values(), key=lambda p: (p["priority"], p["name"].lower()))

    def _manual_profile(self) -> dict | None:
        p = self.profiles.get(self.settings["manual_profile"])
        return p or next((x for x in self._ordered() if x["enabled"]), None) or next(iter(self._ordered()), None)

    def _wanted(self, now: datetime) -> tuple[dict | None, str]:
        """(Profil, das laufen soll, Grund) – Profil None = Simulation aus."""
        self.why = {}
        mode = self.settings["mode"]
        if mode == "off":
            return None, "Modus Aus"
        if mode == "manual":
            p = self._manual_profile()
            return p, "manuell gestartet"
        cur = self.profiles.get(self.run.get("profile") or "") if self.run["active"] else None
        found = None
        for p in self._ordered():
            ok, why = self.conditions(p, now)
            self.why[p["id"]] = "Bedingungen erfüllt" if ok else why
            if ok and not found:
                found = p
        if cur and self.why.get(cur["id"]) == "Bedingungen erfüllt":
            return cur, "Bedingungen erfüllt"      # laufendes Profil bleibt, solange es passt
        return found, ("Bedingungen erfüllt" if found else "keine Bedingung erfüllt")

    def _preview_profile(self, now: datetime) -> dict | None:
        """Profil, für das der Plan gezeigt wird, solange die Simulation nicht läuft."""
        if self.settings["mode"] == "manual":
            return self._manual_profile()
        for p in self._ordered():
            if p["enabled"] and now.weekday() in p["days"] and (
                    not p["date_enabled"] or p["date_from"] <= now.date().isoformat() <= p["date_to"]):
                return p
        return next((p for p in self._ordered() if p["enabled"]), None)

    # ================================================================ Ablauf
    async def _process(self, replan: bool = False, force: bool = False) -> None:
        if self._busy:
            self._again = True
            return
        self._busy = True
        try:
            while True:
                self._again = False
                await self._step(replan, force)
                replan = force = False
                if not self._again:
                    break
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Anwesenheitssimulation: Fehler beim Prüfen")
        finally:
            self._busy = False
            self._save()
            async_dispatcher_send(self.hass, SIGNAL_UPDATE)

    async def _step(self, replan: bool, force: bool) -> None:
        now = dt_util.now()
        home, _e = self._home()
        if home is False:
            if not self.run.get("away_since"):
                s = self._state(self._txt(SRC_PRESENCE))
                self.run["away_since"] = _iso(s.last_changed if s else now)
        else:
            self.run["away_since"] = None

        # Manuell: endet bei Heimkehr, wenn beim Start niemand zuhause war
        if (self.settings["mode"] == "manual" and self.settings["manual_end_on_return"]
                and self.run.get("manual_away") and home):
            self.settings["mode"] = self.run.get("prev_mode") or "off"
            self._log(None, "Manuell beendet: jemand ist heimgekommen")

        want, reason = self._wanted(now)
        if self.run["active"] and (not want or want["id"] != self.run["profile"]):
            if self.settings["mode"] == "off":
                why = "Modus Aus"
            elif want:
                why = f"Wechsel zu {want['name']}"
            else:
                why = "jemand ist zuhause" if home else reason
            await self._stop(why)
        if want and not self.run["active"]:
            self.run.update(active=True, profile=want["id"], since=_iso(now), reason=reason)
            self._log(None, f"Simulation gestartet: {want['name']} ({reason})")
        if self.run["active"]:
            self.run["reason"] = reason

        # Plan: neuer Simulationstag, anderes Profil oder Änderung
        p = self.profiles.get(self.run["profile"]) if self.run["active"] else self._preview_profile(now)
        day = sim_day(now)
        if (force or self.plan.get("day") != day.isoformat() or self.plan.get("profile") != (p or {}).get("id")
                or (replan and p)):
            self._new_plan(p, day, now, keep_future=not (force or self.plan.get("day") != day.isoformat()
                                                        or self.plan.get("profile") != (p or {}).get("id")))

        if self.run["active"] and p:
            await self._execute(p, now)

    def _make_plan(self, p: dict, day: date) -> list[dict]:
        sunset, sunrise = self._sun(day)
        return build_plan(p, day, sunset, sunrise, random.Random())

    def _new_plan(self, p: dict | None, day: date, now: datetime, keep_future: bool) -> None:
        """Neuen Plan erzeugen. Laufende Einträge bleiben; bei bloßen Änderungen auch bereits Erledigtes."""
        old = self.plan.get("entries") or []
        running = [e for e in old if e["state"] == "on"]
        entries = self._make_plan(p, day) if p else []
        # Einträge, die schon vorbei sind, nicht nachträglich "verpasst" anzeigen: nur Zukunft + laufende
        fresh = [e for e in entries if (_dt(e["off"]) or now) > now]
        if keep_future:
            done = [e for e in old if e["state"] in ("done", "skipped", "cancelled")]
        else:
            done = []
        # laufende Einträge: bei Änderungen am selben Profil das Ende aus dem neuen Plan übernehmen
        # (kein passender neuer Eintrag → endet jetzt); dann neue Einträge, die mit ihnen überlappen, auslassen
        for r in running:
            if keep_future:
                cur = next((e for e in fresh if e["room"] == r["room"] and _dt(e["on"]) <= now < _dt(e["off"])), None)
                r["off"] = cur["off"] if cur else _iso(now)
            fresh = [e for e in fresh if not (e["room"] == r["room"] and _dt(e["on"]) < _dt(r["off"]))]
        self.plan = {"day": day.isoformat(), "profile": (p or {}).get("id"), "created": _iso(now),
                     "entries": sorted(done + running + fresh, key=lambda e: e["on"])}

    async def _execute(self, p: dict, now: datetime) -> None:
        for e in self.plan["entries"]:
            on, off = _dt(e["on"]), _dt(e["off"])
            if e["state"] == "on":
                # Lampen, die jemand ausgeschaltet hat, gehören uns nicht mehr
                at = _dt(e.get("at")) or now
                if (now - at).total_seconds() > OFF_GRACE_S:
                    e["lamps"] = [x for x in e["lamps"] if (s := self._state(x)) and s.state == "on"]
                if now >= off:
                    await self._switch_off(e, "Ende")
                    e["state"] = "done"
                elif not e["lamps"]:
                    e["state"], e["note"] = "done", "von Hand ausgeschaltet"
            elif e["state"] == "pending":
                if now >= off:
                    since = _dt(self.run.get("since"))
                    e["state"], e["note"] = "skipped", ("vor dem Start" if since and off <= since else "verpasst")
                elif now >= on:
                    lamps, why = await self._switch_on(p, e)
                    if lamps:
                        e.update(state="on", lamps=lamps, at=_iso(now), note="")
                    else:
                        e["state"], e["note"] = "skipped", why

    async def _switch_on(self, p: dict, e: dict) -> tuple[list[str], str]:
        lamps = self.room_lamps(p, e["room"])
        if not lamps:
            return [], "keine Lampen"
        owned = {x for o in self.plan["entries"] if o is not e and o["state"] == "on" for x in o["lamps"]}
        take, bases = [], set()
        for x in lamps:
            s = self._state(x)
            is_on = bool(s and s.state == "on")
            b = self.motion_slot(x)
            if b:
                src = self._source(b)
                if is_on and src != "simulation" and x not in owned:
                    continue                 # Bewegung, Hand, Zeit, Helligkeit hat das Licht → nicht anfassen
                bases.add(b)
            elif is_on and x not in owned:
                continue
            take.append(x)
        if not take:
            return [], "schon an"
        for b in bases:
            if self._source(b) != "simulation" and self._state(f"input_select.{b}_source"):
                await self.hass.services.async_call("input_select", "select_option",
                                                    {"entity_id": f"input_select.{b}_source", "option": "simulation"},
                                                    context=self._ctx())
        await self._turn_on(p, take, e.get("bri"))
        self._log(e, "ein", take)
        return take, ""

    async def _switch_off(self, e: dict, why: str) -> None:
        taken = {x for o in self.plan["entries"] if o is not e and o["state"] == "on" for x in o["lamps"]}
        off = []
        for x in e["lamps"]:
            if x in taken:
                continue
            b = self.motion_slot(x)
            if b and self._source(b) != "simulation":
                continue                     # jemand anderes hat das Licht übernommen
            s = self._state(x)
            if s and s.state == "on":
                off.append(x)
        if off:
            await self._turn_off(off, self.profiles.get(self.run.get("profile")))
            self._log(e, f"aus ({why})", off)

    async def _stop(self, reason: str) -> None:
        for e in self.plan.get("entries") or []:
            if e["state"] == "on":
                if self.settings["end_action"] == "off":
                    await self._switch_off(e, "Simulation beendet")
                e["state"], e["note"] = "cancelled", "Simulation beendet"
        name = (self.profiles.get(self.run.get("profile") or "") or {}).get("name", "")
        self._log(None, f"Simulation beendet: {name} ({reason})")
        self.run.update(active=False, profile=None, since=None, reason=reason)

    async def _turn_on(self, p: dict, lamps: list[str], bri: int | None) -> None:
        groups: dict[str, list[str]] = {}
        h = p["color"].lstrip("#")
        rgb = [int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)]
        mode = p["mode"]
        for x in lamps:
            if x.split(".")[0] != "light":
                groups.setdefault("other", []).append(x)
                continue
            s = self._state(x)
            modes = set((s.attributes.get("supported_color_modes") if s else None) or [])
            has_color, has_ct, has_bri = bool(modes & COLOR_MODES), "color_temp" in modes, bool(modes - {"onoff"})
            if mode == "on":
                k = "plain"
            elif mode in ("color", "auto") and has_color:
                k = "color"
            elif mode in ("color_temp", "auto") and has_ct:
                k = "ct"
            elif has_bri:
                k = "bri"
            else:
                k = "plain"
            groups.setdefault(k, []).append(x)
        for k, xs in groups.items():
            if k == "other":
                await self.hass.services.async_call("homeassistant", "turn_on", {"entity_id": xs}, context=self._ctx())
                continue
            data: dict[str, Any] = {"entity_id": xs}
            if k != "plain":
                data["brightness_pct"] = bri or p["brightness"]
                data["transition"] = self._fade("on", p)
            if k == "color":
                data["rgb_color"] = rgb
            elif k == "ct":
                data["color_temp_kelvin"] = p["kelvin"]
            await self.hass.services.async_call("light", "turn_on", data, context=self._ctx())

    def _log(self, e: dict | None, what: str, lamps: list[str] | None = None) -> None:
        item = {"at": _iso(dt_util.now()), "what": what}
        if e:
            item.update(room=e["room"], kind=e["kind"], lamps=lamps or [])
            self.hass.bus.async_fire(EVENT_SWITCHED, {"room": e["room"], "kind": e["kind"], "action": what,
                                                      "lights": lamps or []}, context=self._ctx())
        _LOGGER.info("Anwesenheitssimulation: %s %s %s", e["room"] if e else "", what, lamps or "")
        self.log = (self.log + [item])[-MAX_LOG:]

    # ================================================================ Anzeige
    def _entries_view(self, entries: list[dict]) -> list[dict]:
        return [{**e, "room_name": ROOM_NAMES.get(e["room"], e["room"].upper()),
                 "kind_name": KIND_NAMES.get(e["kind"], e["kind"])} for e in entries]

    @property
    def active(self) -> bool:
        return bool(self.run["active"])

    def locks(self) -> dict:
        a = self.active
        return {"lock_motion": a and self.settings["lock_motion"], "lock_schedule": a and self.settings["lock_schedule"],
                "lock_lux": a and self.settings["lock_lux"]}

    def status(self) -> str:
        if self.active:
            return "active"
        return "off" if self.settings["mode"] == "off" else "ready"

    def view(self) -> dict:
        now = dt_util.now()
        entries = self.plan.get("entries") or []
        nxt = next((e for e in entries if e["state"] == "pending" and _dt(e["on"]) > now), None)
        home, src = self._home()
        lo, hi = day_bounds(date.fromisoformat(self.plan["day"])) if self.plan.get("day") else (None, None)
        p = self.profiles.get(self.run.get("profile") or "") if self.active else None
        return {
            "version": VERSION,
            "mode": self.settings["mode"],
            "active": self.active,
            "profile_id": p["id"] if p else None,
            "profile_name": p["name"] if p else None,
            "since": self.run.get("since"),
            "reason": self.run.get("reason"),
            "home": home,
            "presence_source": src or None,
            "alarm": self._alarm(),
            "away_since": self.run.get("away_since"),
            "settings": self.settings,
            "profiles": [{**x, "why": self.why.get(x["id"])} for x in self._ordered()],
            "rooms": [{"room": r, "name": ROOM_NAMES.get(r, r.upper())} for r in self.rooms],
            "plan": {"day": self.plan.get("day"), "from": _iso(lo), "to": _iso(hi), "profile": self.plan.get("profile"),
                     "created": self.plan.get("created"), "entries": self._entries_view(entries)},
            "next": self._entries_view([nxt])[0] if nxt else None,
            "running_lamps": sorted({x for e in entries if e["state"] == "on" for x in e["lamps"]}),
            "log": list(reversed(self.log[-30:])),
            **self.locks(),
        }
