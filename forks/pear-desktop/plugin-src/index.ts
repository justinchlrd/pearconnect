import { createPlugin } from '@/utils';

import { backend } from './backend';
import { defaultPearConnectConfig } from './config';
import { onMenu } from './menu';
import { renderer } from './renderer';

export default createPlugin({
  name: () => 'Pear Connect',
  description: () =>
    'Comme Spotify Connect : voir ce qui joue sur vos autres appareils, transférer la lecture (titre, position et mix) et les piloter depuis Pear.',
  restartNeeded: false,
  config: defaultPearConnectConfig,
  addedVersion: '3.12.X',
  menu: onMenu,

  backend,
  renderer,
});
