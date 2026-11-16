# Additional tableware icons

Eight new transparent 112×112 WebP assets, generated with built-in `image_gen`.

Final assets: `app/web/static/img/dish/<key>.webp`.
Preview: `docs/dish-cards-preview.html`.

## Prompt set

Each image used this exact prompt, substituting the subject below:

Use case: stylized-concept. Asset type: single empty tableware icon for a food diary app. Subject: {subject} Style: polished Apple emoji-like 3D illustration, softly shaded ceramic material, crisp readable silhouette at 48 pixels. Three-quarter view slightly from above showing the interior and depth. Centered square composition with generous transparent padding. Exactly one vessel, no food, no utensils, no table, no environment, no lettering, no logos, no watermark. Actual transparent background. Consistent clean white and ivory dinnerware aesthetic.

| Key | Subject |
| --- | --- |
| dessert_plate | One small round white porcelain dessert plate with a delicate pale blue rim. Empty. |
| square_plate | One square white ceramic dinner plate with softly rounded corners and a shallow recessed center. Empty. |
| oval_plate | One oval pale ivory ceramic dinner plate with a narrow raised rim. Empty. |
| rectangular_plate | One rectangular white ceramic serving plate with rounded corners, noticeably longer than wide. Empty. |
| pasta_plate | One white porcelain pasta plate with a very wide flat rim and a small deep round well in the center. Empty. |
| divided_plate | One round light sage green ceramic divided plate with three visibly separate compartments, no lid. Empty. |
| platter | One large elongated oval white porcelain serving platter with a subtle fine gold rim and two small integrated side handles. Empty. |
| ramekin | One small round ivory ceramic baking ramekin with tall vertically fluted sides, clearly deeper than a plate. Empty. |

## Integration

- Eight explicit dish kinds in the domain, repository, web form and image map.
- Migration 032 expands the SQL kind constraint, preserving ids, photos, default selection and meal-photo links.
- Existing `diameter_cm` stores the measured size: diameter for round dishes, side for square dishes, longest dimension for oval/rectangular dishes and platters. The UI and model description use the corresponding labels. New fields or guessed dimensions are not introduced.
- The user enters their own size, depth and volume. Icon proportions are illustrative.
- Chat parsing supports these dish types as well.
- Existing dishes and their original images remain available.
- Conversion: `python tools/prepare_food_icon.py source.png key --kind dish`, preserving alpha.
- Validation: new shape CRUD, web API, migration preservation, old dish tests, frontend asset coverage and manual mock UI creation.
