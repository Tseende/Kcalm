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
