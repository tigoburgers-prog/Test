# Detectiemodellen

De modelbestanden (`*.onnx`) worden niet in git opgeslagen. Haal ze op met:

```
python scripts/fetch_models.py
```

Het script downloadt de YOLOX-modellen (Apache-2.0, getraind op COCO) van de
officiële YOLOX-release en controleert de SHA-256-hash. Bij het bouwen van de
Windows-versie worden ze in de installer meegeleverd, zodat de applicatie
daarna volledig offline werkt.

Zonder modellen werkt de applicatie ook, maar dan uitsluitend met
bewegingsdetectie (zonder herkenning van het voertuigtype).
