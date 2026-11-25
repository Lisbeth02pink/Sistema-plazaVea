# Datos de referencia para predicción de productos comunes

`scanner-groceries.csv` es `dataRSM`, un conjunto scanner de productos de arroz, azúcar y leche de supermercados en Polonia, recopilado por Statistics Poland. Contiene 8,090 observaciones entre diciembre de 2024 y enero de 2026, con 152 descripciones, cantidades y precios en zlotys (PLN). Las descripciones originales en polaco se traducen a nombres comunes en español para la interfaz; los registros no contienen Plaza Vea ni datos de aceite.

Fuente y detalle: https://zenodo.org/records/18342253
DOI: 10.5281/zenodo.18342253

Los importes se convierten a PEN con `RETAIL_PLN_PEN_RATE` (valor inicial `0.95`). Es una tasa de referencia configurable, no una serie histórica diaria. El archivo no se versiona en Git.
