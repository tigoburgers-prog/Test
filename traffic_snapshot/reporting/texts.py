"""Fixed texts: disclaimer, methodology and terminology.

Kept in one place so the application, the methodology screen and the
reports always say exactly the same thing.
"""

DISCLAIMER = (
    "Deze analyse is gebaseerd op relatieve verkeersbewegingen binnen één tijdelijke opname.\n"
    "Er worden geen absolute snelheden gemeten of gerapporteerd.\n"
    "Alle verwerking is volledig lokaal uitgevoerd."
)

DISCLAIMER_ONE_LINE = DISCLAIMER.replace("\n", " ")

METHODOLOGY_SECTIONS: list[tuple[str, str]] = [
    (
        "Momentopname",
        "Een project bevat één videobestand. De analyse beschrijft het verkeer uitsluitend binnen deze "
        "tijdelijke opname. De uitkomsten zijn een momentopname en zeggen niets over andere dagen, "
        "tijdstippen of weersomstandigheden.",
    ),
    (
        "Automatische detectie",
        "Verkeersdeelnemers worden automatisch in de beelden herkend. Hiervoor wordt een lokaal "
        "detectiemodel (YOLOX, getraind op de openbare COCO-dataset) gebruikt dat personenauto's, "
        "bestel-/vrachtwagens, bussen, motoren, fietsen en voetgangers onderscheidt. Bestelwagens en "
        "vrachtwagens worden door dit model niet betrouwbaar van elkaar onderscheiden en worden daarom "
        "samen gerapporteerd. Daarnaast worden bewegende objecten die het model mist via "
        "bewegingsdetectie gevonden; hiervan wordt het type niet bepaald ('type onbekend').",
    ),
    (
        "Volgen over beelden",
        "Een herkend voertuig wordt van beeld tot beeld gevolgd, zodat het zoveel mogelijk als één "
        "voertuig wordt geteld in plaats van telkens opnieuw. De applicatie bepaalt zelf hoeveel "
        "beelden nodig zijn. De opgegeven verwachte voertuigsnelheid en beeldbreedte worden alleen "
        "gebruikt om die keuze te optimaliseren. Blijkt het volgen onvoldoende stabiel, dan wordt "
        "dat deel van de video automatisch opnieuw en nauwkeuriger (met meer beelden) verwerkt.",
    ),
    (
        "Relatieve beweging",
        "Beweging wordt uitgedrukt ten opzichte van de eigen afmeting van een voertuig in beeld "
        "(dit compenseert grotendeels voor perspectief) en vervolgens ten opzichte van de typische "
        "beweging in deze opname. Een waarde van 1,0 betekent 'gebruikelijk voor deze opname', "
        "0,5 half zo veel beweging en 2,0 twee keer zo veel. De kleuren volgen een warm-koud patroon: "
        "warm is relatief meer beweging, koud relatief minder. Er wordt nergens een snelheid in km/h "
        "berekend en de waarden zijn niet vergelijkbaar tussen verschillende video's.",
    ),
    (
        "Stilstand, stagnatie en wachtrij-indicatie",
        "Een voertuig geldt als stilstaand wanneer het binnen ongeveer een seconde nauwelijks van "
        "plaats verandert. 'Lage beweging' betekent minder dan 40% van de typische beweging. "
        "Stagnatie is een periode waarin het merendeel van de aanwezige voertuigen stilstaat. "
        "Een wachtrij-indicatie ontstaat wanneer drie of meer (bijna) stilstaande voertuigen dicht "
        "bij elkaar staan. Voertuigen die gedurende de gehele waarneming stilstaan (bijvoorbeeld "
        "geparkeerde voertuigen) worden apart gehouden en niet als stagnatie meegeteld.",
    ),
    (
        "Richtingen",
        "Verkeersstromen worden automatisch gegroepeerd op basis van de bewegingsrichting in beeld en "
        "neutraal benoemd (Richting A, Richting B, …). Geografische richtingen zoals noord of zuid "
        "zijn niet uit de video af te leiden en worden daarom niet gebruikt.",
    ),
    (
        "Beperkingen",
        "De resultaten zijn afhankelijk van beeldkwaliteit, camerastandpunt, belichting, weer en "
        "zichtbaarheid. Voertuigen die elkaar afdekken, zeer klein in beeld zijn of het beeld maar "
        "kort passeren kunnen worden gemist of dubbel geteld. De analyse gaat uit van een stilstaande "
        "camera. Automatische detectie is niet foutloos; gebruik de resultaten als indicatie en "
        "controleer opvallende uitkomsten in de video.",
    ),
    (
        "Lokale verwerking",
        "Alle verwerking vindt plaats op deze computer. Er worden geen beelden of gegevens naar "
        "internet of externe diensten verstuurd, en er is geen account nodig.",
    ),
]

STATE_LABELS = {0: "Beweging", 1: "Lage beweging", 2: "Stilstand"}
