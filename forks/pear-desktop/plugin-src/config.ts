export interface PearConnectConfig {
  enabled: boolean;
  /** Adresse du serveur Connect, par ex. https://192.168.1.168:8443 */
  server: string;
  /** Nom de cet ordinateur auprès du serveur (celui donné à add-device). */
  device: string;
  /** Jeton de l'appareil (affiché une seule fois par add-device). */
  token: string;
  /** Autorité de certification du serveur (PEM), si elle n'est pas dans le magasin du système. */
  caPath: string;
}

export const defaultPearConnectConfig: PearConnectConfig = {
  enabled: false,
  server: '',
  device: '',
  token: '',
  caPath: '',
};
