// Invitation details contain names and activation status only.
import { api, tg } from '../api.js';
import { h, icon, pageHead, skeletonCards, errorState, toast, sectionTitle } from '../ui.js';
import { t, locale } from '../i18n.js';

export default async function render(root, ctx) {
  const box = h('div', { class: 'stack' }, skeletonCards(3, 72));
  root.append(h('div', { class: 'screen' }, pageHead(t('ref.title'), { parent: '/menu' }), box));
  async function load() {
    let info;
    try { info = await api.get('/referral'); }
    catch (e) { if (ctx.alive()) errorState(box, e, load); return; }
    if (!ctx.alive()) return;
    const days = info.subscription_days || {};
    const link = info.invites_left > 0 && info.link;
    const linkField = link ? h('input', { class: 'input', type: 'text', value: link, hidden: true, readOnly: true,
      'aria-label': t('ref.copy'), onclick: (e) => e.target.select() }) : null;
    const share = () => {
      const message = [t('ref.share_text')];
      if (days.friend) message.push(t('ref.days_award', { n: days.friend }));
      const url = 'https://t.me/share/url?url=' + encodeURIComponent(link) + '&text=' + encodeURIComponent(message.join(' '));
      if (tg?.openTelegramLink) tg.openTelegramLink(url);
      else window.open(url, '_blank', 'noopener,noreferrer');
    };
    const copy = async () => {
      try { await navigator.clipboard.writeText(link); toast(t('ref.copied')); }
      catch (e) { linkField.hidden = false; linkField.focus(); linkField.select(); toast(t('ref.copy_failed')); }
    };
    const metric = (key, n) => h('div', {}, h('b', {}, n), h('span', {}, t('ref.balance_' + key)));
    const benefit = (label, title, detail, bonus) => h('div', { class: 'ref-reward' },
      h('small', {}, t('ref.' + label)), h('strong', {}, t('ref.' + title)), h('p', {}, detail),
      bonus > 0 && h('strong', { class: 'ref-days' }, t('ref.days_award', { n: bonus })));
    const statuses = new Set(['joined', 'onboarded', 'activated', 'rewarded', 'expired']);
    const welcome = info.welcome;
    let welcomeCard;
    if (welcome) {
      const detail = welcome.status === 'onboarded'
        ? t('ref.own_hint', { n: welcome.days, target: welcome.target,
            date: new Date(welcome.deadline + 'T12:00:00').toLocaleDateString(locale(), { day: 'numeric', month: 'long' }) })
        : t(welcome.status === 'expired' ? 'ref.own_ended' : 'ref.own_done');
      welcomeCard = h('div', { class: 'card ref-progress' }, icon('sparkles'), h('div', {}, h('strong', {}, t('ref.own')), h('p', {}, detail),
        welcome.status === 'onboarded' && h('progress', { value: welcome.days, max: welcome.target, 'aria-label': detail })));
    }
    box.replaceChildren(...[
      h('section', { class: 'list ref-ticket' },
        h('header', {}, h('h2', {}, t('ref.headline')),
          h('span', { class: 'ref-stamps', 'aria-hidden': 'true' }, icon('star', 24), icon('send', 28))),
        h('div', { class: 'ref-rewards' },
          benefit('for_friend', 'gift', t('ref.gift_hint'), days.friend),
          benefit('for_you', info.rewards_cap_left ? 'host' : 'host_max', t(info.rewards_cap_left ? 'ref.host_hint' : 'ref.limit'), info.rewards_cap_left && days.inviter)),
        link ? h('div', { class: 'ref-actions' },
          h('button', { class: 'btn btn-primary', type: 'button', onclick: share }, icon('send', 18), t('ref.share')),
          h('button', { class: 'btn ref-copy', type: 'button', title: t('ref.copy'), 'aria-label': t('ref.copy'), onclick: copy }, icon('link', 20)),
          linkField) : h('div', { class: 'footnote' }, t(info.invites_left > 0 ? 'ref.unavailable' : 'ref.exhausted'))),
      h('div', { class: 'card ref-metrics' }, metric('invites', info.invites_left), metric('freezes', info.freezes), metric('streak', info.streak)),
      days.balance > 0 && h('div', { class: 'card' }, t('ref.days_balance', { n: days.balance })),
      welcomeCard,
      h('div', { class: 'footnote' }, t('ref.cap', { n: info.rewards_cap_left })),
      sectionTitle(t('ref.friends')),
      info.invited.length ? h('div', { class: 'list' }, info.invited.map(friend => h('div', { class: 'row static' },
        h('div', { class: 'grow ref-friend' }, h('span', { class: 'row-title' }, friend.name || t('ref.friend')),
          h('span', { class: 'row-sub' }, t('ref.' + (statuses.has(friend.status) ? friend.status : 'joined')))))))
        : h('div', { class: 'footnote' }, t('ref.empty'))
    ].filter(Boolean));
  }
  await load();
}
