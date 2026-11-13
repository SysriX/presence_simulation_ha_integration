"""Konstanten der Anwesenheitssimulation (presence_sim)."""

DOMAIN = "presence_sim"
VERSION = "1.2.0"

CONF_ROOMS = "rooms"
DEFAULT_ROOMS = ["wz", "bu", "ku", "sz", "fl", "ke", "ba", "bz"]
ROOM_NAMES = {"wz": "Wohnzimmer", "bu": "Büro", "ku": "Küche", "sz": "Schlafzimmer", "fl": "Flur",
              "ke": "Keller", "ba": "Balkon", "bz": "Badezimmer"}

STORE_KEY = "presence_sim"
STORE_VERSION = 1

TICK_SECONDS = [0, 20, 40]     # Prüftakt; zusätzlich sofort bei Änderung von Anwesenheit / Alarmanlage
PLAN_HOUR = 12                 # ein Simulationstag läuft von 12:00 bis 12:00 (Abend, Nacht, Morgen am Stück)
OFF_GRACE_S = 30               # so lange nach dem Einschalten zählt "aus" noch nicht als "jemand hat ausgeschaltet"
MAX_PROFILES = 20
MAX_SLOTS = 8
MAX_LOG = 60

SIGNAL_UPDATE = "presence_sim_update"
EVENT_SWITCHED = "presence_sim_switched"

# Hausweite Quellen – dieselben Helfer wie Bewegungs-, Zeit- und Helligkeitssteuerung
SRC_ALARM = "input_text.haus_light_motion_cfg_alarm_panel"
SRC_PRESENCE = "input_text.haus_light_motion_cfg_presence_sensor"
MOTION_LIGHTS = ("primary", "secondary")

# Entitäten
ENT_ACTIVE = "binary_sensor.haus_sys_presence_sim_active"
ENT_STATUS = "sensor.haus_sys_presence_sim"
ENT_MODE = "select.haus_sys_presence_sim_mode"

GLOBAL_MODES = ["off", "auto", "manual"]          # Aus · Automatisch (nach Profil-Bedingungen) · Manuell (läuft jetzt)
SIM_MODES = ["auto", "fixed", "random"]            # Automatisch (gelernt → fest → Zufall) · Fest · Zufall
PRESENCE = ["ignore", "away"]
ALARM = ["ignore", "armed_away", "armed_any", "armed_vacation"]
LOGIC = ["and", "or"]
START_MODES = ["sun", "time"]
ROLES = ["anchor", "visitor"]                      # Hauptraum (lange an) · Besuchsraum (kurz zwischendurch)
LIGHT_MODES = ["color_temp", "color", "auto", "brightness", "on"]
END_ACTIONS = ["off", "keep"]

DEFAULT_SETTINGS = {
    "mode": "off",
    "manual_profile": "",          # Profil für "Manuell" (leer = erstes aktives)
    "manual_end_on_return": True,  # Manuell: endet, sobald jemand heimkommt
    "lock_motion": True,           # während der Simulation schaltet die Bewegungssteuerung nicht ein
    "lock_schedule": True,         # … die Zeitsteuerung nicht
    "lock_lux": True,              # … die Helligkeitssteuerung nicht
    "end_action": "off",           # Ende: eigene Lampen aus | anlassen
    "sound_enabled": False,        # Ton (Phase 5) – global aus
    "speakers": [],                # media_player.* (z. B. Z906)
}

DEFAULT_ROOM = {
    "room": "",
    "enabled": True,
    "lights": [],                  # leer = Lampen des Bewegungslichts "primär" des Raums
    "role": "visitor",
    "weight": 3,                   # 1–5: wie oft der Raum besucht wird
    "min_on": 5,                   # Minuten je Besuch
    "max_on": 20,
    "slots": [],                   # feste Zeiten (Betriebsart Fest / Automatisch)
}

DEFAULT_PROFILE = {
    "name": "Neues Profil",
    "enabled": True,
    "sim_mode": "auto",
    # Aktivierung (nur Modus Automatisch)
    "presence": "away",
    "away_min": 60,
    "alarm": "ignore",
    "logic": "and",
    "days": [0, 1, 2, 3, 4, 5, 6],
    "date_enabled": False,
    "date_from": "",               # JJJJ-MM-TT
    "date_to": "",
    # Räume
    "rooms": [],
    "max_parallel": 1,             # Besuchsräume gleichzeitig an (zusätzlich zu den Haupträumen)
    "gap_min": 5,                  # Pause zwischen zwei Besuchen (min)
    "gap_max": 30,
    "jitter": 15,                  # Zufallsversatz ± min (Start, feste Zeiten, Zu-Bett)
    # Abend
    "evening_start": "sun",        # sun = Sonnenuntergang + Versatz | time = feste Uhrzeit
    "evening_offset": 0,           # min relativ zum Sonnenuntergang (−120 … +120)
    "evening_time": "18:00",
    # Zu Bett
    "bed_enabled": True,
    "bed_from": "22:30",
    "bed_to": "23:30",
    "bed_route": ["fl", "bz", "sz"],
    "bed_step_min": 2,
    "bed_step_max": 8,
    "bed_last_min": 10,            # letzter Raum (z. B. Schlafzimmer) länger
    "bed_last_max": 25,
    # Nacht
    "night_enabled": True,
    "night_chance": 40,            # % Wahrscheinlichkeit pro Nacht
    "night_from": "01:00",
    "night_to": "04:30",
    "night_route": ["fl", "bz"],
    "night_step_min": 2,
    "night_step_max": 5,
    "night_brightness": 30,
    # Morgen
    "morning_enabled": True,
    "morning_days": [0, 1, 2, 3, 4],
    "morning_from": "06:15",
    "morning_to": "07:15",
    "morning_route": ["bz", "ku"],
    "morning_step_min": 5,
    "morning_step_max": 20,
    "morning_dark_only": True,     # nur solange die Sonne noch nicht aufgegangen ist
    # Licht
    "mode": "color_temp",
    "brightness": 80,
    "kelvin": 2900,
    "color": "#FFB060",
    "transition": 2.0,
    # Bausteine späterer Phasen (schon gespeichert, noch ohne Wirkung)
    "tv_enabled": False,
    "tv_light": "",
    "ring_enabled": False,
    "sound_enabled": False,
}
