/*
 * Protocole du serveur Connect (pearsync, server/connect_server.py) et fonctions pures
 * partagées entre le processus principal et la page.
 */

export const VIDEO_ID_RE = /^[A-Za-z0-9_-]{11}$/;
const PLAYLIST_ID_RE = /^[A-Za-z0-9_-]{2,128}$/;
export const QUEUE_LIMIT = 50;

export interface QueueRow {
  video_id: string;
  title: string;
  artist: string;
}

/** État de lecture publié par un appareil (positions en secondes). */
export interface PlaybackState {
  video_id: string;
  position: number;
  paused: boolean;
  duration: number;
  title: string;
  artist: string;
  playlist_id: string;
  upcoming: string[];
  queue: QueueRow[];
  volume: number | null;
  ts?: number;
}

export interface DeviceView {
  kind: string;
  online: boolean;
  seen: number | null;
  state: (Omit<PlaybackState, 'upcoming'> & { upcoming: number }) | null;
}

export interface Snapshot {
  version: number;
  now: number;
  active: string | null;
  session: { device: string; state: DeviceView['state'] } | null;
  devices: Record<string, DeviceView>;
}

export type Command =
  | { type: 'pause' | 'play' | 'next' | 'previous' }
  | { type: 'seek'; seconds: number }
  | { type: 'volume'; level: number }
  | { type: 'jump'; video_id: string }
  | { type: 'enqueue'; video_id: string; next: boolean }
  | {
      type: 'resume';
      state: Partial<PlaybackState> & { video_id: string };
      with_queue?: boolean;
    };

/** Message de la page vers le processus principal. */
export type UiAction =
  | { op: 'hello' }
  | { op: 'transfer'; target: string }
  | {
      op: 'control';
      action: string;
      seconds?: number;
      level?: number;
      video_id?: string;
      next?: boolean;
    };

export interface ActionResult {
  ok: boolean;
  error?: string;
}

/** Ce que la page sait de la lecture locale (file avec titres, volume). */
export interface LocalExtras {
  queue: QueueRow[];
  upcoming: string[];
  volume: number | null;
}

export const cleanPlaylistId = (value: unknown): string =>
  typeof value === 'string' && PLAYLIST_ID_RE.test(value) ? value : '';

/** Position d'un état « en lecture » `elapsed` secondes après sa capture. */
export const extrapolate = (
  state: { position: number; paused: boolean; duration: number },
  elapsed: number,
): number => {
  let pos = state.position + (state.paused ? 0 : Math.max(0, elapsed));
  if (state.duration > 0) pos = Math.min(pos, Math.max(0, state.duration - 1));
  return pos;
};

/**
 * L'état `next` diffère-t-il de ce que le serveur déduit déjà de `prev`, publié il y a
 * `elapsed` secondes ? (inutile de republier une position qui avance normalement)
 */
export const stateChanged = (
  prev: PlaybackState | null,
  next: PlaybackState | null,
  elapsed: number,
  drift = 3,
): boolean => {
  if (!prev || !next) return prev !== next;
  if (
    prev.video_id !== next.video_id ||
    prev.paused !== next.paused ||
    prev.volume !== next.volume ||
    prev.playlist_id !== next.playlist_id ||
    prev.upcoming.join(',') !== next.upcoming.join(',')
  ) {
    return true;
  }
  return Math.abs(extrapolate(prev, elapsed) - next.position) > drift;
};

/** Point d'entrée « watch » de YouTube Music : titre, contexte (mix…) et position. */
export const watchEndpoint = (
  state: Partial<PlaybackState> & { video_id: string },
): { videoId: string; playlistId?: string; startTimeSeconds?: number } => {
  if (!VIDEO_ID_RE.test(state.video_id)) {
    throw new Error('identifiant de titre invalide');
  }
  const endpoint: {
    videoId: string;
    playlistId?: string;
    startTimeSeconds?: number;
  } = { videoId: state.video_id };
  const list = cleanPlaylistId(state.playlist_id);
  if (list) endpoint.playlistId = list;
  const start = Math.floor(state.position ?? 0);
  if (start >= 1) endpoint.startTimeSeconds = start;
  return endpoint;
};

type Runs = { runs?: { text?: string }[]; simpleText?: string } | undefined;
const runsText = (node: Runs): string =>
  node?.simpleText ?? (node?.runs ?? []).map((r) => r.text ?? '').join('');

interface PanelRenderer {
  videoId?: string;
  selected?: boolean;
  title?: Runs;
  shortBylineText?: Runs;
  longBylineText?: Runs;
}

const panelRenderer = (item: unknown): PanelRenderer | undefined => {
  const it = item as {
    playlistPanelVideoRenderer?: PanelRenderer;
    playlistPanelVideoWrapperRenderer?: {
      primaryRenderer?: { playlistPanelVideoRenderer?: PanelRenderer };
    };
  };
  return (
    it?.playlistPanelVideoRenderer ??
    it?.playlistPanelVideoWrapperRenderer?.primaryRenderer
      ?.playlistPanelVideoRenderer
  );
};

export interface QueueEntry extends QueueRow {
  index: number;
  selected: boolean;
}

/** Éléments de la file de YouTube Music (store du lecteur) → lignes simples. */
export const parseQueue = (items: unknown[] | undefined): QueueEntry[] => {
  const out: QueueEntry[] = [];
  (items ?? []).forEach((item, index) => {
    const r = panelRenderer(item);
    if (!r?.videoId) return;
    const artist =
      runsText(r.shortBylineText) ||
      runsText(r.longBylineText).split(' • ')[0] ||
      '';
    out.push({
      index,
      video_id: r.videoId,
      title: runsText(r.title),
      artist,
      selected: !!r.selected,
    });
  });
  return out;
};

/** Titres qui suivent le titre en cours (au plus QUEUE_LIMIT). */
export const upcomingRows = (entries: QueueEntry[]): QueueRow[] => {
  const current = entries.findIndex((e) => e.selected);
  if (current < 0) return [];
  return entries.slice(current + 1, current + 1 + QUEUE_LIMIT).map((e) => ({
    video_id: e.video_id,
    title: e.title,
    artist: e.artist,
  }));
};

/** Index dans la file d'un titre, de préférence après le titre en cours. */
export const indexOfVideo = (
  entries: QueueEntry[],
  videoId: string,
): number | null => {
  const current = entries.find((e) => e.selected)?.index ?? -1;
  const after = entries.find(
    (e) => e.index > current && e.video_id === videoId,
  );
  const any = after ?? entries.find((e) => e.video_id === videoId);
  return any ? any.index : null;
};
