# Plan de nutrición de Alex: paquete para integrar en la app

Versión 2026-10-21. Este paquete contiene el plan de nutrición completo como datos, listo para que la app lo muestre y lo use. Tú ya conoces la app: aquí solo va qué datos hay, cómo leerlos y qué reglas aplicar. Cómo modelarlo y mostrarlo es decisión tuya.

## Qué es el plan

- **Usuario:** hombre de 22 años, 170 cm y 74.3 kg, que prepara su primer maratón (Monterrey, 13 dic 2026, meta sub-3:20).
- **Objetivo:** perder grasa con un déficit ligero de ~300 kcal al día en promedio, sin comprometer el entrenamiento.
- **Estructura:** cada día del calendario usa uno de **4 tipos de día**: A, B, C o D. Cada tipo tiene un menú fijo con horarios, alimentos y cantidades en gramos o mililitros.
- **Productos:** son los que el usuario compra, con valores tomados de sus etiquetas. Lo que no tiene etiqueta sale del USDA.

| Tipo | Cuándo | kcal | Proteína | Carbohidrato | Déficit vs gasto |
|---|---|---|---|---|---|
| A | Descanso o fuerza ligera | 1,665 | 159 g | 155 g | −360 (−500 con gimnasio) |
| B | Carrera suave | 2,021 | 157 g | 199 g | −429 |
| C | Tirada larga (domingo) | 3,605 | 174 g | 560 g | +280 (geles) |
| D | Sesión de calidad (tempo o ritmo de maratón) | 2,745 | 157 g | 376 g | +45 |

Gasto desde el 29 oct 2026: mantenimiento de 2,025 kcal sin entreno (Katch-McArdle con 61 kg de masa magra × 1.2, sin caminar) más cada sesión a ~1 kcal por kg por km. El día A tenía un piso de 1,830 kcal (disponibilidad de energía de 30 kcal por kg de masa magra); el 14 nov 2026 Alex lo bajó a propósito para el déficit y el menú queda en 1,692. Un cambio de alimento puede subir el día A, pero no bajarlo más de 3 %.

## Archivos

| Archivo | Contenido |
|---|---|
| `alimentos.json` | Catálogo de 53 alimentos y suplementos. 33 se usan en el plan (`en_plan: true`); el resto son alternativas ya calculadas o productos de la alacena que todavía no están en ningún menú. |
| `tipos_de_dia.json` | Los 4 tipos de día, con cada comida, sus ingredientes y cantidades, y los totales por comida y por día, incluidos los micronutrientes. |
| `calendario.json` | Del 19 oct al 13 dic 2026, con el tipo de día de cada fecha, la fase, los días sin déficit y los eventos (medio maratón, tirada de 27 km, carga y maratón). |
| `metas_y_reglas.json` | Metas de macros y 23 micronutrientes con su límite superior, suplementos, reglas de ajuste, meal prep y pendientes. |
| `base_productos.xlsx` | Base original de etiquetas, con ingredientes, alérgenos y notas de evaluación por producto. Es para consulta; los JSON ya contienen lo necesario. |

## Convenciones (importante)

- **Unidades:** cada alimento declara `valores_por`: `"100 g"`, `"100 ml"` o `"1 unidad"` (suplementos y geles). Para calcular un ítem:
  - Si es por 100: `valor × cantidad / 100`.
  - Si es por unidad: `valor × cantidad`.
- **Crudo o cocido:** la carne molida se pesa **cruda**; el pollo, el arroz y la pasta, **ya cocidos** (pollo cocido desde el 14 nov 2026: fila `pollo_cocido`, la etiqueta en crudo entre 0.75, un rendimiento estimado; `meal_prep` sigue en gramos crudos, que es lo que se compra); la Maruchan es la sopa **preparada** (585 g). Está en `nota` de cada alimento y conviene mostrárselo al usuario.
- **Carbohidrato:** siempre es **disponible**, sin fibra, como en la etiqueta mexicana (NOM-051). La fibra va aparte.
- **Lote del 28 oct 2026:** chía OKKO, avena Bob's Red Mill (alta en proteína), bagel New York Deli, pan Oroweat y tocino Obertal entraron al menú con los gramos ajustados al mismo carbohidrato; arándanos OKKO en la merienda de A; Groovies como alternativa a Lucky Charms; camarón congelado solo en el catálogo. La avena Granvita usa la fila `avena_generica` (su etiqueta solo da valores preparada).
- **Bagel (14 nov 2026):** el del desayuno de A es ahora el Bimbo, 1 pieza de 103 g pesada por Alex (la etiqueta dice 91.5 g; era New York Deli, 90 g). Sube el día A 41 kcal, 4 g de proteína, 10.9 g de carbohidrato y 255 mg de sodio, y el déficit de A queda en 116 kcal.
- **Sin toma de antes de dormir (14 nov 2026, decisión de Alex):** se quitaron el cereal y la leche de las 11:59 PM en los cuatro días; la cena es la última comida. Su proteína pasó al pollo y la carne (A: pollo 175 y 145 g; B: carne 160 g y pollo 185 g; D: pollo 160 g) y, en C y D, su carbohidrato a la pasta y el arroz (C: pasta 290 g; D: pasta 275 g y arroz 170 g). El día A lleva además un segundo kéfir de 150 ml en la comida (vitamina D); el arroz se queda en 80 g. Lucky Charms y Groovies quedan solo en el catálogo. La fibra del día B queda en 37 g (meta 38). `meal_prep` se recalculó con las cantidades nuevas.
- **Déficit para bajar grasa (14 nov 2026, decisión de Alex: empezar ya, no después del maratón):** el día A pierde el plátano de la merienda (−107 kcal; fibra 35.6 g, bajo la meta de 38) y el día B baja la pasta de recuperación a 150 g (−51 kcal). C y D no se tocan. Fuera del menú: caminar 40 min en días A y B. Semana tipo (1 C, 2 D, 2 B, 2 A): −1,071 kcal de comida; con la caminata ~−1,750, unos 0.23 kg de grasa. Semana del maratón (7–13 dic) sin déficit. Ajuste con la báscula: menos de 0.2 kg por semana en dos semanas → siguiente recorte; más de 0.4 → regresa el plátano al día A.
- **Avena Granvita (14 nov 2026):** es la avena del plan en los cuatro días (fila `avena_generica`, valores USDA; gramos A 30, B 40, D 60, C 70, mismo carbohidrato que con Bob's). Trae menos proteína que la Bob's (13 contra 21 g por 100 g): cada día pierde 3 a 6 g. El amaranto ya no es alternativa: Alex no lo compra.
- **Día A en un bowl (14 nov 2026):** comida y cena se fundieron en una sola cena a las 8 PM, sin tostadas: pollo cocido 240 g, arroz 145 g, aguacate 80 g, lechuga 100 g y kéfir 150 ml. Variantes: «Bowl con Susalias» (arroz 80 g + 30 g de Susalias) y, en la merienda, «Medio bagel con queso crema y moras» en lugar de los overnight oats (el kéfir, el shake y el café se quedan; ese día la fibra baja a ~18 g). El bagel es el único alimento entero que se parte: `pieza.paso: 0.5`.
- **Solo bagels (14 nov 2026):** el pre-entreno de B, C y D lleva medio bagel Bimbo (51.5 g) en lugar de la rebanada de Oroweat, con la mermelada bajada 10 g (B 35 g; C y D 40 g) para dejar el mismo carbohidrato. El día D sube el aguacate a 130 g para no bajar de 20 % de grasa (el Oroweat traía mantequilla).
- **Vitamina E corregida (14 nov 2026):** la fila del bagel traía 7.46 mg por 100 g, copiados de una fila del USDA que no cuadra (el mismo bagel tostado da 0.12). Con el valor corregido la vitamina E queda bajo la meta de 15 mg en A (8.7), B (12.2) y D (12.9); antes el día A parecía cubierto solo por el bagel.
- **Día D revisado (14 nov 2026):** la cena ya no lleva arroz (Alex no se lo comía). Su carbohidrato pasó a la sesión: 2 dátiles al terminar (5:15 PM) y spaghetti con pasta 310 g y el doble de puré de tomate (200 g; pidió menos pasta y más salsa). El día queda en 5.05 g/kg de carbohidrato, pegado al mínimo de 5.
- **Micronutrientes completados (14 nov 2026):** arándanos, Lucky Charms, chía, espresso, Groovies y atún Tuny se completaron con el USDA (SR Legacy) solo en lo que la etiqueta no declara; el potasio de los dátiles es el del USDA (696 mg), no el de la etiqueta (340 mg). Los totales de micronutrientes de los cuatro días se recalcularon; no cambió qué queda bajo meta.
- **Lote del 14 nov 2026 (P037–P041):** papas Susalia, pollo empanizado Kirkland, queso crema Reny Picot, atún Tuny y dátiles Medjool entraron **solo al catálogo** (`en_plan: false`); ningún menú cambió. Sus `micros` (y los del camarón) se completaron el 14 nov con el USDA: alimento equivalente ajustado a la proteína o grasa de la etiqueta, o receta por ingredientes (pollo empanizado); el valor de la etiqueta manda donde existe, y `fuente_micros` dice `estimate` cuando es aproximación. Una clave que falta sigue siendo «sin dato», no cero (atún: vitaminas A, D, E, K y colina; papas: E, K y colina). Los dátiles llevan el carbohidrato de la etiqueta (62 g), que es dudoso; el USDA da ~68 g. El paquete traía además P027–P036, que ya estaban aquí desde el 28 oct con los ids del plan (`avena`, `chia`, `bagel`, `tocino`…): no se duplicaron. Quedaron fuera a propósito el tocino crudo (P031, etiqueta mal) y la avena Granvita por etiqueta (P033, valores imposibles; se usa `avena_generica`).
- **Campos extra de etiqueta:** los productos P027–P041 traen además `id_producto`, `categoria`, `marca`, `estado`, `porcion_etiqueta_g`, `macros_detalle` (azúcares, añadidos, trans, mono, poli, colesterol; `null` = no declarado), `micros_extra`, `ingredientes`, `contiene` y `puede_contener`; los cinco nuevos también `observaciones`, `datos_dudosos`, `lecturas_dudosas` y `posible_reemplazo_de`. La app todavía no los lee.
- **Alternativas:** un ítem puede traer `alternativa` (`food_id` + `cantidad`): se puede cambiar por ese alimento sin recalcular el día. Hoy: amaranto en lugar de avena, mismos gramos.
- **Cambios (`alternativas`, desde el 14 nov 2026):** lista de alimentos que pueden tomar el lugar de un ítem, para variar, con los gramos ya calculados (`food_id` + `cantidad`). No se escriben a mano: `PYTHONPATH=. python3 scripts/nutrition_swaps.py backend/data/nutrition` los genera desde su tabla `SWAPS` igualando el macro principal del ítem, y deja fuera el cambio que rompa una regla (`backend/services/nutrition_swaps.py`): kcal del día ±5 %, piso de proteína y de grasa, el día A no baja más de 3 % y ningún límite superior aunque se tomen todos los cambios del día a la vez (por eso el día C no ofrece dátiles: calcio). Pre-entreno, geles, lo de durante y después de la sesión y los suplementos no se cambian. Hay que volver a correrlo al cambiar una cantidad del menú. `alternativa` (una sola) sigue ahí para la versión de la app que solo conoce una.
- **Variantes de comida (`variantes`, desde el 14 nov 2026):** una comida puede traer otra comida completa que la reemplaza (`nombre` + `items`), escrita a mano. Hoy: el sándwich de pechuga empanizada (1 pechuga, 1 bagel Bimbo, ½ cucharada de aderezo Juan Eats, lechuga) en lugar de las tostadas de pollo de la cena de B (con frijoles y kéfir 200 ml) y de D (con frijoles, arroz 60 g, aguacate 120 g y kéfir 150 ml); el kéfir repone la proteína que la pechuga empanizada no trae. Pasa las mismas reglas que los cambios y `nutrition_recompute.py` le calcula los totales. En el día A no se ofrece: se come el déficit. Trae ~1,000 mg más de sodio y ~10 g menos de fibra que las tostadas.
- **Alimentos enteros:** `pieza.entera: true` marca lo que se come entero o no se come (bagel, lata de atún, pechuga empanizada, dátil, tostada, huevo, Maruchan). Un cambio a uno de esos solo se ofrece en piezas completas; si la pieza completa no cabe en las reglas, no se ofrece.
- **Piezas:** `pieza` da equivalencias para mostrar (1 tostada = 12 g, 1 scoop = 31 g, 1 segundo de spray = 0.67 g). La cantidad del plan siempre está en gramos o mililitros.
- **Fuentes:** `fuente_macros` y `fuente_micros` valen `label` (etiqueta del usuario), `usda`, `label+usda`, `estimate` o `label+estimate` (estimado por ingredientes o por un alimento parecido). Si la app muestra datos, conviene distinguir los estimados.
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
- **Proteína en polvo:** revuelta en los overnight oats, que le gustan espesos (desde el 14 nov 2026; antes en shake con agua). Sin oats ese día, en shake.
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
- **Vitamina E:** días A, B y D.
- **Vitamina D:** día A.

## Fuente de verdad

**Desde el 28 oct 2026 la fuente de verdad es este paquete** (decidido por Alex). La ficha del usuario en Claude Docs (pestañas "Menús a detalle" y "Plan v2: con tus productos") se actualiza desde aquí, no al revés.

Para cambiar el menú: edita cantidades o alimentos en `alimentos.json` / `tipos_de_dia.json` y recalcula los totales con `python3 scripts/nutrition_recompute.py backend/data/nutrition` (usa las reglas de redondeo del paquete; verificado contra el día C). Nunca edites totales a mano. Después, lleva los números nuevos a la ficha.

## En la app (Phoenix)

- `backend/services/nutrition.py` lee estos archivos; `GET /nutrition/today` sirve el menú del día.
- El tipo de día sale del plan semanal guardado, no de `calendario.json`. El calendario solo manda en sus días fijos (evento, `sin_deficit`, `PENDIENTE`) y cuando no hay plan.
- **Ninguna comida cruza la medianoche.** La toma de antes de dormir estuvo a las 11:59 PM (antes 1:00 AM) hasta que se quitó el 14 nov 2026. Si una comida vuelve a quedar después de medianoche o fuera de orden, la app rechaza el paquete (503).
- Actualizar = editar los JSON, recalcular, probar (`backend/tests/test_nutrition.py`) y desplegar.
