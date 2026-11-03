"""Zielkatalog für „Heute Nacht“: Messier (110) + Caldwell (109) fest eingebaut, dazu ASTAPs deep_sky.csv (falls vorhanden).

Warum zwei Quellen: `objects.CATALOG` kennt nur ~60 Objekte (Profile, Aliasse). Für Empfehlungen braucht es eine
vollständige Liste mit Helligkeit. ASTAPs deep_sky.csv hat **keine** Helligkeit (nur Ort, Typ, Größe) – deshalb sind Messier und
Caldwell mit Helligkeit fest eingebaut (Koordinaten werden bei vorhandenem ASTAP von dort übernommen), und weitere ASTAP-Objekte
kommen nur dazu, wenn sie nach Name/Größe erkennbar auffällig sind (Näherung, im Ergebnis als „Helligkeit unbekannt“ markiert).

Doppelte Bezeichnungen (M31 = NGC 224 = C…) werden über `Target.keys` zusammengeführt; `keys_for(name)` liefert alle Schlüssel
eines beliebigen Namens, damit eigene Objekte zuverlässig ausgeschlossen werden.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import objects

# Kurzcodes der Objektart → objects.KIND_LABEL
_K = {"g": "galaxy", "e": "emission", "r": "reflection", "p": "planetary", "s": "snr", "k": "globular", "o": "open_cluster", "x": "other"}

# (Nummer, NGC/IC-Kennung, deutscher Name, RA in Stunden, Dekl. in Grad, Art, Helligkeit mag oder None, Größe in Bogenminuten)
MESSIER = [
    (1, "NGC1952", "Krebsnebel", 5.575, 22.01, "s", 8.4, 6), (2, "NGC7089", "", 21.558, -0.82, "k", 6.5, 16),
    (3, "NGC5272", "", 13.703, 28.38, "k", 6.2, 18), (4, "NGC6121", "", 16.393, -26.53, "k", 5.6, 36),
    (5, "NGC5904", "", 15.309, 2.08, "k", 5.6, 23), (6, "NGC6405", "Schmetterlingshaufen", 17.668, -32.25, "o", 4.2, 25),
    (7, "NGC6475", "Ptolemäushaufen", 17.898, -34.79, "o", 3.3, 80), (8, "NGC6523", "Lagunennebel", 18.063, -24.38, "e", None, 90),
    (9, "NGC6333", "", 17.320, -18.52, "k", 7.7, 12), (10, "NGC6254", "", 16.952, -4.10, "k", 6.6, 20),
    (11, "NGC6705", "Wildentenhaufen", 18.851, -6.27, "o", 6.3, 14), (12, "NGC6218", "", 16.787, -1.95, "k", 6.7, 16),
    (13, "NGC6205", "Herkuleshaufen", 16.695, 36.46, "k", 5.8, 20), (14, "NGC6402", "", 17.627, -3.25, "k", 7.6, 11),
    (15, "NGC7078", "", 21.500, 12.17, "k", 6.2, 18), (16, "NGC6611", "Adlernebel", 18.313, -13.78, "e", None, 35),
    (17, "NGC6618", "Omeganebel", 18.346, -16.17, "e", None, 46), (18, "NGC6613", "", 18.332, -17.13, "o", 6.9, 9),
    (19, "NGC6273", "", 17.044, -26.27, "k", 6.8, 17), (20, "NGC6514", "Trifidnebel", 18.043, -23.03, "e", None, 28),
    (21, "NGC6531", "", 18.077, -22.50, "o", 5.9, 13), (22, "NGC6656", "", 18.607, -23.90, "k", 5.1, 32),
    (23, "NGC6494", "", 17.947, -19.02, "o", 5.5, 27), (24, "", "Kleine Sagittarius-Sternwolke", 18.283, -18.52, "x", 4.6, 90),
    (25, "IC4725", "", 18.528, -19.12, "o", 4.6, 32), (26, "NGC6694", "", 18.753, -9.40, "o", 8.0, 15),
    (27, "NGC6853", "Hantelnebel", 19.994, 22.72, "p", 7.4, 8), (28, "NGC6626", "", 18.409, -24.87, "k", 6.8, 11),
    (29, "NGC6913", "", 20.397, 38.52, "o", 6.6, 7), (30, "NGC7099", "", 21.673, -23.18, "k", 7.2, 12),
    (31, "NGC224", "Andromedagalaxie", 0.712, 41.27, "g", 3.4, 178), (32, "NGC221", "", 0.712, 40.87, "g", 8.1, 8),
    (33, "NGC598", "Dreiecksgalaxie", 1.564, 30.66, "g", 5.7, 71), (34, "NGC1039", "", 2.702, 42.78, "o", 5.2, 35),
    (35, "NGC2168", "", 6.148, 24.33, "o", 5.3, 28), (36, "NGC1960", "", 5.602, 34.14, "o", 6.0, 12),
    (37, "NGC2099", "", 5.873, 32.55, "o", 5.6, 24), (38, "NGC1912", "", 5.478, 35.85, "o", 6.4, 21),
    (39, "NGC7092", "", 21.536, 48.43, "o", 4.6, 32), (40, "", "Winnecke 4", 12.373, 58.08, "x", 8.4, 1),
    (41, "NGC2287", "", 6.767, -20.75, "o", 4.5, 38), (42, "NGC1976", "Orionnebel", 5.588, -5.39, "e", None, 65),
    (43, "NGC1982", "", 5.592, -5.27, "e", None, 20), (44, "NGC2632", "Krippe", 8.667, 19.67, "o", 3.7, 95),
    (45, "", "Plejaden", 3.79, 24.12, "r", 1.6, 110), (46, "NGC2437", "", 7.696, -14.81, "o", 6.1, 27),
    (47, "NGC2422", "", 7.610, -14.48, "o", 4.4, 30), (48, "NGC2548", "", 8.230, -5.75, "o", 5.8, 54),
    (49, "NGC4472", "", 12.497, 8.00, "g", 8.4, 10), (50, "NGC2323", "", 7.054, -8.37, "o", 5.9, 16),
    (51, "NGC5194", "Whirlpoolgalaxie", 13.498, 47.20, "g", 8.4, 11), (52, "NGC7654", "", 23.405, 61.59, "o", 6.9, 13),
    (53, "NGC5024", "", 13.215, 18.17, "k", 7.6, 13), (54, "NGC6715", "", 18.917, -30.48, "k", 7.6, 12),
    (55, "NGC6809", "", 19.667, -30.96, "k", 6.3, 19), (56, "NGC6779", "", 19.277, 30.18, "k", 8.3, 9),
    (57, "NGC6720", "Ringnebel", 18.893, 33.03, "p", 8.8, 3.8), (58, "NGC4579", "", 12.628, 11.82, "g", 9.7, 6),
    (59, "NGC4621", "", 12.700, 11.65, "g", 9.6, 5), (60, "NGC4649", "", 12.728, 11.55, "g", 8.8, 7),
    (61, "NGC4303", "", 12.365, 4.47, "g", 9.7, 6), (62, "NGC6266", "", 17.021, -30.11, "k", 6.5, 15),
    (63, "NGC5055", "Sonnenblumengalaxie", 13.263, 42.03, "g", 8.6, 13), (64, "NGC4826", "Schwarzauggalaxie", 12.945, 21.68, "g", 8.5, 10),
    (65, "NGC3623", "", 11.315, 13.09, "g", 9.3, 10), (66, "NGC3627", "", 11.337, 12.99, "g", 8.9, 9),
    (67, "NGC2682", "", 8.857, 11.82, "o", 6.1, 30), (68, "NGC4590", "", 12.657, -26.74, "k", 7.8, 11),
    (69, "NGC6637", "", 18.523, -32.35, "k", 7.6, 10), (70, "NGC6681", "", 18.720, -32.30, "k", 7.9, 8),
    (71, "NGC6838", "", 19.896, 18.78, "k", 8.2, 7), (72, "NGC6981", "", 20.891, -12.54, "k", 9.2, 6),
    (73, "NGC6994", "", 20.983, -12.63, "x", 9.0, 3), (74, "NGC628", "", 1.611, 15.78, "g", 9.4, 10),
    (75, "NGC6864", "", 20.101, -21.92, "k", 8.5, 7), (76, "NGC650", "Kleiner Hantelnebel", 1.703, 51.58, "p", 10.1, 2.7),
    (77, "NGC1068", "", 2.711, -0.01, "g", 8.9, 7), (78, "NGC2068", "", 5.78, 0.08, "r", 8.3, 8),
    (79, "NGC1904", "", 5.402, -24.52, "k", 7.7, 9), (80, "NGC6093", "", 16.284, -22.98, "k", 7.3, 10),
    (81, "NGC3031", "Bodes Galaxie", 9.926, 69.07, "g", 6.9, 27), (82, "NGC3034", "Zigarrengalaxie", 9.931, 69.68, "g", 8.4, 11),
    (83, "NGC5236", "Südliche Feuerradgalaxie", 13.617, -29.87, "g", 7.5, 13), (84, "NGC4374", "", 12.418, 12.89, "g", 9.1, 6),
    (85, "NGC4382", "", 12.423, 18.19, "g", 9.1, 7), (86, "NGC4406", "", 12.436, 12.95, "g", 8.9, 9),
    (87, "NGC4486", "", 12.514, 12.39, "g", 8.6, 8), (88, "NGC4501", "", 12.533, 14.42, "g", 9.6, 7),
    (89, "NGC4552", "", 12.595, 12.56, "g", 9.8, 5), (90, "NGC4569", "", 12.614, 13.16, "g", 9.5, 10),
    (91, "NGC4548", "", 12.591, 14.50, "g", 10.2, 5), (92, "NGC6341", "", 17.285, 43.14, "k", 6.4, 14),
    (93, "NGC2447", "", 7.743, -23.86, "o", 6.2, 22), (94, "NGC4736", "", 12.848, 41.12, "g", 8.2, 11),
    (95, "NGC3351", "", 10.733, 11.70, "g", 9.7, 7), (96, "NGC3368", "", 10.780, 11.82, "g", 9.2, 8),
    (97, "NGC3587", "Eulennebel", 11.246, 55.02, "p", 9.9, 3.4), (98, "NGC4192", "", 12.230, 14.90, "g", 10.1, 10),
    (99, "NGC4254", "", 12.313, 14.42, "g", 9.9, 5), (100, "NGC4321", "", 12.382, 15.82, "g", 9.3, 7),
    (101, "NGC5457", "Feuerradgalaxie", 14.054, 54.35, "g", 7.9, 29), (102, "NGC5866", "Spindelgalaxie", 15.108, 55.76, "g", 9.9, 7),
    (103, "NGC581", "", 1.553, 60.66, "o", 7.4, 6), (104, "NGC4594", "Sombrerogalaxie", 12.667, -11.62, "g", 8.0, 9),
    (105, "NGC3379", "", 10.797, 12.58, "g", 9.3, 5), (106, "NGC4258", "", 12.316, 47.30, "g", 8.4, 18),
    (107, "NGC6171", "", 16.542, -13.05, "k", 7.9, 13), (108, "NGC3556", "", 11.192, 55.67, "g", 10.0, 8),
    (109, "NGC3992", "", 11.960, 53.37, "g", 9.8, 8), (110, "NGC205", "", 0.673, 41.69, "g", 8.5, 20),
]

# (Nummer, Kennung, deutscher Name, RA h, Dekl., Art, Helligkeit, Größe′) – C50 (NGC 2244) steckt in C49, C61 (NGC 4039) in C60.
CALDWELL = [
    (1, "NGC188", "", 0.73, 85.24, "o", 8.1, 17), (2, "NGC40", "Bow-Tie-Nebel", 0.222, 72.52, "p", 10.7, 1.0),
    (3, "NGC4236", "", 12.277, 69.47, "g", 10.1, 22), (4, "NGC7023", "Irisnebel", 21.027, 68.17, "r", 6.8, 18),
    (5, "IC342", "Versteckte Galaxie", 3.780, 68.10, "g", 9.1, 21), (6, "NGC6543", "Katzenaugennebel", 17.976, 66.63, "p", 8.1, 0.4),
    (7, "NGC2403", "", 7.615, 65.60, "g", 8.4, 18), (8, "NGC559", "", 1.490, 63.30, "o", 7.4, 7),
    (9, "SH2155", "Höhlennebel", 22.955, 62.62, "e", None, 50), (10, "NGC663", "", 1.768, 61.23, "o", 6.4, 16),
    (11, "NGC7635", "Blasennebel", 23.345, 61.20, "e", None, 15), (12, "NGC6946", "Feuerwerksgalaxie", 20.581, 60.15, "g", 8.9, 11),
    (13, "NGC457", "Eulenhaufen", 1.320, 58.28, "o", 6.4, 13), (14, "NGC869", "Doppelhaufen h und χ Persei", 2.322, 57.13, "o", 4.3, 60),
    (15, "NGC6826", "Blinkender Planetarischer", 19.747, 50.52, "p", 8.8, 0.5), (16, "NGC7243", "", 22.250, 49.88, "o", 6.4, 21),
    (17, "NGC147", "", 0.553, 48.51, "g", 9.5, 13), (18, "NGC185", "", 0.650, 48.34, "g", 9.2, 12),
    (19, "IC5146", "Kokonnebel", 21.894, 47.27, "e", None, 12), (20, "NGC7000", "Nordamerikanebel", 20.980, 44.33, "e", None, 120),
    (21, "NGC4449", "", 12.469, 44.09, "g", 9.4, 6), (22, "NGC7662", "Blauer Schneeball", 23.435, 42.54, "p", 8.6, 0.5),
    (23, "NGC891", "", 2.376, 42.35, "g", 9.9, 13), (24, "NGC1275", "", 3.331, 41.51, "g", 11.9, 2.2),
    (25, "NGC2419", "", 7.635, 38.88, "k", 10.4, 4.6), (26, "NGC4244", "", 12.294, 37.81, "g", 10.2, 16),
    (27, "NGC6888", "Crescentnebel", 20.200, 38.35, "e", None, 20), (28, "NGC752", "", 1.963, 37.79, "o", 5.7, 50),
    (29, "NGC5005", "", 13.178, 37.06, "g", 9.8, 6), (30, "NGC7331", "", 22.617, 34.42, "g", 9.5, 10),
    (31, "IC405", "Flammender Stern", 5.270, 34.27, "e", None, 37), (32, "NGC4631", "Walgalaxie", 12.700, 32.54, "g", 9.8, 15),
    (33, "NGC6992", "Cirrusnebel (Ost)", 20.935, 31.72, "s", None, 60), (34, "NGC6960", "Cirrusnebel (West)", 20.760, 30.72, "s", None, 70),
    (35, "NGC4889", "", 13.000, 27.98, "g", 11.4, 3), (36, "NGC4559", "", 12.936, 27.96, "g", 10.0, 11),
    (37, "NGC6885", "", 20.200, 26.48, "o", 5.7, 7), (38, "NGC4565", "Nadelgalaxie", 12.606, 25.99, "g", 9.6, 16),
    (39, "NGC2392", "Eskimonebel", 7.485, 20.91, "p", 9.9, 0.7), (40, "NGC3626", "", 11.334, 18.36, "g", 10.9, 3),
    (41, "MEL25", "Hyaden", 4.447, 15.87, "o", 0.5, 330), (42, "NGC7006", "", 21.027, 16.19, "k", 10.6, 3.6),
    (43, "NGC7814", "", 0.050, 16.15, "g", 10.5, 6), (44, "NGC7479", "", 23.076, 12.32, "g", 10.9, 4),
    (45, "NGC5248", "", 13.627, 8.89, "g", 10.3, 6), (46, "NGC2261", "Hubbles veränderlicher Nebel", 6.65, 8.73, "r", 9.0, 2),
    (47, "NGC6934", "", 20.572, 7.40, "k", 8.9, 7), (48, "NGC2775", "", 9.171, 7.04, "g", 10.3, 4),
    (49, "NGC2237", "Rosettennebel", 6.535, 4.95, "e", None, 80), (51, "IC1613", "", 1.075, 2.13, "g", 9.3, 16),
    (52, "NGC4697", "", 12.806, -5.80, "g", 9.2, 7), (53, "NGC3115", "Sextans-Spindel", 10.087, -7.72, "g", 8.9, 8),
    (54, "NGC2506", "", 8.001, -10.77, "o", 7.6, 12), (55, "NGC7009", "Saturnnebel", 21.070, -11.36, "p", 8.0, 0.5),
    (56, "NGC246", "Totenkopfnebel", 0.783, -11.87, "p", 10.9, 4), (57, "NGC6822", "Barnards Galaxie", 19.749, -14.80, "g", 9.3, 15),
    (58, "NGC2360", "", 7.297, -15.63, "o", 7.2, 13), (59, "NGC3242", "Geist des Jupiter", 10.418, -18.64, "p", 7.7, 0.4),
    (60, "NGC4038", "Antennengalaxien", 12.026, -18.87, "g", 10.3, 5), (62, "NGC247", "", 0.789, -20.76, "g", 9.7, 20),
    (63, "NGC7293", "Helixnebel", 22.494, -20.84, "p", 7.6, 16), (64, "NGC2362", "", 7.310, -24.95, "o", 4.1, 8),
    (65, "NGC253", "Sculptorgalaxie", 0.793, -25.29, "g", 7.1, 27), (66, "NGC5694", "", 14.673, -26.54, "k", 10.2, 4),
    (67, "NGC1097", "", 2.77, -30.27, "g", 9.3, 9), (68, "NGC6729", "", 19.02, -36.95, "r", None, 7),
    (69, "NGC6302", "Käfernebel", 17.22, -37.10, "p", 9.6, 1.5), (70, "NGC300", "", 0.915, -37.68, "g", 8.1, 22),
    (71, "NGC2477", "", 7.87, -38.54, "o", 5.8, 27), (72, "NGC55", "", 0.25, -39.20, "g", 7.9, 32),
    (73, "NGC1851", "", 5.24, -40.05, "k", 7.2, 11), (74, "NGC3132", "Achtfach-Nebel", 10.12, -40.44, "p", 8.2, 1.4),
    (75, "NGC6124", "", 16.42, -40.66, "o", 5.8, 29), (76, "NGC6231", "", 16.90, -41.83, "o", 2.6, 15),
    (77, "NGC5128", "Centaurus A", 13.425, -43.02, "g", 6.8, 26), (78, "NGC6541", "", 18.13, -43.71, "k", 6.3, 13),
    (79, "NGC3201", "", 10.29, -46.41, "k", 6.8, 18), (80, "NGC5139", "Omega Centauri", 13.447, -47.48, "k", 3.9, 36),
    (81, "NGC6352", "", 17.41, -48.42, "k", 7.8, 7), (82, "NGC6193", "", 16.67, -48.76, "o", 5.2, 15),
    (83, "NGC4945", "", 13.09, -49.47, "g", 9.3, 20), (84, "NGC5286", "", 13.77, -51.37, "k", 7.3, 9),
    (85, "IC2391", "", 8.67, -53.04, "o", 2.5, 50), (86, "NGC6397", "", 17.67, -53.67, "k", 5.7, 26),
    (87, "NGC1261", "", 3.2, -55.22, "k", 8.4, 7), (88, "NGC5823", "", 15.0, -55.6, "o", 7.9, 10),
    (89, "NGC6087", "", 16.3, -57.9, "o", 5.4, 12), (90, "NGC2867", "", 9.35, -58.31, "p", 9.7, 0.2),
    (91, "NGC3532", "", 11.1, -58.7, "o", 3.0, 55), (92, "NGC3372", "Carinanebel", 10.73, -59.87, "e", 1.0, 120),
    (93, "NGC6752", "", 19.18, -59.98, "k", 5.4, 20), (94, "NGC4755", "Juwelenkästchen", 12.9, -60.37, "o", 4.2, 10),
    (95, "NGC6025", "", 16.06, -60.5, "o", 5.1, 12), (96, "NGC2516", "", 7.97, -60.87, "o", 3.8, 30),
    (97, "NGC3766", "", 11.6, -61.6, "o", 5.3, 12), (98, "NGC4609", "", 12.7, -62.99, "o", 6.9, 5),
    (99, "", "Kohlensack", 12.87, -63.0, "x", None, 400), (100, "IC2944", "", 11.6, -63.0, "e", 4.5, 15),
    (101, "NGC6744", "", 19.16, -63.86, "g", 9.0, 20), (102, "IC2602", "", 10.72, -64.4, "o", 1.9, 100),
    (103, "NGC2070", "Tarantelnebel", 5.64, -69.1, "e", 8.0, 40), (104, "NGC362", "", 1.05, -70.85, "k", 6.4, 13),
    (105, "NGC4833", "", 12.99, -70.88, "k", 7.4, 14), (106, "NGC104", "47 Tucanae", 0.4, -72.08, "k", 4.0, 50),
    (107, "NGC6101", "", 16.43, -72.2, "k", 9.2, 11), (108, "NGC4372", "", 12.43, -72.66, "k", 7.8, 19),
    (109, "NGC3195", "", 10.16, -80.86, "p", 11.6, 0.6),
]
# Zweitnamen, die zu einem Katalogeintrag gehören (zusammenführen statt doppelt vorschlagen)
EXTRA_KEYS = {("C", 49): ("NGC2244", "C50", "ROSETTE"), ("C", 60): ("NGC4039", "C61"), ("C", 14): ("NGC884",),
              ("C", 33): ("NGC6995", "IC1340"), ("C", 20): ("IC5070",), ("C", 4): (), ("M", 45): ("PLEIADES", "PLEJADEN"),
              ("M", 42): ("ORION",), ("M", 31): ("ANDROMEDA",), ("C", 41): ("HYADES", "HYADEN")}

MAG_LIMITS = {"stellina": 11.5, "vespera": 10.5, "seestar": 10.5, "dwarf": 9.5, "evscope": 12.0}
MAG_DEFAULT = 11.0
MIN_SIZE = 2.0            # Bogenminuten: darunter sind Objekte auf Smart-Teleskopen nur Pünktchen
MIN_FRACTION = 0.03       # oder weniger als 3 % der kurzen Bildfeldseite
MAX_FRACTION = 0.9        # größer als 90 % der kurzen Seite passt mit Rand nicht ins Bild


def mag_limit(device: dict | None) -> float:
    return MAG_LIMITS.get((device or {}).get("id"), MAG_DEFAULT)


@dataclass(frozen=True)
class Target:
    id: str                # eindeutige Kennung, z. B. "M31", "C20", "NGC1234"
    display: str
    ra: float              # Grad
    dec: float
    kind: str
    size: float            # Bogenminuten (größte Ausdehnung)
    mag: float | None      # None = unbekannt (Nebel: Helligkeit nicht aussagekräftig; ASTAP: nicht im Katalog)
    source: str            # messier | caldwell | astap
    keys: frozenset = field(default_factory=frozenset)
    number: int | None = None


def norm(name: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", name.upper())


def _ident(code: str) -> str:
    m = re.match(r"^(NGC|IC|MEL|SH2)(\d+)$", code)
    if not m:
        return code
    return {"NGC": "NGC ", "IC": "IC ", "MEL": "Mel ", "SH2": "Sh2-"}[m.group(1)] + m.group(2)


def _display(prefix: str, n: int, ngc: str, name: str) -> str:
    head = f"M{n}" if prefix == "M" else f"C {n}" + (f" ({_ident(ngc)})" if ngc else "")
    return f"{head} {name}".strip()


_FIXED: list[Target] | None = None
_ASTAP: list[Target] | None = None
_KEYS: dict[str, Target] = {}


def fixed() -> list[Target]:
    """Messier + Caldwell. Koordinaten/Größe kommen aus ASTAP, wenn der Eintrag dort über NGC/IC/M steht (genauer als meine Tabelle)."""
    global _FIXED
    if _FIXED is not None:
        return _FIXED
    ext = {}
    for c in objects.extended_catalog():
        ext.setdefault(c.id, c)
    out = []
    for prefix, rows in (("M", MESSIER), ("C", CALDWELL)):
        for n, ngc, name, ra_h, dec, k, mag, size in rows:
            kind = _K[k]
            if kind == "other":
                continue
            ra = ra_h * 15.0
            e = ext.get(f"M{n}" if prefix == "M" else "") or ext.get(norm(ngc))
            if e and objects.ang_sep(ra, dec, e.ra, e.dec) < 0.5:         # nur übernehmen, wenn es dasselbe Objekt ist
                ra, dec = e.ra, e.dec
                if e.size > 0 and abs(e.size - size) / max(size, 1) < 3:
                    size = max(size, e.size) if prefix == "M" or kind in ("galaxy",) else size
            keys = {norm(f"{prefix}{n}")}
            if prefix == "C":
                keys.add(f"CALDWELL{n}")
            else:
                keys.add(f"MESSIER{n}")
            if ngc:
                keys.add(norm(ngc))
            keys.update(norm(x) for x in EXTRA_KEYS.get((prefix, n), ()))
            tid = f"{prefix}{n}"
            if name:
                keys.add(norm(name))
            out.append(Target(tid, _display(prefix, n, ngc, name), ra, dec, kind, float(size), mag,
                              "messier" if prefix == "M" else "caldwell", frozenset(keys), n))
    _FIXED = out
    for t in out:                                                           # Messier gewinnt bei gleicher NGC-Nummer (C/M gibt es nie doppelt)
        for k in t.keys:
            _KEYS.setdefault(k, t)
    return out


def astap_extras() -> list[Target]:
    """Auffällige weitere Objekte aus ASTAPs deep_sky.csv. Es gibt dort keine Helligkeit; Auswahl über Name und Größe:
    benannte Objekte, größere Galaxien (≥ 8′) und NGC/IC-Sternhaufen 10–45′. Was Messier/Caldwell schon abdeckt, fällt weg."""
    global _ASTAP
    if _ASTAP is not None:
        return _ASTAP
    base = fixed()
    taken = {k for t in base for k in t.keys}
    out = []
    for c in objects.extended_catalog():
        if c.kind == "generic" or c.size < MIN_SIZE or not re.match(r"^(NGC|IC|MEL|CR|TR|STOCK)\d", c.id):
            continue
        if c.id in taken or any(a in taken for a in c.aliases):
            continue
        if any(abs(t.dec - c.dec) < 1 and objects.ang_sep(c.ra, c.dec, t.ra, t.dec) < max(0.1, t.size / 120) for t in base):
            continue
        named = len(c.display.split()) > (2 if re.match(r"^(NGC|IC|M) \d", c.display) else 1)
        if named or (c.kind == "galaxy" and c.size >= 8) or (c.kind in ("open_cluster", "globular") and 10 <= c.size <= 45 and c.id[:2] in ("NG", "IC")):
            out.append(Target(c.id, c.display, c.ra, c.dec, c.kind, c.size, None, "astap", frozenset({c.id, *c.aliases})))
    _ASTAP = out
    return out


def catalog() -> list[Target]:
    return fixed() + astap_extras()


def signature() -> tuple:
    """Ändert sich, wenn die ASTAP-Datei dazukommt/wegfällt (Zwischenspeicher der Planung)."""
    return (len(fixed()), len(astap_extras()))


def reset() -> None:
    """Nach Änderung der ASTAP-Datei bzw. in Tests: Zwischenspeicher leeren."""
    global _FIXED, _ASTAP
    _FIXED = _ASTAP = None
    _KEYS.clear()
    objects._EXT = None
    objects._EXT_ALIAS.clear()


def keys_for(name: str | None) -> set[str]:
    """Alle Schlüssel eines Objektnamens (z. B. „NGC 224“ → {NGC224, M31, MESSIER31, …}) für den Abgleich eigener Objekte."""
    if not name:
        return set()
    fixed()
    found = {norm(name)}
    n = re.sub(r"^MESSIER", "M", norm(name))
    found.add(n)
    tok = re.match(r"\s*([A-Za-z]{1,8})\s*0*(\d+)", name)
    if tok:
        found.add((tok.group(1) + tok.group(2)).upper())
        found.add(re.sub(r"^MESSIER", "M", (tok.group(1) + tok.group(2)).upper()))
        found.add(re.sub(r"^CALDWELL", "C", (tok.group(1) + tok.group(2)).upper()))
    cat = objects.resolve_name(name)
    if cat:
        found |= {norm(cat.id), *(norm(a) for a in cat.aliases)}
    ext = objects.resolve_extended(name)
    if ext:
        found.add(norm(ext.id))
    out = set(found)
    for k in found:
        t = _KEYS.get(k)
        if t:
            out |= t.keys
    return out


def usable(t: Target, device: dict | None, field_fov: list | None) -> bool:
    """Passt das Ziel zu Gerät und Bildfeld (Helligkeit, Größe)?"""
    if t.mag is not None and t.mag > mag_limit(device):
        return False
    if t.size < MIN_SIZE:
        return False
    if field_fov:
        short = min(field_fov) * 60
        if t.size < MIN_FRACTION * short or t.size > MAX_FRACTION * short:
            return False
    elif t.size > 120:
        return False
    return True
