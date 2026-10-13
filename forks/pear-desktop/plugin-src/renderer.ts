/*
 * Pear Connect, page YouTube Music :
 *  - bouton « appareils » dans la barre du lecteur (liste, transfert, volume) ;
 *  - barre « Lecture sur … » quand un autre appareil joue (titre, progression en direct,
 *    lecture/pause, précédent/suivant, déplacement, volume, « Écouter ici ») ;
 *  - lecture locale envoyée au processus principal (position, file, volume) ;
 *  - reprise d'une session : titre ouvert DANS son mix par la navigation interne de
 *    YouTube Music, sans redémarrer Pear, puis position recalée.
 * DOM construit sans innerHTML (Trusted Types).
 */
import { createRenderer } from '@/utils';

import {
  type ActionResult,
  type Command,
  type DeviceView,
  type Snapshot,
  type UiAction,
  QUEUE_LIMIT,
  VIDEO_ID_RE,
  indexOfVideo,
  locateUpcoming,
  parseQueue,
  upcomingRows,
  upcomingTarget,
  watchEndpoint,
} from './protocol';

import type { LocalReport } from './backend';
import type { PearConnectConfig } from './config';
import type { RendererContext } from '@/types/contexts';
import type { MusicPlayer } from '@/types/music-player';
import type { MusicPlayerAppElement } from '@/types/music-player-app-element';
import type { QueueElement } from '@/types/queue';

const SVG_NS = 'http://www.w3.org/2000/svg';
const ACCENT = '#1ed760';
const ICONS = {
  devices:
    'M4 6h18V4H4c-1.1 0-2 .9-2 2v11H0v3h14v-3H4V6zm19 2h-6c-.55 0-1 .45-1 1v10c0 .55.45 1 1 1h6c.55 0 1-.45 1-1V9c0-.55-.45-1-1-1zm-1 9h-4v-7h4v7z',
  computer:
    'M20 18c1.1 0 1.99-.9 1.99-2L22 6c0-1.1-.9-2-2-2H4c-1.1 0-2 .9-2 2v10c0 1.1.9 2 2 2H0v2h24v-2h-4zM4 6h16v10H4V6z',
  phone:
    'M16 1H8C6.34 1 5 2.34 5 4v16c0 1.66 1.34 3 3 3h8c1.66 0 3-1.34 3-3V4c0-1.66-1.34-3-3-3zm-2 20h-4v-1h4v1zm3.25-3H6.75V4h10.5v14z',
  play: 'M8 5v14l11-7z',
  pause: 'M6 19h4V5H6v14zm8-14v14h4V5h-4z',
  next: 'M6 18l8.5-6L6 6v12zM16 6v12h2V6h-2z',
  prev: 'M6 6h2v12H6zm3.5 6l8.5 6V6z',
  volume:
    'M3 9v6h4l5 5V4L7 9H3zm13.5 3c0-1.77-1.02-3.29-2.5-4.03v8.05c1.48-.73 2.5-2.25 2.5-4.02zM14 3.23v2.06c2.89.86 5 3.54 5 6.71s-2.11 5.85-5 6.71v2.06c4.01-.91 7-4.49 7-8.77s-2.99-7.86-7-8.77z',
  queue:
    'M15 6H3v2h12V6zm0 4H3v2h12v-2zM3 16h8v-2H3v2zM17 6v8.18c-.31-.11-.65-.18-1-.18-1.66 0-3 1.34-3 3s1.34 3 3 3 3-1.34 3-3V8h3V6h-5z',
  speaker:
    'M17 2H7c-1.1 0-2 .9-2 2v16c0 1.1.9 1.99 2 1.99L17 22c1.1 0 2-.9 2-2V4c0-1.1-.9-2-2-2zm-5 2c1.1 0 2 .9 2 2s-.9 2-2 2a2 2 0 1 1 0-4zm0 16c-2.76 0-5-2.24-5-5s2.24-5 5-5 5 2.24 5 5-2.24 5-5 5zm0-8c-1.66 0-3 1.34-3 3s1.34 3 3 3 3-1.34 3-3-1.34-3-3-3z',
  up: 'M7 14l5-5 5 5z',
  down: 'M7 10l5 5 5-5z',
  close:
    'M19 6.41L17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z',
} as const;
type IconName = keyof typeof ICONS;
const KIND_ICON: Record<string, IconName> = {
  pear: 'computer',
  android: 'phone',
};

const STYLE = `
:host { all: initial; }
* { box-sizing: border-box; font-family: Roboto, Arial, sans-serif; }
button { font: inherit; color: inherit; background: none; border: 0; cursor: pointer; padding: 0; }
button:focus-visible { outline: 2px solid ${ACCENT}; outline-offset: 2px; border-radius: 50%; }
.bar { position: fixed; left: 0; right: 0; bottom: 0; height: var(--pc-height, 72px); z-index: 2000;
  display: none; grid-template-columns: minmax(0, 1fr) minmax(280px, 2fr) minmax(0, 1fr); align-items: center;
  gap: 16px; padding: 0 16px; background: #212121; color: #fff; border-top: 1px solid rgba(255,255,255,.08); }
.bar.show { display: grid; }
.now { display: flex; align-items: center; gap: 12px; min-width: 0; }
.art { width: 48px; height: 48px; flex: none; border-radius: 4px; background: #333 center / auto 134% no-repeat; }
.meta { min-width: 0; }
.title { font-size: 14px; font-weight: 500; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.artist { font-size: 13px; color: #aaa; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.where { display: flex; align-items: center; gap: 4px; font-size: 12px; color: ${ACCENT}; margin-top: 2px;
  white-space: nowrap; overflow: hidden; }
.center { display: flex; flex-direction: column; align-items: center; gap: 2px; min-width: 0; }
.controls { display: flex; align-items: center; gap: 12px; }
.round { width: 40px; height: 40px; display: grid; place-items: center; border-radius: 50%; color: #fff; }
.round:hover { background: rgba(255,255,255,.1); }
.round.big { background: #fff; color: #000; }
.round.big:hover { background: #ddd; }
.progress { display: flex; align-items: center; gap: 8px; width: 100%; font-size: 12px; color: #aaa;
  font-variant-numeric: tabular-nums; }
.track { position: relative; flex: 1; height: 16px; cursor: pointer; }
.track::before { content: ""; position: absolute; left: 0; right: 0; top: 7px; height: 3px; border-radius: 2px; background: #555; }
.fill { position: absolute; left: 0; top: 7px; height: 3px; border-radius: 2px; background: #fff; }
.track:hover .fill { background: ${ACCENT}; }
.right { display: flex; align-items: center; justify-content: flex-end; gap: 8px; min-width: 0; }
.vol { display: flex; align-items: center; gap: 4px; color: #aaa; }
input[type=range] { width: 96px; accent-color: ${ACCENT}; }
.here { height: 32px; padding: 0 14px; border-radius: 16px; background: ${ACCENT}; color: #000; font-size: 13px;
  font-weight: 500; white-space: nowrap; }
.here:hover { filter: brightness(1.1); }
.devbtn { color: ${ACCENT}; }
.pop { position: fixed; right: 16px; bottom: calc(var(--pc-height, 72px) + 8px); z-index: 2001; width: 320px;
  max-height: 60vh; overflow: auto; display: none; padding: 16px 8px 8px; border-radius: 8px; background: #282828;
  color: #fff; box-shadow: 0 8px 24px rgba(0,0,0,.5); }
.pop.show { display: block; }
.pop h2 { margin: 0 8px 4px; font-size: 16px; font-weight: 700; }
.pop p.sub { margin: 0 8px 12px; font-size: 12px; color: #aaa; }
.dev { display: flex; align-items: center; gap: 12px; width: 100%; padding: 10px 8px; border-radius: 6px; text-align: left; }
.dev:hover:not([disabled]) { background: rgba(255,255,255,.08); }
.dev[disabled] { cursor: default; }
.dev .name { font-size: 14px; font-weight: 500; }
.dev .state { font-size: 12px; color: #aaa; }
.dev.active .name, .dev.active .state, .dev.active svg { color: ${ACCENT}; }
.dev.offline { opacity: .45; }
.popvol { display: flex; align-items: center; gap: 8px; margin: 8px; color: #aaa; }
.popvol input { flex: 1; width: auto; }
.empty { margin: 8px; font-size: 13px; color: #aaa; }
.qpop { right: 340px; width: 360px; }
.qrow { display: flex; align-items: center; gap: 2px; width: 100%; }
.qmain { display: flex; align-items: center; gap: 10px; flex: 1; min-width: 0; padding: 6px 8px; border-radius: 6px; text-align: left; }
.qmain:hover { background: rgba(255,255,255,.08); }
.qctl { width: 28px; height: 28px; flex: none; display: grid; place-items: center; border-radius: 50%; color: #aaa; }
.qctl:hover:not([disabled]) { background: rgba(255,255,255,.1); color: #fff; }
.qctl[disabled] { opacity: .3; cursor: default; }
.qart { width: 40px; height: 40px; flex: none; border-radius: 4px; background: #333 center / auto 134% no-repeat; }
.qrow .name { font-size: 13px; font-weight: 500; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.qrow .state { font-size: 12px; color: #aaa; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.toast { position: fixed; left: 50%; bottom: calc(var(--pc-height, 72px) + 16px); transform: translateX(-50%);
  z-index: 2002; max-width: 80vw; padding: 10px 16px; border-radius: 6px; background: #f1f1f1; color: #000;
  font-size: 13px; opacity: 0; transition: opacity .2s; pointer-events: none; }
.toast.show { opacity: 1; }
`;

type Props = Record<string, unknown> & {
  ctx?: RendererContext<PearConnectConfig>;
  playerApi?: MusicPlayer;
  timers: number[];
  teardown?: () => void;
  report(): void;
  diag(message: string): void;
  lastDiag?: string;
  runCommand(cmd: Command): Promise<void>;
  resume(
    cmd: Extract<Command, { type: 'resume' }>,
    receivedAt: number,
  ): Promise<void>;
  replaceUpcoming(ids: string[]): Promise<void>;
  sameUpcoming(ids: string[]): boolean;
};

const queueElement = () => document.querySelector<QueueElement>('#queue');

const wait = async (pred: () => boolean, timeoutMs: number, stepMs = 200) => {
  const end = Date.now() + timeoutMs;
  while (Date.now() < end) {
    if (pred()) return true;
    await new Promise((r) => setTimeout(r, stepMs));
  }
  return pred();
};

// -- utilitaires DOM ----------------------------------------------------------------
type Child = Node | string | null | undefined | false;
function h(
  tag: string,
  props: Record<string, string | ((ev: Event) => void)> = {},
  ...children: Child[]
): HTMLElement {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (typeof v === 'function') el.addEventListener(k.slice(2), v);
    else if (k === 'text') el.textContent = v;
    else el.setAttribute(k, v);
  }
  for (const c of children) if (c) el.append(c);
  return el;
}
function icon(name: IconName, size = 24): SVGSVGElement {
  const svg = document.createElementNS(SVG_NS, 'svg');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('width', String(size));
  svg.setAttribute('height', String(size));
  svg.setAttribute('aria-hidden', 'true');
  svg.style.fill = 'currentColor';
  svg.style.display = 'block';
  svg.style.pointerEvents = 'none';
  const path = document.createElementNS(SVG_NS, 'path');
  path.setAttribute('d', ICONS[name]);
  svg.append(path);
  return svg;
}
const fmt = (sec: number) => {
  const s = Math.max(0, Math.floor(sec || 0));
  const m = Math.floor(s / 60);
  return m >= 60
    ? `${Math.floor(m / 60)}:${String(m % 60).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`
    : `${m}:${String(s % 60).padStart(2, '0')}`;
};

/** Interface : construite une fois, mise à jour à chaque snapshot du serveur. */
function createUi(send: (action: UiAction) => Promise<ActionResult>) {
  let snap: Snapshot | null = null;
  let self = '';
  let status = '';
  let snapAt = 0;

  const host = h('div', { id: 'pear-connect-root' });
  const root = host.attachShadow({ mode: 'closed' });
  root.append(h('style', { text: STYLE }));

  const toastEl = h('div', { class: 'toast', role: 'status' });
  let toastTimer = 0;
  const toast = (message: string) => {
    toastEl.textContent = message;
    toastEl.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = window.setTimeout(
      () => toastEl.classList.remove('show'),
      3500,
    );
  };
  const act = async (action: UiAction, okMessage?: string) => {
    const res = await send(action);
    if (!res.ok) toast(res.error || 'action impossible');
    else if (okMessage) toast(okMessage);
    return res.ok;
  };

  const devices = () => snap?.devices ?? {};
  const activeName = () => snap?.active ?? null;
  const activeState = () => {
    const d = devices()[activeName() ?? ''];
    return d?.online && d.state ? d.state : null;
  };
  const remoteActive = () =>
    !!(activeName() && activeName() !== self && activeState());
  const positionOf = (st: NonNullable<DeviceView['state']>) => {
    let p = Number(st.position) || 0;
    if (!st.paused) p += (performance.now() - snapAt) / 1000;
    return st.duration ? Math.min(p, Math.max(0, st.duration - 1)) : p;
  };
  const listenHere = async () => {
    if (
      await act(
        { op: 'transfer', target: self },
        'Lecture transférée sur cet ordinateur',
      )
    )
      hidePopover();
  };

  // barre « Lecture sur … »
  const art = h('div', { class: 'art' });
  const title = h('div', { class: 'title' });
  const artist = h('div', { class: 'artist' });
  const whereText = h('span');
  const playBtn = h('button', {
    'class': 'round big',
    'aria-label': 'Lecture',
    'onclick': () => {
      const st = activeState();
      act({ op: 'control', action: st && !st.paused ? 'pause' : 'play' });
    },
  });
  const fill = h('div', { class: 'fill' });
  const track = h(
    'div',
    {
      'class': 'track',
      'role': 'slider',
      'aria-label': 'Position',
      'onclick': (ev) => {
        const st = activeState();
        if (!st?.duration) return;
        const r = track.getBoundingClientRect();
        const seconds = Math.round(
          (((ev as MouseEvent).clientX - r.left) / r.width) * st.duration,
        );
        act({ op: 'control', action: 'seek', seconds });
      },
    },
    fill,
  );
  const pos = h('span');
  const dur = h('span');
  const volChange = (ev: Event) =>
    act({
      op: 'control',
      action: 'volume',
      level: Number((ev.target as HTMLInputElement).value),
    });
  const volRange = h('input', {
    'type': 'range',
    'min': '0',
    'max': '100',
    'aria-label': 'Volume de l’appareil',
    'onchange': volChange,
  }) as HTMLInputElement;
  const vol = h('div', { class: 'vol' }, icon('volume', 20), volRange);
  const devBtn = h(
    'button',
    {
      'class': 'round devbtn',
      'aria-label': 'Appareils',
      'title': 'Appareils (Pear Connect)',
      'onclick': (ev) => {
        ev.stopPropagation();
        togglePopover();
      },
    },
    icon('devices', 22),
  );
  const bar = h(
    'div',
    {
      'class': 'bar',
      'role': 'region',
      'aria-label': 'Lecture sur un autre appareil',
    },
    h(
      'div',
      { class: 'now' },
      art,
      h(
        'div',
        { class: 'meta' },
        title,
        artist,
        h('div', { class: 'where' }, icon('speaker', 14), whereText),
      ),
    ),
    h(
      'div',
      { class: 'center' },
      h(
        'div',
        { class: 'controls' },
        h(
          'button',
          {
            'class': 'round',
            'aria-label': 'Précédent',
            'onclick': () => act({ op: 'control', action: 'previous' }),
          },
          icon('prev'),
        ),
        playBtn,
        h(
          'button',
          {
            'class': 'round',
            'aria-label': 'Suivant',
            'onclick': () => act({ op: 'control', action: 'next' }),
          },
          icon('next'),
        ),
      ),
      h('div', { class: 'progress' }, pos, track, dur),
    ),
    h(
      'div',
      { class: 'right' },
      vol,
      h(
        'button',
        {
          'class': 'round',
          'aria-label': 'File d’attente',
          'title': 'File d’attente de l’appareil qui joue',
          'onclick': (ev) => {
            ev.stopPropagation();
            toggleQueue();
          },
        },
        icon('queue', 22),
      ),
      devBtn,
      h('button', {
        class: 'here',
        text: 'Écouter ici',
        onclick: () => listenHere(),
      }),
    ),
  );

  // liste des appareils
  const list = h('div');
  const popSub = h('p', { class: 'sub' });
  const popVolRange = h('input', {
    'type': 'range',
    'min': '0',
    'max': '100',
    'aria-label': 'Volume',
    'onchange': volChange,
  }) as HTMLInputElement;
  const popVol = h('div', { class: 'popvol' }, icon('volume', 20), popVolRange);
  const pop = h(
    'div',
    {
      'class': 'pop',
      'role': 'dialog',
      'aria-label': 'Appareils',
      'onclick': (ev) => ev.stopPropagation(),
    },
    h('h2', { text: 'Appareils' }),
    popSub,
    list,
    popVol,
  );
  // file d'attente de l'appareil qui joue
  const qlist = h('div');
  const qpop = h(
    'div',
    {
      'class': 'pop qpop',
      'role': 'dialog',
      'aria-label': 'File d’attente',
      'onclick': (ev) => ev.stopPropagation(),
    },
    h('h2', { text: 'À suivre' }),
    qlist,
  );
  root.append(bar, pop, qpop, toastEl);

  const deviceStatus = (name: string, d: DeviceView) => {
    if (name === activeName() && d.online && d.state) {
      return `${d.state.paused ? 'En pause' : 'En lecture'} · ${d.state.title || ''}`;
    }
    if (name === self) return 'Cet ordinateur';
    return d.online ? 'Disponible' : 'Hors ligne';
  };

  function renderList() {
    list.replaceChildren();
    if (!snap) {
      list.append(
        h('p', {
          class: 'empty',
          text: `Pear Connect ${status || 'non connecté'}`,
        }),
      );
    }
    const entries = Object.entries(devices()).filter(
      ([, d]) => d.kind !== 'web',
    );
    entries.sort(([a], [b]) =>
      a === self ? -1 : b === self ? 1 : a.localeCompare(b),
    );
    for (const [name, d] of entries) {
      const isActive = name === activeName() && d.online && !!d.state;
      const btn = h(
        'button',
        {
          class: `dev${isActive ? ' active' : ''}${d.online || name === self ? '' : ' offline'}`,
          onclick: () => {
            if (isActive) return;
            if (name === self) {
              listenHere();
              return;
            }
            act(
              { op: 'transfer', target: name },
              `Lecture transférée sur ${name}`,
            ).then((ok) => ok && hidePopover());
          },
        },
        icon(KIND_ICON[d.kind] ?? 'speaker', 28),
        h(
          'div',
          {},
          h('div', {
            class: 'name',
            text: name === self ? `${name} (cet ordinateur)` : name,
          }),
          h('div', { class: 'state', text: deviceStatus(name, d) }),
        ),
      );
      if (isActive) btn.setAttribute('disabled', '');
      list.append(btn);
    }
    const st = activeState();
    popSub.textContent = st
      ? `Lecture sur ${activeName() === self ? 'cet ordinateur' : activeName()}`
      : snap
        ? 'Aucune lecture en cours'
        : '';
    const showVol = remoteActive() && st?.volume != null;
    popVol.style.display = showVol ? 'flex' : 'none';
    if (showVol && root.activeElement !== popVolRange)
      popVolRange.value = String(st.volume);
  }

  function renderQueue() {
    qlist.replaceChildren();
    const st = activeState();
    const rows = st?.queue ?? [];
    if (!rows.length) {
      qlist.append(h('p', { class: 'empty', text: 'File vide (ou inconnue)' }));
      return;
    }
    // rang dans « à suivre » (0 = juste après le titre en cours) : départage les doublons
    const edit = (
      name: IconName,
      label: string,
      disabled: boolean,
      action: UiAction,
    ) => {
      const b = h(
        'button',
        {
          'class': 'qctl',
          'aria-label': label,
          'title': label,
          'onclick': () => act(action),
        },
        icon(name, 18),
      );
      if (disabled) b.setAttribute('disabled', '');
      return b;
    };
    rows.forEach((row, i) => {
      const qart = h('div', { class: 'qart' });
      if (VIDEO_ID_RE.test(row.video_id)) {
        qart.style.backgroundImage = `url("https://i.ytimg.com/vi/${row.video_id}/hqdefault.jpg")`;
      }
      const label = row.title || row.video_id;
      const videoId = row.video_id;
      qlist.append(
        h(
          'div',
          { class: 'qrow' },
          h(
            'button',
            {
              'class': 'qmain',
              'aria-label': `Jouer ${label}`,
              'onclick': () => {
                act({ op: 'control', action: 'jump', video_id: videoId });
              },
            },
            qart,
            h(
              'div',
              { style: 'min-width:0' },
              h('div', { class: 'name', text: label }),
              h('div', { class: 'state', text: row.artist }),
            ),
          ),
          edit('up', 'Monter', i === 0, {
            op: 'control',
            action: 'move',
            video_id: videoId,
            index: i,
            to: i - 1,
          }),
          edit('down', 'Descendre', i === rows.length - 1, {
            op: 'control',
            action: 'move',
            video_id: videoId,
            index: i,
            to: i + 1,
          }),
          edit('close', 'Retirer de la file', false, {
            op: 'control',
            action: 'remove',
            video_id: videoId,
            index: i,
          }),
        ),
      );
    });
  }
  function toggleQueue() {
    if (qpop.classList.contains('show')) return qpop.classList.remove('show');
    pop.classList.remove('show');
    renderQueue();
    qpop.classList.add('show');
  }
  const hidePopover = () => {
    pop.classList.remove('show');
    qpop.classList.remove('show');
  };
  function togglePopover() {
    if (pop.classList.contains('show')) return hidePopover();
    qpop.classList.remove('show');
    renderList();
    pop.classList.add('show');
    send({ op: 'hello' });
  }
  const onDocClick = () => hidePopover();
  const onKey = (ev: KeyboardEvent) => ev.key === 'Escape' && hidePopover();
  document.addEventListener('click', onDocClick);
  document.addEventListener('keydown', onKey);

  // bouton dans la barre du lecteur de YouTube Music
  const playerBarButton = h(
    'button',
    {
      'id': 'pear-connect-devices',
      'title': 'Appareils (Pear Connect)',
      'aria-label': 'Appareils',
      'onclick': (ev) => {
        ev.stopPropagation();
        togglePopover();
      },
    },
    icon('devices'),
  );
  Object.assign(playerBarButton.style, {
    width: '40px',
    height: '40px',
    display: 'grid',
    placeItems: 'center',
    background: 'none',
    border: '0',
    borderRadius: '50%',
    cursor: 'pointer',
    color: 'var(--yt-spec-text-secondary, #aaa)',
    padding: '0',
    margin: '0 4px',
  });

  function attach() {
    if (!host.isConnected) document.body.append(host);
    // barre du lecteur classique, ou nouveau mini-lecteur de YouTube Music (déploiement 2026)
    const playerBar =
      document.querySelector<HTMLElement>('ytmusic-player-bar') ??
      document.querySelector<HTMLElement>('ytmusic-miniplayer');
    const slot =
      playerBar?.querySelector('.right-controls-buttons') ??
      playerBar?.querySelector('.ytMusicMiniPlayerRightSection');
    if (slot && playerBarButton.parentElement !== slot)
      slot.prepend(playerBarButton);
    host.style.setProperty(
      '--pc-height',
      playerBar?.offsetHeight ? `${playerBar.offsetHeight}px` : '72px',
    );
  }

  function tick() {
    if (!remoteActive()) return;
    const st = activeState()!;
    const p = positionOf(st);
    pos.textContent = fmt(p);
    dur.textContent = fmt(st.duration);
    fill.style.width = st.duration
      ? `${Math.min(100, (p / st.duration) * 100)}%`
      : '0';
  }

  function render() {
    attach();
    const remote = remoteActive();
    bar.classList.toggle('show', remote);
    playerBarButton.style.color = remote
      ? ACCENT
      : 'var(--yt-spec-text-secondary, #aaa)';
    if (remote) {
      const st = activeState()!;
      art.style.backgroundImage = VIDEO_ID_RE.test(st.video_id)
        ? `url("https://i.ytimg.com/vi/${st.video_id}/hqdefault.jpg")`
        : '';
      title.textContent = st.title || st.video_id;
      artist.textContent = st.artist || '';
      whereText.textContent = `Lecture sur ${activeName()}`;
      playBtn.replaceChildren(icon(st.paused ? 'play' : 'pause'));
      playBtn.setAttribute('aria-label', st.paused ? 'Lecture' : 'Pause');
      const hasVol = st.volume != null;
      vol.style.display = hasVol ? 'flex' : 'none';
      if (hasVol && root.activeElement !== volRange)
        volRange.value = String(st.volume);
      tick();
    }
    if (pop.classList.contains('show')) renderList();
    if (qpop.classList.contains('show')) {
      if (remoteActive()) renderQueue();
      else qpop.classList.remove('show');
    }
  }

  const timers = [
    window.setInterval(tick, 500),
    window.setInterval(attach, 1000),
  ];

  return {
    update(data: { snapshot: Snapshot | null; self: string; status: string }) {
      snap = data.snapshot;
      self = data.self;
      status = data.status;
      snapAt = performance.now();
      render();
    },
    toast,
    destroy() {
      timers.forEach((t) => clearInterval(t));
      document.removeEventListener('click', onDocClick);
      document.removeEventListener('keydown', onKey);
      playerBarButton.remove();
      host.remove();
    },
  };
}

export const renderer = createRenderer<Props, PearConnectConfig>({
  timers: [],

  start(ctx) {
    this.ctx = ctx;
    const ui = createUi(
      (action) =>
        ctx.ipc.invoke('pear-connect:action', action) as Promise<ActionResult>,
    );
    ctx.ipc.on(
      'pear-connect:snapshot',
      (data: Parameters<typeof ui.update>[0]) => ui.update(data),
    );
    ctx.ipc.on('pear-connect:command', (cmd: Command) => {
      this.runCommand(cmd).catch((e: unknown) => {
        console.warn('[Pear Connect] commande en échec', e);
        this.diag(`commande ${cmd.type} : ${(e as Error).message}`);
        ui.toast(`Pear Connect : ${(e as Error).message}`);
      });
    });
    this.teardown = () => {
      ctx.ipc.removeAllListeners('pear-connect:snapshot');
      ctx.ipc.removeAllListeners('pear-connect:command');
      ui.destroy();
    };
    ctx.ipc.send('pear-connect:ready');
    // indépendant de onPlayerApiReady : le lecteur peut exister avant le plugin, ou après
    this.timers.push(window.setInterval(() => this.report(), 1000));
  },

  onPlayerApiReady(playerApi) {
    this.playerApi = playerApi;
    this.report();
  },

  stop() {
    this.timers.forEach((t) => clearInterval(t));
    this.timers = [];
    this.teardown?.();
  },

  /** Lecture locale → processus principal (qui décide quoi publier). */
  report() {
    if (!this.ctx) return;
    const api =
      this.playerApi ??
      (document.querySelector(
        '#movie_player',
      ) as unknown as MusicPlayer | null);
    if (!api?.getVideoData) return;
    try {
      const data = api.getVideoData() as
        | { video_id?: string; title?: string; author?: string }
        | undefined;
      let rows: ReturnType<typeof upcomingRows> = [];
      try {
        rows = upcomingRows(parseQueue(queueElement()?.queue.getItems()));
      } catch (e) {
        this.diag(`lecture de la file : ${(e as Error).message}`);
      }
      const state = api.getPlayerState(); // -1 non démarré, 0 fini, 1 lecture, 2 pause, 3 chargement
      let volume: number | null = null;
      try {
        volume = api.isMuted() ? 0 : Math.round(api.getVolume());
      } catch {
        volume = null;
      }
      const report: LocalReport = {
        videoId: data?.video_id ?? '',
        playlistId: new URL(location.href).searchParams.get('list') ?? '',
        title: data?.title ?? '',
        artist: data?.author ?? '',
        position: api.getCurrentTime() || 0,
        duration: api.getDuration() || 0,
        paused: state !== 1 && state !== 3,
        volume,
        upcoming: rows.map((r) => r.video_id),
        queue: rows,
      };
      this.ctx.ipc.send('pear-connect:local', report);
    } catch (e) {
      this.diag(`lecture de l'état : ${(e as Error).message}`);
    }
  },

  diag(message) {
    if (this.lastDiag === message) return;
    this.lastDiag = message;
    console.warn('[Pear Connect]', message);
    this.ctx?.ipc.send('pear-connect:diag', message);
  },

  async runCommand(cmd) {
    const receivedAt = Date.now();
    const api = this.playerApi;
    if (cmd.type === 'resume') return this.resume(cmd, receivedAt);
    if (!api) throw new Error('lecteur pas encore prêt');
    switch (cmd.type) {
      case 'play':
        api.playVideo();
        return;
      case 'pause':
        api.pauseVideo();
        return;
      case 'next':
        api.nextVideo();
        return;
      case 'previous':
        api.previousVideo();
        return;
      case 'seek':
        api.seekTo(Math.max(0, Number(cmd.seconds) || 0));
        return;
      case 'volume': {
        const level = Math.min(100, Math.max(0, Number(cmd.level) || 0));
        if (level > 0 && api.isMuted()) api.unMute();
        api.setVolume(level);
        return;
      }
      default:
        break;
    }
    const queue = queueElement();
    const entries = parseQueue(queue?.queue.getItems());
    if (cmd.type === 'jump') {
      const index = indexOfVideo(entries, cmd.video_id);
      if (index === null || !queue)
        throw new Error("ce titre n'est plus dans la file");
      queue.dispatch({ type: 'SET_INDEX', payload: index });
      return;
    }
    if (cmd.type === 'remove' || cmd.type === 'move') {
      // édition à distance de « à suivre » (télécommande, autre PC, téléphone)
      const from = locateUpcoming(entries, cmd.video_id, cmd.index);
      if (from === null || !queue)
        throw new Error("ce titre n'est plus dans la file");
      if (cmd.type === 'remove') {
        queue.dispatch({ type: 'REMOVE_ITEM', payload: from });
        return;
      }
      const to = upcomingTarget(entries, cmd.to);
      if (to !== from) {
        queue.dispatch({
          type: 'MOVE_ITEM',
          payload: { fromIndex: from, toIndex: to },
        });
      }
    }
  },

  async resume(cmd, receivedAt) {
    const app = document.querySelector<MusicPlayerAppElement>('ytmusic-app');
    if (!app) throw new Error('YouTube Music pas encore chargé');
    // Pear tout juste lancé, rien encore joué : le lecteur n'existe pas avant la navigation
    const player = () =>
      this.playerApi ??
      (document.querySelector(
        '#movie_player',
      ) as unknown as MusicPlayer | null) ??
      undefined;
    const videoOf = (p?: MusicPlayer) =>
      (p?.getVideoData?.() as { video_id?: string } | undefined)?.video_id;
    const st = cmd.state;
    const target = () => {
      let p = Number(st.position) || 0;
      if (!st.paused) p += (Date.now() - receivedAt) / 1000;
      return st.duration ? Math.min(p, Math.max(0, st.duration - 2)) : p;
    };
    const endpoint = watchEndpoint({ ...st, position: target() });
    const currentList = new URL(location.href).searchParams.get('list') ?? '';
    // même titre ET même contexte : un simple recalage suffit (sinon on rouvre le mix)
    if (
      videoOf(player()) !== st.video_id ||
      (endpoint.playlistId ?? '') !== (endpoint.playlistId ? currentList : '')
    ) {
      // navigation interne : YouTube Music ouvre le titre dans son mix, comme un clic
      app.dispatchEvent(
        new CustomEvent('yt-navigate', {
          bubbles: true,
          composed: true,
          detail: { endpoint: { watchEndpoint: endpoint } },
        }),
      );
      const loaded = await wait(() => {
        const p = player();
        return videoOf(p) === st.video_id && (p?.getDuration() ?? 0) > 0;
      }, 20_000);
      if (!loaded) throw new Error("le titre ne s'est pas chargé");
    }
    const api = player()!;
    if (Math.abs(api.getCurrentTime() - target()) > 2.5) api.seekTo(target());
    if (st.paused) api.pauseVideo();
    else api.playVideo();
    // Même file que la source : YouTube Music régénère un mix à chaque ouverture, la suite
    // différerait. On remplace donc les titres à venir par ceux de la source ; le mix reste le
    // contexte de lecture et continue de se générer après eux.
    const upcoming = (st.upcoming ?? []).slice(0, QUEUE_LIMIT);
    if (cmd.with_queue !== false && upcoming.length) {
      // attendre que YouTube Music ait chargé la file du mix, sinon elle écraserait la nôtre
      await wait(
        () => (queueElement()?.queue.getItems().length ?? 0) > 1,
        8_000,
      );
      for (
        let attempt = 0;
        attempt < 2 && !this.sameUpcoming(upcoming);
        attempt++
      ) {
        await this.replaceUpcoming(upcoming);
        await wait(() => this.sameUpcoming(upcoming), 3_000);
      }
    }
  },

  sameUpcoming(ids) {
    const rows = upcomingRows(parseQueue(queueElement()?.queue.getItems()));
    return ids.every((id, i) => rows[i]?.video_id === id);
  },

  async replaceUpcoming(ids) {
    const queue = queueElement();
    const app = document.querySelector<MusicPlayerAppElement>('ytmusic-app');
    const store = queue?.queue.store.store;
    if (!queue || !app || !store) return;
    const valid = ids.filter((id) => VIDEO_ID_RE.test(id));
    if (!valid.length) return;
    await new Promise((r) => setTimeout(r, 500));
    const entries = parseQueue(queue.queue.getItems());
    const current = entries.find((e) => e.selected)?.index ?? -1;
    if (current < 0) return;
    const total = queue.queue.getItems().length;
    for (let i = total - 1; i > current; i--)
      queue.dispatch({ type: 'REMOVE_ITEM', payload: i });
    const state = store.getState() as unknown as {
      queue: {
        queueContextParams: unknown;
        items: unknown[];
        nextQueueItemId: unknown;
      };
    };
    const result = await app.networkManager.fetch('/music/get_queue', {
      queueContextParams: state.queue.queueContextParams,
      queueInsertPosition: 'INSERT_AT_END',
      videoIds: valid,
    });
    const datas = (result as { queueDatas?: { content?: unknown }[] } | null)
      ?.queueDatas;
    if (!Array.isArray(datas)) return;
    const after = store.getState() as unknown as typeof state;
    queue.dispatch({
      type: 'ADD_ITEMS',
      payload: {
        nextQueueItemId: after.queue.nextQueueItemId,
        index: after.queue.items.length,
        items: datas.map((d) => d?.content).filter(Boolean),
        shuffleEnabled: false,
        shouldAssignIds: true,
      },
    });
  },
});
