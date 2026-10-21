# Plan de nutrición de Alex: paquete para integrar en la app

Versión 2026-10-21. Este paquete contiene el plan de nutrición completo como datos, listo para que la app lo muestre y lo use. Tú ya conoces la app: aquí solo va qué datos hay, cómo leerlos y qué reglas aplicar. Cómo modelarlo y mostrarlo es decisión tuya.

## Qué es el plan

- **Usuario:** hombre de 22 años, 170 cm y 74.3 kg, que prepara su primer maratón (Monterrey, 13 dic 2026, meta sub-3:20).
- **Objetivo:** perder grasa con un déficit ligero de ~300 kcal al día en promedio, sin comprometer el entrenamiento.
- **Estructura:** cada día del calendario usa uno de **4 tipos de día**: A, B, C o D. Cada tipo tiene un menú fijo con horarios, alimentos y cantidades en gramos o mililitros.
- **Productos:** son los que el usuario compra, con valores tomados de sus etiquetas. Lo que no tiene etiqueta sale del USDA.

| Tipo | Cuándo | kcal | Proteína | Carbohidrato | Déficit vs gasto |
|---|---|---|---|---|---|
| A | Descanso o fuerza ligera | 2,095 | 158 g | 220 g | −255 |
| B | Carrera suave o fuerza pesada | 2,199 | 157 g | 245 g | −301 |
| C | Tirada larga (domingo) | 3,201 | 180 g | 450 g | −99 |
| D | Sesión de calidad (tempo o ritmo de maratón) | 2,792 | 157 g | 380 g | ~0 |

## Archivos

| Archivo | Contenido |
|---|---|
| `alimentos.json` | Catálogo de 42 alimentos y suplementos. 34 se usan en el plan (`en_plan: true`); el resto son alternativas ya calculadas. |
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
- **Límites que no conviene romper al editar:**
  - Calcio del día C (está en 2,424 de 2,500 mg).
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

Los mismos números están en la ficha del usuario (Claude Docs, pestañas "Plan v2: con tus productos", "Menús a detalle" y "Requerimientos"). Si el usuario cambia el plan ahí, este paquete se regenera; no edites los totales a mano.

## En la app (Phoenix)

- `backend/services/nutrition.py` lee estos archivos; `GET /nutrition/today` sirve el menú del día.
- El tipo de día sale del plan semanal guardado, no de `calendario.json`. El calendario solo manda en sus días fijos (evento, `sin_deficit`, `PENDIENTE`) y cuando no hay plan.
- **Antes de dormir es a las 11:59 PM** (era 1:00 AM; cambiado el 2026-10-21 para que ninguna comida cruce la medianoche). Si el paquete se regenera con 1:00 AM, la app lo rechaza (503). Cambia la hora también en la ficha de Claude Docs.
- Actualizar = reemplazar los JSON y desplegar.
