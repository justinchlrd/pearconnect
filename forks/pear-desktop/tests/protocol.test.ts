/*
 * Tests des fonctions pures du plugin (protocol.ts), sans dépendance :
 *   node --experimental-strip-types --test forks/pear-desktop/tests/
 * (Node ≥ 22.6 ; le drapeau n'est plus nécessaire à partir de Node 23.6.)
 */
import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import {
  QUEUE_LIMIT,
  cleanPlaylistId,
  extrapolate,
  indexOfVideo,
  locateUpcoming,
  parseQueue,
  stateChanged,
  upcomingRows,
  upcomingTarget,
  watchEndpoint,
  type PlaybackState,
} from '../plugin-src/protocol.ts';

const ID = (c: string) => c.repeat(11);

/** Élément de file au format du store de YouTube Music (direct ou enveloppé). */
const item = (
  video: string,
  opts: { selected?: boolean; wrapped?: boolean; title?: string; artist?: string } = {},
) => {
  const r = {
    videoId: video,
    selected: opts.selected ?? false,
    title: { runs: [{ text: opts.title ?? `T-${video}` }] },
    shortBylineText: opts.artist ? { runs: [{ text: opts.artist }] } : undefined,
    longBylineText: { runs: [{ text: 'Artiste long • Album • 2026' }] },
  };
  return opts.wrapped
    ? { playlistPanelVideoWrapperRenderer: { primaryRenderer: { playlistPanelVideoRenderer: r } } }
    : { playlistPanelVideoRenderer: r };
};

const state = (over: Partial<PlaybackState> = {}): PlaybackState => ({
  video_id: ID('a'),
  position: 10,
  paused: false,
  duration: 200,
  title: 'T',
  artist: 'A',
  playlist_id: 'RDAMVMabc',
  upcoming: [ID('b')],
  queue: [],
  volume: 50,
  ...over,
});

describe('cleanPlaylistId', () => {
  it('garde un identifiant sain et rejette le reste', () => {
    assert.equal(cleanPlaylistId('RDAMVMabc_-123'), 'RDAMVMabc_-123');
    assert.equal(cleanPlaylistId('x'), '');
    assert.equal(cleanPlaylistId('RD AMV'), '');
    assert.equal(cleanPlaylistId('a'.repeat(129)), '');
    assert.equal(cleanPlaylistId(42), '');
    assert.equal(cleanPlaylistId(undefined), '');
  });
});

describe('extrapolate', () => {
  it('avance en lecture, pas en pause, et reste sous la durée', () => {
    assert.equal(extrapolate({ position: 10, paused: false, duration: 200 }, 5), 15);
    assert.equal(extrapolate({ position: 10, paused: true, duration: 200 }, 5), 10);
    assert.equal(extrapolate({ position: 10, paused: false, duration: 20 }, 50), 19);
    assert.equal(extrapolate({ position: 10, paused: false, duration: 0 }, 50), 60);
    assert.equal(extrapolate({ position: 10, paused: false, duration: 200 }, -5), 10);
  });
});

describe('stateChanged', () => {
  it('ne republie pas une position qui avance normalement', () => {
    const prev = state();
    assert.equal(stateChanged(prev, state({ position: 15 }), 5), false);
    assert.equal(stateChanged(prev, state({ position: 25 }), 5), true, 'saut de position');
    assert.equal(stateChanged(prev, state({ position: 10 }), 5), true, 'retour en arrière > dérive');
  });
  it('détecte titre, pause, volume, mix et file', () => {
    const prev = state();
    assert.equal(stateChanged(prev, state({ video_id: ID('z') }), 0), true);
    assert.equal(stateChanged(prev, state({ paused: true }), 0), true);
    assert.equal(stateChanged(prev, state({ volume: 51 }), 0), true);
    assert.equal(stateChanged(prev, state({ playlist_id: '' }), 0), true);
    assert.equal(stateChanged(prev, state({ upcoming: [ID('c')] }), 0), true);
    assert.equal(stateChanged(prev, state(), 0), false);
  });
  it('gère l’absence d’état', () => {
    assert.equal(stateChanged(null, null, 0), false);
    assert.equal(stateChanged(null, state(), 0), true);
    assert.equal(stateChanged(state(), null, 0), true);
  });
});

describe('watchEndpoint', () => {
  it('construit le point d’entrée avec mix et position entière', () => {
    assert.deepEqual(watchEndpoint({ video_id: ID('a'), playlist_id: 'RDx', position: 42.9 }), {
      videoId: ID('a'),
      playlistId: 'RDx',
      startTimeSeconds: 42,
    });
  });
  it('omet le mix invalide et la position < 1 s', () => {
    assert.deepEqual(watchEndpoint({ video_id: ID('a'), playlist_id: 'bad id', position: 0.5 }), {
      videoId: ID('a'),
    });
    assert.deepEqual(watchEndpoint({ video_id: ID('a') }), { videoId: ID('a') });
  });
  it('refuse un identifiant de titre invalide', () => {
    assert.throws(() => watchEndpoint({ video_id: 'x; rm -rf' }), /invalide/);
  });
});

describe('parseQueue / upcomingRows', () => {
  const items = [
    item(ID('a')),
    item(ID('b'), { selected: true, artist: 'Seeko' }),
    { somethingElse: {} },
    item(ID('c'), { wrapped: true }),
    item(ID('d')),
  ];
  it('lit les éléments directs et enveloppés en gardant leur index d’origine', () => {
    const entries = parseQueue(items);
    assert.deepEqual(
      entries.map((e) => [e.index, e.video_id, e.selected]),
      [
        [0, ID('a'), false],
        [1, ID('b'), true],
        [3, ID('c'), false],
        [4, ID('d'), false],
      ],
    );
    assert.equal(entries[1].artist, 'Seeko', 'shortBylineText prioritaire');
    assert.equal(entries[2].artist, 'Artiste long', 'sinon premier segment du longBylineText');
    assert.equal(entries[2].title, `T-${ID('c')}`);
  });
  it('« à suivre » = titres après le titre en cours, sans index', () => {
    assert.deepEqual(upcomingRows(parseQueue(items)), [
      { video_id: ID('c'), title: `T-${ID('c')}`, artist: 'Artiste long' },
      { video_id: ID('d'), title: `T-${ID('d')}`, artist: 'Artiste long' },
    ]);
    assert.deepEqual(upcomingRows(parseQueue([item(ID('a'))])), [], 'rien de sélectionné');
    assert.deepEqual(upcomingRows(parseQueue(undefined)), []);
  });
  it('borne « à suivre » à QUEUE_LIMIT', () => {
    const many = [item(ID('a'), { selected: true })];
    for (let i = 0; i < QUEUE_LIMIT + 10; i++) many.push(item(ID('b')));
    assert.equal(upcomingRows(parseQueue(many)).length, QUEUE_LIMIT);
  });
});

describe('indexOfVideo', () => {
  const entries = parseQueue([item(ID('b')), item(ID('a'), { selected: true }), item(ID('c')), item(ID('b'))]);
  it('préfère l’occurrence après le titre en cours', () => {
    assert.equal(indexOfVideo(entries, ID('b')), 3);
    assert.equal(indexOfVideo(entries, ID('c')), 2);
  });
  it('retombe sur une occurrence avant, ou null', () => {
    assert.equal(indexOfVideo(parseQueue([item(ID('b')), item(ID('a'), { selected: true })]), ID('b')), 0);
    assert.equal(indexOfVideo(entries, ID('z')), null);
  });
});

describe('locateUpcoming', () => {
  // file : b [a] c b d   → « à suivre » : c(0) b(1) d(2)
  const entries = parseQueue([item(ID('b')), item(ID('a'), { selected: true }), item(ID('c')), item(ID('b')), item(ID('d'))]);
  it('prend le rang indiqué quand il désigne bien ce titre', () => {
    assert.equal(locateUpcoming(entries, ID('b'), 1), 3);
    assert.equal(locateUpcoming(entries, ID('d'), 2), 4);
  });
  it('ignore un rang périmé et prend la première occurrence après le titre en cours', () => {
    assert.equal(locateUpcoming(entries, ID('b'), 0), 3, 'rang 0 est « c », pas « b »');
    assert.equal(locateUpcoming(entries, ID('c'), 7), 2);
    assert.equal(locateUpcoming(entries, ID('c')), 2);
    assert.equal(locateUpcoming(entries, ID('c'), null), 2);
  });
  it('ne renvoie jamais le titre en cours ni un titre déjà passé', () => {
    assert.equal(locateUpcoming(entries, ID('a')), null);
    assert.equal(locateUpcoming(entries, ID('a'), -2), null);
    assert.equal(locateUpcoming(parseQueue([item(ID('b')), item(ID('a'), { selected: true })]), ID('b')), null);
  });
});

describe('upcomingTarget', () => {
  const entries = parseQueue([item(ID('b')), item(ID('a'), { selected: true }), item(ID('c')), item(ID('b')), item(ID('d'))]);
  it('convertit un rang « à suivre » en index de file', () => {
    assert.equal(upcomingTarget(entries, 0), 2);
    assert.equal(upcomingTarget(entries, 2), 4);
  });
  it('borne aux limites de la file', () => {
    assert.equal(upcomingTarget(entries, 99), 4);
    assert.equal(upcomingTarget(entries, -3), 2);
    assert.equal(upcomingTarget(entries, Number.NaN), 2);
  });
});
