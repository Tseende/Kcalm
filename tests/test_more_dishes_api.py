"""All new shape values are accepted by the same API used by the Mini App."""
import pytest

from tests.test_web_api import db, cfg, make_client, client, user_id, H, _fixed_now  # noqa: F401
from tests.test_more_dishes import NEW_KINDS

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize('kind', NEW_KINDS)
async def test_new_shape_create_and_edit(client, kind):
    response = await client.post('/api/dishes', json={'name': 'Новая посуда', 'kind': kind,
                                 'diameter_cm': 24, 'volume_ml': 400}, headers=H)
    assert response.status == 201
    saved = await response.json()
    assert saved['kind'] == kind
    response = await client.patch(f"/api/dishes/{saved['id']}", json={'name': 'Моя посуда', 'is_default': True}, headers=H)
    assert response.status == 200
    assert (await response.json())['kind'] == kind


async def test_container_dimensions_roundtrip(client):
    from aiohttp import FormData
    form = FormData()
    for key, value in {'name': 'Контейнер', 'kind': 'container', 'diameter_cm': '22',
                       'width_cm': '14', 'depth_cm': '6'}.items():
        form.add_field(key, value, content_type='text/plain')
    response = await client.post('/api/dishes', data=form, headers=H)
    assert response.status == 201
    saved = await response.json()
    assert (saved['diameter_cm'], saved['width_cm'], saved['depth_cm']) == (22, 14, 6)
    response = await client.patch(f"/api/dishes/{saved['id']}",
                                  json={'width_cm': 22}, headers=H)
    assert response.status == 200
    assert (await response.json())['width_cm'] == 22
    response = await client.get('/api/dishes', headers=H)
    assert (await response.json())['dishes'][0]['width_cm'] == 22
    response = await client.patch(f"/api/dishes/{saved['id']}",
                                  json={'width_cm': None}, headers=H)
    assert response.status == 200
    assert (await response.json())['width_cm'] is None


@pytest.mark.parametrize('width', [-1, 0, 61, 'oops'])
async def test_container_width_validation(client, width):
    response = await client.post('/api/dishes', json={'name': 'Контейнер',
                                 'kind': 'container', 'width_cm': width}, headers=H)
    assert response.status == 400
