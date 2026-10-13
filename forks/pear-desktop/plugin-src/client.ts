/*
 * Client HTTP(S) du serveur Connect, côté processus principal (Node).
 * node:https plutôt que fetch : permet de faire confiance à l'autorité de
 * certification du homelab sans l'installer dans Windows.
 */
import { readFileSync } from 'node:fs';
import { request as httpRequest, type IncomingMessage } from 'node:http';
import { request as httpsRequest } from 'node:https';

import type {
  Command,
  PlaybackState,
  Snapshot,
  VersionsInfo,
} from './protocol';

export class ConnectError extends Error {
  constructor(
    message: string,
    readonly status = 0,
  ) {
    super(message);
  }
}

export interface ClientOptions {
  server: string;
  token: string;
  device: string;
  caPath?: string;
}

const MAX_BODY = 2 * 1024 * 1024;

export class ConnectClient {
  private readonly base: URL;
  private readonly ca?: Buffer;

  constructor(private readonly opts: ClientOptions) {
    this.base = new URL(opts.server.replace(/\/+$/, '') + '/');
    if (this.base.protocol !== 'http:' && this.base.protocol !== 'https:') {
      throw new ConnectError(
        'adresse du serveur invalide (http:// ou https://)',
      );
    }
    if (opts.caPath) this.ca = readFileSync(opts.caPath);
  }

  private open(
    method: string,
    path: string,
    body: unknown,
    timeoutMs: number,
    onResponse: (res: IncomingMessage) => void,
    onError: (err: Error) => void,
    signal?: AbortSignal,
  ) {
    const url = new URL(path.replace(/^\//, ''), this.base);
    const data =
      body === undefined ? undefined : Buffer.from(JSON.stringify(body));
    const req = (url.protocol === 'https:' ? httpsRequest : httpRequest)(
      url,
      {
        method,
        ca: this.ca,
        signal,
        headers: {
          'Authorization': `Bearer ${this.opts.token}`,
          ...(data
            ? {
                'Content-Type': 'application/json',
                'Content-Length': data.length,
              }
            : method === 'GET'
              ? {}
              : { 'Content-Length': 0 }),
        },
      },
      onResponse,
    );
    req.setTimeout(timeoutMs, () =>
      req.destroy(new ConnectError('délai dépassé')),
    );
    req.on('error', onError);
    req.end(data);
  }

  request<T>(
    method: string,
    path: string,
    body?: unknown,
    timeoutMs = 15_000,
  ): Promise<T> {
    return new Promise<T>((resolve, reject) => {
      this.open(
        method,
        path,
        body,
        timeoutMs,
        (res) => {
          const chunks: Buffer[] = [];
          let size = 0;
          res.on('data', (c: Buffer) => {
            size += c.length;
            if (size > MAX_BODY)
              res.destroy(new ConnectError('réponse trop grande'));
            else chunks.push(c);
          });
          res.on('error', (e) => reject(e));
          res.on('end', () => {
            const text = Buffer.concat(chunks).toString('utf8');
            const status = res.statusCode ?? 0;
            if (status === 401)
              return reject(
                new ConnectError('jeton refusé par le serveur', 401),
              );
            if (status >= 400) {
              let detail = '';
              try {
                detail = (JSON.parse(text) as { error?: string }).error ?? '';
              } catch {
                /* corps non JSON */
              }
              return reject(
                new ConnectError(
                  `HTTP ${status}${detail ? ` : ${detail}` : ''}`,
                  status,
                ),
              );
            }
            try {
              resolve((text ? JSON.parse(text) : null) as T);
            } catch {
              reject(new ConnectError('réponse illisible'));
            }
          });
        },
        (err) =>
          reject(
            err instanceof ConnectError
              ? err
              : new ConnectError(`serveur injoignable : ${err.message}`),
          ),
      );
    });
  }

  private get node() {
    return encodeURIComponent(this.opts.device);
  }

  putState(state: PlaybackState | null) {
    return this.request('PUT', `/v1/devices/${this.node}/state`, {
      kind: 'pear',
      state,
    });
  }

  async takeCommands(waitSeconds: number): Promise<Command[]> {
    const res = await this.request<{ commands?: Command[] }>(
      'POST',
      `/v1/devices/${this.node}/commands/take?wait=${waitSeconds}`,
      undefined,
      (waitSeconds + 15) * 1000,
    );
    return res?.commands ?? [];
  }

  snapshot() {
    return this.request<Snapshot>('GET', '/v1/snapshot');
  }

  /** Versions publiées (fichier public du serveur, voir build_dist.py). */
  versions() {
    return this.request<VersionsInfo | null>('GET', '/versions.json');
  }

  transfer(target: string) {
    return this.request('POST', '/v1/transfer', { target });
  }

  control(body: Record<string, unknown>) {
    return this.request('POST', '/v1/control', body);
  }

  /** Flux temps réel /v1/events : appelle `onSnapshot` à chaque changement, jusqu'à coupure. */
  events(
    onSnapshot: (snap: Snapshot) => void,
    signal: AbortSignal,
  ): Promise<void> {
    return new Promise<void>((resolve, reject) => {
      this.open(
        'GET',
        '/v1/events',
        undefined,
        60_000, // le serveur envoie un keepalive toutes les 20 s
        (res) => {
          if ((res.statusCode ?? 0) >= 400) {
            res.resume();
            return reject(
              new ConnectError(
                `HTTP ${res.statusCode} sur /v1/events`,
                res.statusCode,
              ),
            );
          }
          res.setEncoding('utf8');
          let buffer = '';
          res.on('data', (chunk: string) => {
            buffer += chunk;
            if (buffer.length > MAX_BODY) {
              res.destroy(new ConnectError('flux trop volumineux'));
              return;
            }
            let sep: number;
            while ((sep = buffer.indexOf('\n\n')) >= 0) {
              const block = buffer.slice(0, sep);
              buffer = buffer.slice(sep + 2);
              const data = block
                .split('\n')
                .filter((l) => l.startsWith('data:'))
                .map((l) => l.slice(5).trimStart())
                .join('\n');
              if (!data) continue;
              try {
                onSnapshot(JSON.parse(data) as Snapshot);
              } catch {
                /* bloc illisible : ignoré */
              }
            }
          });
          res.on('end', () => resolve());
          res.on('error', (e) => reject(e));
        },
        (err) => reject(err),
        signal,
      );
    });
  }
}
