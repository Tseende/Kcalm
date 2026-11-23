"""Referral access, one-time rewards, and streak recovery on a migrated database."""
from datetime import date
from unittest.mock import patch

import pytest

from app.services import referral, motivation
from app.storage import repo
from tests.test_web_api import db, cfg, add_user  # noqa: F401


@pytest.fixture
def inviter(db, cfg):
    uid = add_user(db, 1001)
    return repo.get_user_by_id(db, uid)


def join(db, inviter, tg_id=2001):
    code = referral.ensure_code(db, inviter)
    assert referral.redeem(db, tg_id, code, 'Друг') == 'granted'
    uid = add_user(db, tg_id)
    assert referral.on_onboarded(db, tg_id, uid)
    return uid


def meal(db, uid, day):
    db.execute("INSERT INTO meals (user_id,eaten_at_utc,local_date,local_time,meal_type,source) VALUES (?,?,?,'12:00','lunch','manual')",
               (uid, day + ' 09:00:00', day))
    db.commit()


def test_invite_spent_once_and_access_persists(db, inviter):
    code = referral.ensure_code(db, inviter)
    assert referral.redeem(db, 2001, code) == 'granted'
    assert referral.redeem(db, 2001, code) == 'ignored'
    assert referral.has_access(db, 2001)
    assert repo.get_user_by_id(db, inviter['id'])['invites_left'] == 2
    assert referral.redeem(db, 2002, 'nonexistent') == 'invalid'
    assert referral.redeem(db, 1001, code) == 'ignored'
    assert referral.redeem(db, 2002, code) == 'granted'
    assert referral.redeem(db, 2003, code) == 'granted'
    assert referral.redeem(db, 2004, code) == 'exhausted'
    assert not repo.has_access_grant(db, 2004)


def test_welcome_bonus_once_and_atomic(db, inviter):
    code = referral.ensure_code(db, inviter)
    referral.redeem(db, 2001, code)
    uid = add_user(db, 2001)
    with patch.object(repo, 'add_reward', side_effect=RuntimeError('failure')):
        with pytest.raises(RuntimeError):
            referral.on_onboarded(db, 2001, uid)
    assert repo.referral_of_invitee_tg(db, 2001)['onboarded_at'] is None
    assert referral.on_onboarded(db, 2001, uid)
    assert not referral.on_onboarded(db, 2001, uid)
    assert repo.get_user_by_id(db, uid)['streak_freezes'] == 1


def test_activation_requires_three_distinct_days_and_rewards_once(db, inviter, monkeypatch):
    monkeypatch.setattr(referral, '_utcnow', lambda: '2026-11-10 12:00:00')
    uid = join(db, inviter)
    for day in ['2026-11-10', '2026-11-10', '2026-11-11']:
        meal(db, uid, day)
    monkeypatch.setattr(referral, '_utcnow', lambda: '2026-11-12 12:00:00')
    referral.check_activation(db, uid)
    assert repo.get_user_by_id(db, inviter['id'])['streak_freezes'] == 0
    meal(db, uid, '2026-11-12')
    referral.check_activation(db, uid)
    referral.check_activation(db, uid)
    owner = repo.get_user_by_id(db, inviter['id'])
    assert (owner['streak_freezes'], owner['invites_left']) == (1, 3)
    assert repo.rewarded_referrals(db, inviter['id']) == 1


def test_no_activation_after_first_week(db, inviter, monkeypatch):
    monkeypatch.setattr(referral, '_utcnow', lambda: '2026-11-01 12:00:00')
    uid = join(db, inviter)
    for day in ['2026-11-01', '2026-11-02', '2026-11-03']:
        meal(db, uid, day)
    monkeypatch.setattr(referral, '_utcnow', lambda: '2026-11-08 12:00:00')
    referral.check_activation(db, uid)
    assert repo.rewarded_referrals(db, inviter['id']) == 0


def test_reward_cap(db, inviter, cfg, monkeypatch):
    monkeypatch.setattr(cfg, "referral_inviter_days", 7)
    monkeypatch.setattr(referral, '_utcnow', lambda: '2026-11-10 12:00:00')
    for i in range(11):
        uid = join(db, inviter, 3000 + i)
        for day in ['2026-11-10', '2026-11-11', '2026-11-12']:
            meal(db, uid, day)
        monkeypatch.setattr(referral, '_utcnow', lambda: '2026-11-12 12:00:00')
        referral.check_activation(db, uid)
        monkeypatch.setattr(referral, '_utcnow', lambda: '2026-11-10 12:00:00')
    assert repo.subscription_day_balance(db, inviter['id']) == 70
    assert repo.rewarded_referrals(db, inviter['id']) == 10
    assert repo.get_user_by_id(db, inviter['id'])['streak_freezes'] == 10


def test_freeze_covers_one_gap_once(db, inviter, monkeypatch):
    uid = inviter['id']
    repo.add_reward(db, uid, 'freeze', 2, 'test')
    db.commit()
    monkeypatch.setattr(referral, 'local_today', lambda tz: date(2026, 11, 15))
    meal(db, uid, '2026-11-13')
    meal(db, uid, '2026-11-15')
    referral.on_meal_saved(db, uid, '2026-11-15')
    referral.on_meal_saved(db, uid, '2026-11-15')
    assert motivation.logging_streak(db, uid, date(2026, 11, 15)) == 3
    assert repo.get_user_by_id(db, uid)['streak_freezes'] == 1
    assert not repo.spend_freeze(db, uid, '2026-11-14')
    assert repo.get_user_by_id(db, uid)['streak_freezes'] == 1


def test_two_day_gap_and_backdated_entry_do_not_spend_freeze(db, inviter, monkeypatch):
    uid = inviter['id']
    repo.add_reward(db, uid, 'freeze', 1, 'test')
    db.commit()
    monkeypatch.setattr(referral, 'local_today', lambda tz: date(2026, 11, 15))
    meal(db, uid, '2026-11-12')
    referral.on_meal_saved(db, uid, '2026-11-15')
    meal(db, uid, '2026-11-13')
    referral.on_meal_saved(db, uid, '2026-11-13')
    assert repo.get_user_by_id(db, uid)['streak_freezes'] == 1


def test_meal_save_listener_activates_and_notifies(db, inviter, monkeypatch):
    # Exercise the real save path rather than invoking activation directly.
    monkeypatch.setattr(referral, '_utcnow', lambda: '2026-11-10 12:00:00')
    uid = join(db, inviter)
    meal(db, uid, '2026-11-10')
    meal(db, uid, '2026-11-11')
    monkeypatch.setattr(referral, '_utcnow', lambda: '2026-11-12 12:00:00')
    messages = []
    referral.install(lambda tg_id, text: messages.append((tg_id, text)))
    try:
        from types import SimpleNamespace
        calc = SimpleNamespace(items=[], kcal_total=100, protein_g=5, fat_g=3, carbs_g=12,
                               kcal_low=100, kcal_high=100, confidence=1, method='db')
        repo.save_meal(db, uid, '2026-11-12 09:00:00', '2026-11-12', '12:00', 'lunch', 'manual', calc)
    finally:
        referral.uninstall()
    assert repo.rewarded_referrals(db, inviter['id']) == 1
    assert {tg_id for tg_id, _ in messages} == {1001, 2001}


def test_personal_progress_and_expiration(db, inviter, monkeypatch):
    monkeypatch.setattr(referral, '_utcnow', lambda: '2026-11-01 12:00:00')
    uid = join(db, inviter)
    friend = repo.get_user_by_id(db, uid)
    meal(db, uid, '2026-11-01')
    meal(db, uid, '2026-11-01')
    own = referral.summary(db, friend, 'bot')['welcome']
    assert own == {'status': 'onboarded', 'days': 1, 'target': 3, 'deadline': '2026-11-07'}
    monkeypatch.setattr(referral, '_utcnow', lambda: '2026-11-08 12:00:00')
    assert referral.summary(db, friend, 'bot')['welcome'] == {'status': 'expired'}
    assert referral.summary(db, inviter, 'bot')['invited'] == [{'name': 'Друг', 'status': 'expired'}]


def test_subscription_days_keep_accepted_terms_and_credit_once(db, cfg, inviter, monkeypatch):
    monkeypatch.setattr(cfg, 'referral_friend_days', 3)
    monkeypatch.setattr(cfg, 'referral_inviter_days', 7)
    monkeypatch.setattr(referral, '_utcnow', lambda: '2026-11-10 12:00:00')
    code = referral.ensure_code(db, inviter)
    assert referral.redeem(db, 2001, code) == 'granted'
    monkeypatch.setattr(cfg, 'referral_friend_days', 0)
    monkeypatch.setattr(cfg, 'referral_inviter_days', 0)
    uid = add_user(db, 2001)
    assert referral.on_onboarded(db, 2001, uid)
    assert not referral.on_onboarded(db, 2001, uid)
    assert repo.subscription_day_balance(db, uid) == 3
    for day in ['2026-11-10', '2026-11-11', '2026-11-12']:
        meal(db, uid, day)
    monkeypatch.setattr(referral, '_utcnow', lambda: '2026-11-12 12:00:00')
    referral.check_activation(db, uid)
    referral.check_activation(db, uid)
    assert referral.summary(db, inviter, 'bot')['subscription_days'] == {'balance': 7, 'friend': 0, 'inviter': 0}
    later = join(db, inviter, 2002)
    assert repo.subscription_day_balance(db, later) == 0
