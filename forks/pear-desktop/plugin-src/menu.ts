import prompt from 'custom-electron-prompt';
import { dialog, shell, type MenuItemConstructorOptions } from 'electron';

import promptOptions from '@/providers/prompt-options';

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

export const onMenu = async ({
  getConfig,
  setConfig,
  window,
}: MenuContext<PearConnectConfig>): Promise<MenuItemConstructorOptions[]> => {
  const config = await getConfig();
  const masked = config.token ? '•••• (enregistré)' : 'à renseigner';

  return [
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
      label: `Autorité de certification : ${config.caPath || 'magasin de Windows'}`,
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
          label: 'Utiliser le magasin de Windows',
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
