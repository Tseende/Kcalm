-- Фиксируем условия при первом переходе по приглашению.
ALTER TABLE referrals ADD COLUMN friend_bonus_days INTEGER NOT NULL DEFAULT 0 CHECK(friend_bonus_days >= 0);
ALTER TABLE referrals ADD COLUMN inviter_bonus_days INTEGER NOT NULL DEFAULT 0 CHECK(inviter_bonus_days >= 0);
CREATE UNIQUE INDEX idx_subscription_day_award ON reward_log(referral_id, reason)
    WHERE kind = 'subscription_days' AND referral_id IS NOT NULL;
