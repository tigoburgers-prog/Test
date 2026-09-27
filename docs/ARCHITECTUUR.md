# Architectuur en technische keuzes

Prioriteit bij elke keuze: betrouwbaarheid → bruikbaarheid → performance → uitbreidbaarheid → visuele verfijning.

## Technische stack

| Onderdeel | Keuze | Waarom |
|---|---|---|
| Taal | Python 3.11 | Beste ecosysteem voor computer vision; snel te onderhouden. Het zware rekenwerk zit in native bibliotheken (OpenCV, ONNX Runtime, NumPy) die de GIL vrijgeven. |
| UI | Qt 6 via PySide6 (LGPL) | Volwassen, native Windows-desktop-UI, uitstekende tekstopmaak en PDF-uitvoer, geen browser/Electron nodig. |
| Video | OpenCV (FFmpeg-backend) | Beeld-exacte posities; dezelfde decoder voor analyse en weergave, zodat overlays exact samenvallen. Qt Multimedia is bewust niet gebruikt (codec-afhankelijk, niet beeld-exact). |
| Detectie | YOLOX-S (Apache-2.0, COCO) via ONNX Runtime | Licentie staat vrije distributie toe (in tegenstelling tot AGPL-modellen); ONNX Runtime draait op CPU en via DirectML op elke DirectX 12-GPU (NVIDIA/AMD/Intel) zonder extra installatie. |
| Aanvullende detectie | Achtergrondsubtractie (MOG2) | Vindt bewegende objecten die het neurale model mist (bovenaanzicht/drone, bewegingsonscherpte, zeer kleine voertuigen). Werkt ook zonder model. |
| Tracking | Eigen ByteTrack-achtige tracker | Klein, volledig testbaar en *pickle*-baar (voorwaarde voor hervatten). Snelheden in px/s, dus robuust bij wisselende beeldstap. |
| Opslag | Projectmap met `project.json` + SQLite + NPZ | Leesbaar, zelfstandig, geen databaseserver. SQLite (WAL) voor ruwe waarnemingen en checkpoints, NPZ voor snelle resultaten. |
| Grafieken | Matplotlib (object-API) | Eén renderfunctie voor scherm, export en rapport → altijd identiek. |
| Rapport | QTextDocument → QPdfWriter | Professionele typografie, tabellen, paginanummers; geen extra afhankelijkheden. |
| Distributie | PyInstaller (map) + Inno Setup | Start zonder Python; installer zonder beheerdersrechten; modellen meegeleverd → volledig offline. |

## Lagen

```
traffic_snapshot/
  core/        domeinmodel, video-probe, projectopslag, foutmeldingen, paden, opmaak
  analysis/    strategy → detection → tracking → postprocess → results (+ pipeline, store)
  reporting/   charts (gedeeld), report (PDF), export (PNG/JPEG/CSV), vaste teksten
  ui/          pagina's, widgets (videospeler, tijdlijn, heatmap), workers (achtergrondthreads)
```

`core` en `analysis` kennen geen UI; alles is zonder scherm te testen (zie `tests/`).

## Analyseflow

1. **Voorbereiden** — video/ruimte controleren, `ProcessingPlan` bepalen.
2. **Frames / detectie / tracking** — in één lus, per *venster* (~3 s video) vastgelegd.
3. **Beweging** — relatieve beweging per waarneming (zie hieronder).
4. **Ruimtelijk** — achtergrondbeeld (mediaan), rasters worden op aanvraag berekend.
5. **Resultaten** — richtingen, gebeurtenissen, kerncijfers.
6. **Opslaan** — eerst naar `results.tmp/`, daarna atomisch omwisselen.

### Hoe de instellingen worden gebruikt

* *Verwachte snelheid* bepaalt de beeldstap: een voertuig mag tussen twee geanalyseerde beelden
  hooguit ~40 % van zijn lengte verplaatsen (`dt = 0,4 × 4 m / v`). De beeldbreedte valt hierin weg.
  Nooit wordt hieruit een snelheid berekend.
* *Beeldbreedte* bepaalt de verwachte voertuiggrootte in pixels, en daarmee of het beeld in
  tegels wordt geanalyseerd (kleine voertuigen) of dat bewegingsdetectie nodig is.

### Automatisch nauwkeuriger

Per venster meet de tracker hoe ver objecten springen (t.o.v. hun grootte) en hoeveel sporen
midden in beeld opnieuw beginnen (fragmentatie). Is dat te veel, dan wordt de tracker-toestand
van het vensterbegin hersteld en het venster opnieuw verwerkt met een halve beeldstap.
Is het volgen ruim stabiel, dan wordt de stap voorzichtig vergroot (maximaal 2× de basisstap
en nooit minder dan 2 beelden per seconde). Alle aanpassingen worden gelogd in de resultaten.

### Relatieve beweging

`verplaatsing / eigen grootte in beeld` (compenseert perspectief) gedeeld door de mediaan van alle
bewegende waarnemingen in díe video. 1,0 = typisch voor deze opname. Stilstand, lage beweging,
wachtrij-indicatie (≥ 3 dicht opeenstaande (bijna) stilstaande voertuigen) en stagnatie
(meerderheid staat stil) zijn hierop gebaseerd. Voertuigen die de hele waarneming stilstaan
(bijv. geparkeerd) worden apart gehouden.

### Categorieën

Het COCO-model onderscheidt bestelwagens en vrachtwagens niet betrouwbaar; die worden daarom
als *Bestel-/vrachtwagen* gerapporteerd in plaats van een verzonnen splitsing. Een fietser of
motorrijder telt als één verkeersdeelnemer (de persoon op het voertuig wordt onderdrukt).
Objecten die alleen via bewegingsdetectie gevonden zijn heten *Voertuig (type onbekend)*.
Richtingen zijn neutraal (*Richting A/B* + beeldrichting), nooit geografisch.

## Robuustheid

* **Checkpoints**: waarnemingen en tracker-/samplertoestand worden per venster in één
  SQLite-transactie vastgelegd. Na stoppen, crash of stroomuitval gaat hooguit één venster verloren.
* **Hervatten** kan alleen als video, instellingen en methodeversie ongewijzigd zijn; anders
  begint de analyse automatisch opnieuw. Het achtergrondmodel van de bewegingsdetectie wordt
  bij hervatten opnieuw opgebouwd uit voorgaande beelden.
* **Beschadigde video**: onleesbare stukken worden overgeslagen en vermeld; alleen als er
  niets meer te lezen valt stopt de analyse (hervatbaar, voortgang bewaard).
* **Atomisch schrijven** van `project.json` en resultaten.
* Een project dat bij het starten nog op "Analyse bezig" staat, wordt als *onderbroken* gemarkeerd.
* Foutmeldingen: begrijpelijke tekst + tip; technische details inklapbaar en in het logbestand
  (`%LOCALAPPDATA%\TrafficSnapshot\logs`).

## Versiebeheer van analyses

Per analyse worden bewaard (in `project.json` en `results/summary.json`): softwareversie,
analysemethodeversie (`ANALYSIS_METHOD_VERSION`), instellingen, detectiemodel + SHA-256,
tracker-parameters, verwerkingsplan en alle automatische aanpassingen, rekeneenheid
(CPU/GPU), start/eindtijd, aantal hervattingen en opmerkingen. Deze informatie staat ook
in het methodologie-hoofdstuk van elk rapport.

## Performance

* Analyse in een achtergrondthread; ONNX Runtime gebruikt alle kernen op één na.
* GPU via DirectML (Windows) of CUDA, met automatische terugval op de CPU.
* Slim beeldsampling (zie boven); `grab()` voor overgeslagen beelden i.p.v. volledig decoderen.
* Dashboard: resultaten één keer in het geheugen geladen; alle selecties zijn gevectoriseerde
  NumPy-bewerkingen; tabbladen worden pas getekend als ze zichtbaar worden.

## Testen

`pytest` bevat unit-tests (tracker, strategie, relatieve beweging, richtingen, opslag, video-probe),
end-to-end-analyses op een synthetische verkeersvideo met wachtrij (inclusief onderbreken en
hervatten), rapport/export-tests en UI-tests (offscreen) die de volledige gebruikersflow doorlopen.

## Uitbreidingen (voorbereid)

* Een ander detectiemodel: een `ModelSpec` toevoegen in `analysis/detection.py`.
* Nieuwe analyses/kaarten: een `heatmap`-soort of methode op `Selection` toevoegen; UI,
  export en rapport gebruiken dezelfde functies uit `reporting/charts.py`.
