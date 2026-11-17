"""Additional plate shapes: persistence, dimensions and safe schema upgrade."""
import pytest

from app.db import connect, migrate, MIGRATIONS_DIR, _set_version, user_version
from app.domain import dishes
from app.storage import repo
from tests.test_v2_dishes import plain_db  # noqa: F401

NEW_KINDS = ('dessert_plate', 'square_plate', 'oval_plate', 'rectangular_plate',
             'pasta_plate', 'divided_plate', 'platter', 'ramekin')


@pytest.mark.parametrize('kind', NEW_KINDS)
def test_new_shape_roundtrips(plain_db, kind):
    uid = repo.create_user(plain_db, 1)
    did = repo.create_dish(plain_db, uid, dishes.KIND_RU[kind], kind,
                           diameter_cm=24, depth_cm=3, volume_ml=400)
    saved = repo.get_dish(plain_db, uid, did)
    assert saved['kind'] == kind
    assert dishes.KIND_RU[kind] in dishes.describe_dish(saved)
    assert saved['diameter_cm'] == 24
    assert repo.update_dish(plain_db, uid, did, is_default=True)
    assert repo.default_dish(plain_db, uid)['id'] == did


@pytest.mark.parametrize('text,kind,description', [
    ('квадратная тарелка сторона 22 см', 'square_plate', 'сторона 22 см'),
    ('контейнер сторона 18 см 750 мл', 'container', 'длина 18 см'),
    ('овальная тарелка длина 30 см', 'oval_plate', 'длина 30 см'),
    ('прямоугольная тарелка 30 см', 'rectangular_plate', 'длина 30 см'),
    ('десертная тарелка 18 см', 'dessert_plate', 'Ø18 см'),
    ('тарелка для пасты 28 см 500 мл', 'pasta_plate', 'объём 500 мл'),
    ('тарелка с секциями 24 см', 'divided_plate', 'Ø24 см'),
    ('сервировочное блюдо длина 35 см', 'platter', 'длина 35 см'),
    ('рамекин 150 мл', 'ramekin', 'объём 150 мл'),
])
def test_shape_description_uses_real_dimension(text, kind, description):
    parsed = dishes.parse_dish_text(text)
    assert parsed['kind'] == kind
    assert description in dishes.describe_dish(parsed)


def test_upgrade_preserves_old_dishes_photos_and_links(tmp_path):
    db = connect(tmp_path / 'upgrade.db')
    user_version(db)  # Creates version tracking on libSQL, without applying migrations.
    try:
        for path in sorted(MIGRATIONS_DIR.glob('*.sql')):
            version = int(path.name.split('_')[0])
            if version > 31:
                break
            db.executescript(path.read_text())
            _set_version(db, version)
        db.commit()
        uid = repo.create_user(db, 1)
        did = db.execute("INSERT INTO dishes (user_id,name,kind,diameter_cm,photo_path,is_default) VALUES (?,?,?,?,?,?)",
                         (uid, 'Моя тарелка', 'plate', 26, 'dishes/original.jpg', 1)).lastrowid
        db.execute("INSERT INTO meals(user_id,eaten_at_utc,local_date,local_time,meal_type,source) VALUES (?, '2026-11-16 09:00:00','2026-11-16','12:00','lunch','photo')", (uid,))
        mid = db.execute('SELECT id FROM meals').fetchone()[0]
        db.execute('INSERT INTO meal_photos(meal_id,path,dish_id) VALUES (?,?,?)', (mid, 'meal.jpg', did))
        db.commit()
        before = dict(repo.get_dish(db, uid, did))
        latest = max(int(p.name.split("_")[0]) for p in MIGRATIONS_DIR.glob("*.sql"))
        assert migrate(db) == latest
        assert dict(repo.get_dish(db, uid, did)) == {**before, "width_cm": None}
        assert db.execute('SELECT dish_id FROM meal_photos WHERE meal_id=?', (mid,)).fetchone()[0] == did
        assert migrate(db) == latest
        assert not db.execute('PRAGMA foreign_key_check').fetchall()
        repo.create_dish(db, uid, 'Квадратная', 'square_plate', diameter_cm=22)
    finally:
        db.close()


def test_all_shape_options_have_images_and_frontend_labels():
    import re
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    pics = (root / 'app/web/static/js/pics.js').read_text()
    ui = (root / 'app/web/static/js/ui.js').read_text()
    mapping = re.search(r'const DISH_PICS = \{(.*?)\};', pics, re.S).group(1)
    images = dict(re.findall(r"(\w+): '([^']+)'", mapping))
    assert set(images) == set(repo.DISH_KINDS) - {'other'}
    assert set(repo.DISH_KINDS) == set(dishes.KIND_RU)
    for kind, filename in images.items():
        assert (root / 'app/web/static/img/dish' / (filename + '.webp')).is_file(), kind
        assert f"{kind}: {{ label: t('dish.{kind}')" in ui, kind


def test_container_description_contains_both_dimensions(plain_db):
    uid = repo.create_user(plain_db, 1)
    did = repo.create_dish(plain_db, uid, 'Ланчбокс', 'container',
                           diameter_cm=22, width_cm=14, depth_cm=6)
    description = dishes.describe_dish(repo.get_dish(plain_db, uid, did))
    assert 'длина 22 см' in description
    assert 'ширина 14 см' in description
    assert 'глубина 6 см' in description
    assert 'Ø' not in description
