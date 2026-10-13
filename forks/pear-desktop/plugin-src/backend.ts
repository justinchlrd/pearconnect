/*
 * Pear Connect, processus principal : relie ce Pear Desktop au serveur Connect.
 *  - publie l'état de lecture (titre, position, mix, file, volume) ;
 *  - relève et exécute les commandes (long-poll) ;
 *  - relaie le flux temps réel du serveur à la page (barre « Lecture sur … »).
 */
import { appendFileSync, renameSync, statSync } from 'node:fs';
import { join } from 'node:path';

import { app } from 'electron';

import { getSongControls } from '@/providers/song-controls';
import { registerCallback, type SongInfo } from '@/providers/song-info';
import { createBackend } from '@/utils';

import { ConnectClient, ConnectError } from './client';
import {
  type ActionResult,
  type Command,
  type PlaybackState,
  type Snapshot,
  type UiAction,
  type UpdateInfo,
  cleanPlaylistId,
  pluginUpdate,
  stateChanged,
} from './protocol';
import { PLUGIN_VERSION } from './version';

import type { PearConnectConfig } from './config';
import type { BackendContext } from '@/types/contexts';

/** État de lecture lu dans la page (positions en secondes). */
export interface LocalReport {
  videoId: string;
  /** Contexte de lecture lu dans l'adresse de la page (…&list=RD…) */
  playlistId: string;
  title: string;
  artist: string;
  position: number;
  duration: number;
  paused: boolean;
  volume: number | null;
  upcoming: string[];
  queue: PlaybackState['queue'];
}

const PUBLISH_EVERY = 2_000;
const HEARTBEAT = 15_000;
const LONG_POLL = 20;
const UPDATE_FIRST_CHECK = 15_000;
const UPDATE_EVERY = 6 * 3600_000;
const LOG = '[Pear Connect]';

/** Journal %APPDATA%\\YouTube Music\\pear-connect.log (1 Mo, une rotation). */
const journal = (message: string) => {
  try {
    const file = join(app.getPath('userData'), 'pear-connect.log');
    try {
      if (statSync(file).size > 1_000_000) renameSync(file, `${file}.1`);
    } catch {
      /* pas encore de journal */
    }
    appendFileSync(file, `${new Date().toISOString()} ${message}\n`);
  } catch {
    /* journal facultatif */
  }
};

const sleep = (ms: number, signal: AbortSignal) =>
  new Promise<void>((resolve) => {
    const t = setTimeout(resolve, ms);
    signal.addEventListener('abort', () => {
      clearTimeout(t);
      resolve();
    });
  });

type Props = {
  ctx?: BackendContext<PearConnectConfig>;
  client?: ConnectClient;
  config?: PearConnectConfig;
  abort?: AbortController;
  song?: SongInfo;
  local?: LocalReport;
  localAt: number;
  published: PlaybackState | null | undefined;
  publishedAt: number;
  snapshot?: Snapshot;
  status: string;
  callbackRegistered: boolean;
  /** Mise à jour du plugin disponible (null : à jour ; undefined : pas encore vérifié). */
  update?: UpdateInfo | null;
  updateCheckedAt: number;
  updateError?: string;
  run(config: PearConnectConfig): void;
  halt(): void;
  currentState(): PlaybackState | null;
  publishLoop(signal: AbortSignal): Promise<void>;
  commandLoop(signal: AbortSignal): Promise<void>;
  eventsLoop(signal: AbortSignal): Promise<void>;
  updateLoop(signal: AbortSignal): Promise<void>;
  checkUpdate(): Promise<UpdateInfo | null>;
  execute(cmd: Command): void;
  action(action: UiAction): Promise<ActionResult>;
  pushSnapshot(): void;
  setStatus(status: string): void;
};

export const backend = createBackend<Props, PearConnectConfig>({
  localAt: 0,
  published: undefined,
  publishedAt: 0,
  status: 'désactivé',
  callbackRegistered: false,
  updateCheckedAt: 0,

  async start(ctx) {
    this.ctx = ctx;
    if (!this.callbackRegistered) {
      // pas de désinscription possible : on filtre selon this.abort
      registerCallback((info) => {
        this.song = info;
      });
      this.callbackRegistered = true;
    }
    ctx.ipc.on('pear-connect:local', (report: LocalReport) => {
      this.local = report;
      this.localAt = Date.now();
    });
    ctx.ipc.on('pear-connect:ready', () => this.pushSnapshot());
    ctx.ipc.on('pear-connect:diag', (message: string) =>
      journal(`page : ${message}`),
    );
    // position de lecture tenue à jour par Pear (repli si la page ne rapporte rien)
    ctx.ipc.on('peard:player-api-loaded', () =>
      ctx.ipc.send('peard:setup-time-changed-listener'),
    );
    ctx.ipc.handle('pear-connect:action', (action: UiAction) =>
      this.action(action),
    );
    this.run(await ctx.getConfig());
  },

  stop() {
    this.halt();
    this.ctx?.ipc.removeHandler('pear-connect:action');
  },

  onConfigChange(config) {
    this.halt();
    this.run(config);
  },

  run(config) {
    this.config = config;
    if (!config.server || !config.token || !config.device) {
      this.setStatus('à configurer (menu Plugins › Pear Connect)');
      return;
    }
    try {
      this.client = new ConnectClient(config);
    } catch (e) {
      this.setStatus(`configuration invalide : ${(e as Error).message}`);
      return;
    }
    const abort = new AbortController();
    this.abort = abort;
    this.published = undefined;
    this.publishLoop(abort.signal);
    this.commandLoop(abort.signal);
    this.eventsLoop(abort.signal);
    this.updateLoop(abort.signal);
    console.log(LOG, `appareil « ${config.device} » sur ${config.server}`);
    journal(
      `démarrage : plugin v${PLUGIN_VERSION}, appareil « ${config.device} » sur ${config.server}`,
    );
  },

  async updateLoop(signal) {
    await sleep(UPDATE_FIRST_CHECK, signal);
    while (!signal.aborted) {
      await this.checkUpdate();
      await sleep(UPDATE_EVERY, signal);
    }
  },

  /** Compare la version du plugin à `versions.json` du serveur ; signale la page et le menu. */
  async checkUpdate() {
    const client = this.client;
    if (!client) return null;
    try {
      const info = await client.versions();
      const update = pluginUpdate(info, PLUGIN_VERSION, process.platform);
      const changed = update?.latest !== this.update?.latest;
      this.update = update;
      this.updateError = undefined;
      this.updateCheckedAt = Date.now();
      if (changed) {
        journal(
          update
            ? `mise à jour disponible : v${update.latest} (installée : v${PLUGIN_VERSION})`
            : `plugin v${PLUGIN_VERSION} à jour`,
        );
        this.pushSnapshot();
      }
      return update;
    } catch (e) {
      this.updateError = (e as Error).message;
      return null;
    }
  },

  halt() {
    this.abort?.abort();
    this.abort = undefined;
    this.client = undefined;
    this.snapshot = undefined;
    this.pushSnapshot();
  },

  setStatus(status) {
    if (status !== this.status) {
      console.log(LOG, status);
      journal(`état : ${status}`);
    }
    this.status = status;
  },

  currentState() {
    const local = this.local;
    const song = this.song;
    // la page ne rapporte rien (rechargement, erreur) : repli sur les infos de titre de Pear
    if (!local || !local.videoId || Date.now() - this.localAt > 10_000) {
      if (!song?.videoId) return null;
      return {
        video_id: song.videoId,
        position: song.elapsedSeconds ?? 0,
        paused: song.isPaused ?? true,
        duration: song.songDuration || 0,
        title: song.title,
        artist: song.artist,
        playlist_id: cleanPlaylistId(song.playlistId),
        upcoming: [],
        queue: [],
        volume: null,
      };
    }
    const same = song?.videoId === local.videoId ? song : undefined;
    return {
      video_id: local.videoId,
      position: local.position,
      paused: local.paused,
      duration: local.duration || same?.songDuration || 0,
      title: same?.title || local.title,
      artist: same?.artist || local.artist,
      playlist_id:
        cleanPlaylistId(local.playlistId) || cleanPlaylistId(same?.playlistId),
      upcoming: local.upcoming,
      queue: local.queue,
      volume: local.volume,
    };
  },

  async publishLoop(signal) {
    let backoff = PUBLISH_EVERY;
    while (!signal.aborted) {
      try {
        const state = this.currentState();
        const now = Date.now();
        if (
          this.published === undefined ||
          stateChanged(
            this.published,
            state,
            (now - this.publishedAt) / 1000,
          ) ||
          now - this.publishedAt >= HEARTBEAT
        ) {
          await this.client!.putState(state);
          this.published = state;
          this.publishedAt = now;
        }
        this.setStatus('connecté');
        backoff = PUBLISH_EVERY;
      } catch (e) {
        this.setStatus(`serveur : ${(e as Error).message}`);
        backoff = Math.min(backoff * 2, 30_000);
      }
      await sleep(backoff, signal);
    }
  },

  async commandLoop(signal) {
    let failures = 0;
    while (!signal.aborted) {
      try {
        const commands = await this.client!.takeCommands(LONG_POLL);
        if (signal.aborted) return;
        for (const cmd of commands) {
          try {
            this.execute(cmd);
          } catch (e) {
            console.warn(LOG, `commande ${cmd.type} en échec`, e);
          }
        }
        if (commands.length) this.publishedAt = 0; // republier vite le nouvel état
        failures = 0;
      } catch (e) {
        if (e instanceof ConnectError && e.status === 401)
          this.setStatus('jeton refusé par le serveur');
        failures += 1;
        await sleep(
          Math.min(2_000 << Math.min(failures - 1, 4), 30_000),
          signal,
        );
      }
    }
  },

  async eventsLoop(signal) {
    while (!signal.aborted) {
      try {
        await this.client!.events((snap) => {
          this.snapshot = snap;
          this.pushSnapshot();
        }, signal);
      } catch {
        /* coupure : on se reconnecte */
      }
      await sleep(3_000, signal);
    }
  },

  pushSnapshot() {
    const win = this.ctx?.window;
    if (!win || win.isDestroyed()) return;
    win.webContents.send('pear-connect:snapshot', {
      snapshot: this.snapshot ?? null,
      self: this.config?.device ?? '',
      status: this.status,
      version: PLUGIN_VERSION,
      update: this.update ?? null,
    });
  },

  execute(cmd) {
    const win = this.ctx!.window;
    if (cmd.type === 'enqueue') {
      getSongControls(win).addSongToQueue(
        cmd.video_id,
        cmd.next ? 'INSERT_AFTER_CURRENT_VIDEO' : 'INSERT_AT_END',
      );
      return;
    }
    journal(
      `commande ${cmd.type}${'video_id' in cmd ? ` ${cmd.video_id}` : ''}`,
    );
    if (cmd.type === 'resume') {
      journal(
        `reprise ${cmd.state.video_id} mix=${cmd.state.playlist_id || '-'} file=${cmd.state.upcoming?.length ?? 0} titres`,
      );
      console.log(
        LOG,
        `reprise de ${cmd.state.video_id} (${cmd.state.playlist_id || 'sans mix'})`,
      );
    }
    // Le reste passe par l'API du lecteur dans la page : indépendant de la mise en page
    // de YouTube Music (barre du lecteur classique ou nouveau mini-lecteur).
    win.webContents.send('pear-connect:command', cmd);
  },

  async action(action) {
    const client = this.client;
    if (!client) return { ok: false, error: `Pear Connect ${this.status}` };
    try {
      if (action.op === 'hello') {
        this.pushSnapshot();
      } else if (action.op === 'transfer') {
        journal(`transfert vers ${action.target}`);
        await client.transfer(action.target);
      } else if (action.op === 'control') {
        const body: Record<string, unknown> = { ...action };
        delete body.op;
        await client.control(body);
      }
      return { ok: true };
    } catch (e) {
      return { ok: false, error: (e as Error).message };
    }
  },
});
