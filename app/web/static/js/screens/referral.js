// Invitation details contain names and activation status only.
import { api, tg } from '../api.js';
import { h, pageHead, skeletonCards, errorState, toast, sectionTitle } from '../ui.js';
import { t } from '../i18n.js';

export default async function render(root, ctx) {
  const box = h('div', { class: 'stack' }, skeletonCards(3, 72));
  root.append(h('div', { class: 'screen' }, pageHead(t('ref.title'), { parent: '/menu' }), box));
  async function load() {
    let info;
    try { info = await api.get('/referral'); }
    catch (e) { if (ctx.alive()) errorState(box, e, load); return; }
    if (!ctx.alive()) return;
    const link = info.link;
    const linkField = link ? h('input', { class: 'input', type: 'text', value: link, readOnly: true,
      'aria-label': t('ref.copy'), onclick: (e) => e.target.select() }) : null;
    const share = () => {
      const url = 'https://t.me/share/url?url=' + encodeURIComponent(link) + '&text=' + encodeURIComponent(t('ref.share_text'));
      if (tg?.openTelegramLink) tg.openTelegramLink(url);
      else window.open(url, '_blank', 'noopener,noreferrer');
    };
    const copy = async () => {
      try { await navigator.clipboard.writeText(link); toast(t('ref.copied')); }
      catch (e) { linkField.focus(); linkField.select(); toast(t('ref.copy_failed')); }
    };
    const counter = (key, n) => h('div', { class: 'row static' }, t('ref.' + key, { n }));
    const statuses = new Set(['joined', 'onboarded', 'activated']);
    box.replaceChildren(...[
      h('div', { class: 'list' }, counter('invites', info.invites_left), counter('freezes', info.freezes), counter('streak', info.streak)),
      h('div', { class: 'footnote' }, t('ref.rules')),
      h('div', { class: 'footnote' }, t('ref.freeze_note')),
      linkField,
      link && info.invites_left > 0 ? h('button', { class: 'btn btn-primary btn-block', type: 'button', onclick: share }, t('ref.share')) : null,
      link ? h('button', { class: 'btn btn-secondary btn-block', type: 'button', onclick: copy }, t('ref.copy')) : h('div', { class: 'footnote' }, t('ref.unavailable')),
      info.invites_left <= 0 ? h('div', { class: 'footnote' }, t('ref.exhausted')) : null,
      h('div', { class: 'footnote' }, t('ref.cap', { n: info.rewards_cap_left })),
      sectionTitle(t('ref.friends')),
      info.invited.length ? h('div', { class: 'list' }, info.invited.map(friend => h('div', { class: 'row static' },
        h('span', { class: 'grow' }, friend.name || t('ref.friend')),
        h('span', { class: 'row-value' }, t('ref.' + (statuses.has(friend.status) ? friend.status : 'joined'))))))
        : h('div', { class: 'footnote' }, t('ref.empty'))
    ].filter(Boolean));
  }
  await load();
}
