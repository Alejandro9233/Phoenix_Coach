# Plan de nutrición de Alex: paquete para integrar en la app

Versión 2026-10-21. Este paquete contiene el plan de nutrición completo como datos, listo para que la app lo muestre y lo use. Tú ya conoces la app: aquí solo va qué datos hay, cómo leerlos y qué reglas aplicar. Cómo modelarlo y mostrarlo es decisión tuya.

## Qué es el plan

- **Usuario:** hombre de 22 años, 170 cm y 74.3 kg, que prepara su primer maratón (Monterrey, 13 dic 2026, meta sub-3:20).
- **Objetivo:** perder grasa con un déficit ligero de ~300 kcal al día en promedio, sin comprometer el entrenamiento.
- **Estructura:** cada día del calendario usa uno de **4 tipos de día**: A, B, C o D. Cada tipo tiene un menú fijo con horarios, alimentos y cantidades en gramos o mililitros.
- **Productos:** son los que el usuario compra, con valores tomados de sus etiquetas. Lo que no tiene etiqueta sale del USDA.

| Tipo | Cuándo | kcal | Proteína | Carbohidrato | Déficit vs gasto |
|---|---|---|---|---|---|
| A | Descanso o fuerza ligera | 1,868 | 154 g | 198 g | −157 (−297 con gimnasio) |
| B | Carrera suave | 2,198 | 160 g | 245 g | −252 |
| C | Tirada larga (domingo) | 3,674 | 186 g | 559 g | +349 (geles) |
| D | Sesión de calidad (tempo o ritmo de maratón) | 2,794 | 162 g | 380 g | +94 |

Gasto desde el 29 oct 2026: mantenimiento de 2,025 kcal sin entreno (Katch-McArdle con 61 kg de masa magra × 1.2, sin caminar) más cada sesión a ~1 kcal por kg por km. El día A no baja de 1,830 kcal (disponibilidad de energía de 30 kcal por kg de masa magra).

## Archivos

| Archivo | Contenido |
|---|---|
| `alimentos.json` | Catálogo de 48 alimentos y suplementos. 34 se usan en el plan (`en_plan: true`); el resto son alternativas ya calculadas. |
| `tipos_de_dia.json` | Los 4 tipos de día, con cada comida, sus ingredientes y cantidades, y los totales por comida y por día, incluidos los micronutrientes. |
| `calendario.json` | Del 19 oct al 13 dic 2026, con el tipo de día de cada fecha, la fase, los días sin déficit y los eventos (medio maratón, tirada de 27 km, carga y maratón). |
| `metas_y_reglas.json` | Metas de macros y 23 micronutrientes con su límite superior, suplementos, reglas de ajuste, meal prep y pendientes. |
| `base_productos.xlsx` | Base original de etiquetas, con ingredientes, alérgenos y notas de evaluación por producto. Es para consulta; los JSON ya contienen lo necesario. |

## Convenciones (importante)

- **Unidades:** cada alimento declara `valores_por`: `"100 g"`, `"100 ml"` o `"1 unidad"` (suplementos y geles). Para calcular un ítem:
  - Si es por 100: `valor × cantidad / 100`.
  - Si es por unidad: `valor × cantidad`.
- **Crudo o cocido:** carne molida y pollo se pesan **crudos**; arroz y pasta, **ya cocidos**; la Maruchan es la sopa **preparada** (585 g). Está en `nota` de cada alimento y conviene mostrárselo al usuario.
- **Carbohidrato:** siempre es **disponible**, sin fibra, como en la etiqueta mexicana (NOM-051). La fibra va aparte.
- **Lote del 28 oct 2026:** chía OKKO, avena Bob's Red Mill (alta en proteína), bagel New York Deli, pan Oroweat y tocino Obertal entraron al menú con los gramos ajustados al mismo carbohidrato; arándanos OKKO en la merienda de A; Groovies como alternativa a Lucky Charms; camarón congelado solo en el catálogo. La avena Granvita usa la fila `avena_generica` (su etiqueta solo da valores preparada).
- **Alternativas:** un ítem puede traer `alternativa` (`food_id` + `cantidad`): se puede cambiar por ese alimento sin recalcular el día. Hoy: amaranto en lugar de avena, mismos gramos.
- **Piezas:** `pieza` da equivalencias para mostrar (1 tostada = 12 g, 1 scoop = 31 g, 1 segundo de spray = 0.67 g). La cantidad del plan siempre está en gramos o mililitros.
- **Fuentes:** `fuente_macros` y `fuente_micros` valen `label` (etiqueta del usuario), `usda`, `label+usda` o `estimate`. Si la app muestra datos, conviene distinguir los estimados.
- **Claves de micronutrientes:** el sufijo indica la unidad (`vitA_ug`, `calcio_mg`, `ala_g`). `epa_dha_g` en las metas es la suma de `epa_g` + `dha_g`.

## Cómo calcular un día

1. Busca el `tipo_dia` de la fecha en `calendario.json`.
2. Toma ese tipo en `tipos_de_dia.json`. Ya trae `totales` y `micros_totales` precalculados.
3. Si permites cambiar cantidades o alimentos, recalcula con `alimentos.json`. Verifiqué que el recálculo coincide exacto con los totales exportados.

El tipo `PENDIENTE` (11, 12 y 13 dic) todavía no tiene menú. Mientras tanto, muestra la nota del día y usa un día C sin recortar.

## Reglas que la app debería respetar

Están en `metas_y_reglas.json → reglas`. Las que se pueden automatizar:

- **Pesaje semanal (lunes en ayunas):**
  - Si no baja en 3 semanas: arroz de los días A a 80 g.
  - Si baja más de 0.5 kg por semana dos semanas seguidas: regresar plátano 120 g al desayuno de los días B.
- **Freno por recuperación:** si la HRV o la frecuencia cardíaca durmiendo empeoran dos semanas seguidas, una semana casi a mantenimiento: +plátano 120 g en el desayuno y +miel 42 g en la noche. El usuario usa reloj COROS; si la app ya recibe esos datos, este es el disparador.
- **Lo que no se recorta nunca:** el pre-entreno, la comida de recuperación y los geles.
- **Días `sin_deficit: true`:** se comen completos.
- **Víspera de tirada larga de 2 h o más:** menú D aunque el plan diga descanso o gimnasio (la app lo aplica sola desde el plan).
- **Geles de la tirada larga (desde el 29 oct 2026):** la app pone tantos geles de 40 g como pida la tasa de `geles_tirada` (80 g/h hasta el medio maratón, 90 g/h desde el 9 nov) por la duración de la tirada del plan, y mueve los totales del día con ellos. Sin tirada en el plan, el menú C trae 4. Los totales de C en este paquete son con 4 geles.
- **Límites que no conviene romper al editar:**
  - Calcio del día C (está en 2,393 de 2,500 mg).
  - Magnesio de suplementos: máximo 350 mg al día. Hoy son 204 mg del glicinato, 115 mg por scoop de Isopure y 12.3 mg por tableta de Multilyte; por eso hay **máximo 1 scoop de Isopure al día**.
  - Vitamina C: el usuario toma ½ comprimido de 2,000 mg.

## Preferencias del usuario (no proponer lo contrario)

- **Huevo:** solo en el bagel de los días A.
- **Sin espinaca ni almendras.**
- **Kéfir:** se toma solo en vaso, sin mezclar.
- **Proteína en polvo:** en shake con agua.
- **Overnight oats:** remojados en leche, no en agua.
- **Carne:** prefiere la molida sobre el pollo.
- **Ramen:** es Maruchan mezclada con el paquete completo de atún con camarón (200 g), sin nada más.
- **Cocina:** quiere cocinar lo menos posible, en 2 sesiones por semana (ver `meal_prep`).
- **Café:** 4 shots de espresso al día, todos antes de las 5 PM, con 100 ml de leche por cada 2 shots.

## Pendientes conocidos

Están en `metas_y_reglas.json → pendientes`:
- Menús de carga, tirada de 27 km y días de carrera.
- Confirmar los días de calidad (hoy se suponen martes y jueves).
- Proporción de atún y camarón.
- Masa magra medida y análisis de sangre.

Quedan bajo meta, aceptados por ahora:
- **Colina:** días B, C y D.
- **Vitamina E:** días B y D.
- **Vitamina D:** día A.

## Fuente de verdad

**Desde el 28 oct 2026 la fuente de verdad es este paquete** (decidido por Alex). La ficha del usuario en Claude Docs (pestañas "Menús a detalle" y "Plan v2: con tus productos") se actualiza desde aquí, no al revés.

Para cambiar el menú: edita cantidades o alimentos en `alimentos.json` / `tipos_de_dia.json` y recalcula los totales con `python3 scripts/nutrition_recompute.py backend/data/nutrition` (usa las reglas de redondeo del paquete; verificado contra el día C). Nunca edites totales a mano. Después, lleva los números nuevos a la ficha.

## En la app (Phoenix)

- `backend/services/nutrition.py` lee estos archivos; `GET /nutrition/today` sirve el menú del día.
- El tipo de día sale del plan semanal guardado, no de `calendario.json`. El calendario solo manda en sus días fijos (evento, `sin_deficit`, `PENDIENTE`) y cuando no hay plan.
- **Antes de dormir es a las 11:59 PM** (era 1:00 AM; cambiado el 2026-10-21 para que ninguna comida cruce la medianoche). Si una comida vuelve a quedar después de medianoche, la app rechaza el paquete (503).
- Actualizar = editar los JSON, recalcular, probar (`backend/tests/test_nutrition.py`) y desplegar.
