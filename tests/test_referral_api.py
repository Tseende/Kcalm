"""Signed Mini App invites and bot /start access use the same persisted grants."""
import json
import time
from types import SimpleNamespace
from urllib.parse import urlencode

from aiogram.types import Message, User, Chat

from app.middleware import _admitted
from app.services import referral
from app.storage import repo
from app.web.auth import compute_hash
from tests.test_web_api import db, cfg, make_client, add_user, H, TOKEN  # noqa: F401

def signed(tg_id, code=None):
    data = {'auth_date': str(int(time.time())), 'user': json.dumps({'id': tg_id, 'first_name': 'Друг'})}
    if code:
        data['start_param'] = 'r_' + code
    data['hash'] = compute_hash(data, TOKEN)
    return {'Authorization': 'tma ' + urlencode(data)}


async def test_signed_invite_opens_setup_and_access_persists(db, cfg, make_client):
    uid = add_user(db, 1001)
    code = referral.ensure_code(db, repo.get_user_by_id(db, uid))
    client = await make_client()
    response = await client.get('/api/me', headers=signed(5001, code))
    assert response.status == 200
    assert (await response.json())['onboarded'] is False
    assert (await client.get('/api/me', headers=signed(5001))).status == 200
    assert repo.get_user_by_id(db, uid)['invites_left'] == 2
    assert (await client.get('/api/referral', headers=signed(5001))).status == 409
    assert (await client.get('/api/me', headers=signed(5002, 'badcode'))).status == 401
    assert not repo.has_access_grant(db, 5002)


async def test_summary_includes_link_counts_and_no_health_data(db, cfg, make_client, monkeypatch):
    uid = add_user(db, 1001)
    async def username(bot):
        return 'test_bot'
    monkeypatch.setattr(referral, 'bot_username', username)
    client = await make_client()
    response = await client.get('/api/referral', headers=H)
    assert response.status == 200
    data = await response.json()
    assert data['link'].startswith('https://t.me/test_bot?start=r_')
    assert (data['invites_left'], data['freezes'], data['rewards_cap_left']) == (3, 0, 10)
    assert data['invited'] == []


async def test_bot_start_admits_once(db, cfg):
    uid = add_user(db, 1001)
    code = referral.ensure_code(db, repo.get_user_by_id(db, uid))
    user = User(id=5001, is_bot=False, first_name='Друг')
    message = Message(message_id=1, date=int(time.time()), chat=Chat(id=5001, type='private'), from_user=user,
                      text='/start r_' + code)
    assert await _admitted(message, user, {'db': db})
    assert await _admitted(message, user, {'db': db})
    assert repo.get_user_by_id(db, uid)['invites_left'] == 2


async def test_public_invitation_in_app_and_bot(db, make_client, tmp_path):
    from app.middleware import AccessMiddleware
    from tests.test_web_api import make_config
    config = make_config(tmp_path, allow_public_bot=True, allowed_tg_ids="")
    client = await make_client(config)
    uid = add_user(db, 1001)
    code = referral.ensure_code(db, repo.get_user_by_id(db, uid))
    for _ in range(2):
        assert (await client.get('/api/me', headers=signed(5001, code))).status == 200
    assert (await client.get('/api/me', headers=signed(5003, 'badcode'))).status == 200
    user = User(id=5002, is_bot=False, first_name='Друг')
    message = Message(message_id=1, date=int(time.time()), chat=Chat(id=5002, type='private'),
                      from_user=user, text='/start r_' + code)

    async def handler(event, data):
        return True

    middleware = AccessMiddleware()
    for _ in range(2):
        assert await middleware(handler, message, {'db': db})
    assert repo.get_user_by_id(db, uid)['invites_left'] == 1
    assert len(repo.list_referrals(db, uid)) == 2
