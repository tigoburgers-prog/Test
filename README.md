# Traffic Snapshot

Windows-desktopapplicatie voor **tijdelijke verkeersanalyse op basis van videobeelden**.
Een gebruiker importeert één verkeersvideo, de applicatie analyseert die volledig lokaal
(zonder cloud, account of internet) en toont de resultaten in een interactief dashboard.

> Deze analyse is gebaseerd op relatieve verkeersbewegingen binnen één tijdelijke opname.
> Er worden geen absolute snelheden gemeten of gerapporteerd.
> Alle verwerking is volledig lokaal uitgevoerd.

## Wat kan de applicatie?

| Onderdeel | Inhoud |
|---|---|
| Projecthub | Overzicht van projecten (naam, datum, videoduur, resolutie, status), zoeken, sorteren, openen, verwijderen, projectmap openen |
| Nieuw project | Video kiezen of slepen (MP4; ook MOV/M4V/AVI/MKV), controle op geldigheid, metadata, voorbeeldbeeld, projectnaam, video optioneel naar projectmap kopiëren |
| Instellingen | Verwachte voertuigsnelheid en geschatte beeldbreedte — alleen voor technische optimalisatie; uitleg in gewone taal van hoe er wordt geanalyseerd en hoe lang dat ongeveer duurt |
| Analyse | 8 zichtbare stappen, percentage, verwerkte video, resterende tijd; stoppen, hervatten en opnieuw beginnen; de interface blijft bruikbaar |
| Dashboard | Videospeler met 6 schakelbare lagen (voertuigen, tracking, categorieën, relatieve beweging, stilstand/stagnatie, drukte), kerncijfers, tijdlijn met klikbare gebeurtenissen, selectie van periode en zone, heatmaps, grafieken |
| Zones | Klik op een gebied in de heatmap voor een detailweergave, of teken en bewaar eigen zones |
| Analysedetails | Relatieve beweging, voertuigtelling, stilstand en drukte met grafieken en sorteerbare tabellen (dubbelklik → naar dat moment in de video) |
| Rapportage | PDF met kiesbare onderdelen: overzicht, kerncijfers, heatmaps, voertuiggegevens, bewegingsanalyse, stilstand/stagnatie, methodologie, disclaimer |
| Export | Heatmaps, grafieken, videobeeld met lagen en dashboard als PNG/JPEG (met context-onderschrift) en gegevens als CSV voor Excel |

## Gebruik (eindgebruiker)

Installeer `TrafficSnapshot-Setup-1.0.0.exe` (geen beheerdersrechten nodig) en start
**Traffic Snapshot** vanuit het Startmenu. Projecten worden opgeslagen in
`Documenten\Traffic Snapshot\Projecten`; elk project is een zelfstandige map.

## Ontwikkelen

Vereist Python 3.10+ (3.11 aanbevolen).

```bash
python -m venv .venv
.venv\Scripts\activate           # Linux/macOS: source .venv/bin/activate
pip install -r requirements-dev.txt
python scripts/fetch_models.py   # detectiemodellen (eenmalig, met SHA-256-controle)
python -m traffic_snapshot       # applicatie starten
pytest                           # tests (headless: QT_QPA_PLATFORM=offscreen)
ruff check traffic_snapshot tests
```

`TRAFFIC_SNAPSHOT_HOME` verplaatst de gegevensmap (handig voor tests of een draagbare installatie).

## Windows-distributie bouwen

```powershell
.\packaging\build_windows.ps1
```

Dit draait de tests, bouwt met PyInstaller een zelfstandige map `dist\TrafficSnapshot\`
(met Python, Qt, OpenCV, ONNX Runtime en de modellen erin) en maakt — als Inno Setup 6
aanwezig is — de installer `dist\TrafficSnapshot-Setup-<versie>.exe`.
De GitHub Actions-workflow `.github/workflows/build-windows.yml` doet hetzelfde op
`windows-latest` en levert de installer als build-artefact.

## Documentatie

* [docs/ARCHITECTUUR.md](docs/ARCHITECTUUR.md) — architectuur, technische keuzes en waarom
* De methodologie staat in de applicatie (Help → Methodologie) en in elk rapport.
