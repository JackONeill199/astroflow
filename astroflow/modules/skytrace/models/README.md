# Erkennungsmodell

SkyTrace verwendet eine eigene lokale Kopie von `night-detector/night-yolo26s-1920.pt` und benötigt keinen OpenSkyCam-Projektordner.

Das vortrainierte Nachtmodell stammt aus [OpenSkyCam](https://github.com/Deibler/openskycam/tree/main/models/night-detector). Modellkarte, Metadaten und AGPL-3.0-Lizenz liegen unverändert unter `night-detector/`. Seine Modellvorschläge bleiben unbestätigte Kandidaten; SkyTrace ergänzt die Prüfung von Sternfeldbewegung, Bildqualität und Spurform.

Bei einer neuen Installation muss der Ordner `night-detector/` einschließlich Gewichten, `README.md`, `model.json` und `LICENSE` mitgeliefert werden. `Einrichten.cmd` installiert die Python-Abhängigkeiten aus `requirements-lock.txt`; es lädt kein Ersatzmodell herunter.
