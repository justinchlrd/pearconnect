import prompt from 'custom-electron-prompt';
import {
  clipboard,
  dialog,
  shell,
  type MenuItemConstructorOptions,
} from 'electron';

import promptOptions from '@/providers/prompt-options';

import { backend } from './backend';
import { PLUGIN_VERSION } from './version';

import type { PearConnectConfig } from './config';
import type { MenuContext } from '@/types/contexts';

const ask = async (
  window: MenuContext<PearConnectConfig>['window'],
  title: string,
  label: string,
  value: string,
  type: 'input' = 'input',
) =>
  await prompt(
    { title, label, value, type, width: 460, ...promptOptions() },
    window,
  );

/** Libellé de la ligne « Version » : installée, et mise à jour disponible le cas échéant. */
const versionLabel = () => {
  const base = `Version : v${PLUGIN_VERSION}`;
  if (backend.update) return `${base} — mise à jour v${backend.update.latest} disponible`;
  if (backend.update === null) return `${base} (à jour)`;
  return base;
};

/** Vérifie maintenant et affiche le résultat (commande d'installation copiable). */
const checkNow = async (window: MenuContext<PearConnectConfig>['window']) => {
  const update = await backend.checkUpdate();
  if (backend.updateError && !update) {
    await dialog.showMessageBox(window, {
      type: 'warning',
      title: 'Pear Connect',
      message: 'Impossible de vérifier les mises à jour',
      detail: `Plugin installé : v${PLUGIN_VERSION}\n${backend.updateError}`,
    });
    return;
  }
  if (!update) {
    await dialog.showMessageBox(window, {
      type: 'info',
      title: 'Pear Connect',
      message: `Pear Connect v${PLUGIN_VERSION} est à jour.`,
    });
    return;
  }
  const { response } = await dialog.showMessageBox(window, {
    type: 'info',
    title: 'Pear Connect',
    message: `Mise à jour v${update.latest} disponible (installée : v${update.current})`,
    detail: update.command
      ? `Dans ${process.platform === 'win32' ? 'PowerShell' : 'un terminal'}, Pear ouvert :\n\n${update.command}\n\nLe script remplace app.asar, garde le nom et le jeton de cet ordinateur et relance Pear.`
      : 'Relance l’installation depuis le serveur Connect.',
    buttons: update.command ? ['Copier la commande', 'Fermer'] : ['Fermer'],
    defaultId: 0,
    cancelId: update.command ? 1 : 0,
  });
  if (update.command && response === 0) clipboard.writeText(update.command);
};

export const onMenu = async ({
  getConfig,
  setConfig,
  window,
}: MenuContext<PearConnectConfig>): Promise<MenuItemConstructorOptions[]> => {
  const config = await getConfig();
  const masked = config.token ? '•••• (enregistré)' : 'à renseigner';

  return [
    {
      label: versionLabel(),
      submenu: [
        {
          label: 'Rechercher une mise à jour…',
          enabled: !!config.server && !!config.token,
          async click() {
            await checkNow(window);
          },
        },
      ],
    },
    { type: 'separator' },
    {
      label: `Serveur : ${config.server || 'à renseigner'}`,
      async click() {
        const value = await ask(
          window,
          'Pear Connect',
          'Adresse du serveur Connect (ex. https://192.168.1.168:8443)',
          (await getConfig()).server,
        );
        if (value !== null)
          setConfig({ server: value.trim().replace(/\/+$/, '') });
      },
    },
    {
      label: `Nom de cet ordinateur : ${config.device || 'à renseigner'}`,
      async click() {
        const value = await ask(
          window,
          'Pear Connect',
          'Nom de cet ordinateur sur le serveur (celui donné à add-device)',
          (await getConfig()).device,
        );
        if (value !== null && /^[A-Za-z0-9_.-]{1,64}$/.test(value.trim())) {
          setConfig({ device: value.trim() });
        }
      },
    },
    {
      label: `Jeton : ${masked}`,
      async click() {
        const value = await ask(
          window,
          'Pear Connect',
          "Jeton de l'appareil (jetons.txt)",
          '',
        );
        if (value) setConfig({ token: value.trim() });
      },
    },
    {
      label: `Autorité de certification : ${config.caPath || 'magasin du système'}`,
      submenu: [
        {
          label: 'Choisir le fichier (.crt, .pem)…',
          async click() {
            const result = await dialog.showOpenDialog(window, {
              title: 'Autorité de certification du serveur Connect',
              filters: [
                { name: 'Certificat', extensions: ['crt', 'pem', 'cer'] },
              ],
              properties: ['openFile'],
            });
            if (!result.canceled && result.filePaths[0])
              setConfig({ caPath: result.filePaths[0] });
          },
        },
        {
          label: 'Utiliser le magasin du système',
          click: () => setConfig({ caPath: '' }),
        },
      ],
    },
    { type: 'separator' },
    {
      label: 'Ouvrir la télécommande web',
      enabled: /^https?:\/\//.test(config.server),
      click: () => shell.openExternal(config.server),
    },
  ];
};
