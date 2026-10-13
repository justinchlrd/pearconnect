package app.morphe.jam.companion;

import android.content.Context;
import android.content.SharedPreferences;

/** Réglages de l'appli (stockage privé de l'appli, non sauvegardé : allowBackup=false). */
final class Prefs {
    static final String DEFAULT_SERVER = "https://192.168.1.168:8443";
    static final String DEFAULT_DEVICE = "telephone";

    private final SharedPreferences p;

    Prefs(Context c) {
        p = c.getApplicationContext().getSharedPreferences("pearconnect", Context.MODE_PRIVATE);
    }

    String server() { return p.getString("server", DEFAULT_SERVER); }
    String device() { return p.getString("device", DEFAULT_DEVICE); }
    String token() { return p.getString("token", ""); }
    /** Jeton local généré par YouTube Music lors de l'appairage Jam. */
    String capability() { return p.getString("cap", ""); }
    /** Paquet de YouTube Music patché qui s'est appairé (peut être renommé par GmsCore). */
    String musicPackage() { return p.getString("musicPackage", ""); }
    boolean enabled() { return p.getBoolean("enabled", false); }
    /** Afficher dans YouTube Music ce qui joue sur les autres appareils (mode participant Jam). */
    boolean remoteView() { return p.getBoolean("remoteView", true); }

    void setRemoteView(boolean on) {
        p.edit().putBoolean("remoteView", on).apply();
    }

    boolean configured() {
        return !server().isEmpty() && !device().isEmpty() && !token().isEmpty();
    }

    boolean paired() {
        return capability().length() >= 32 && !musicPackage().isEmpty();
    }

    void saveConnect(String server, String device, String token) {
        p.edit()
            .putString("server", server.replaceAll("/+$", ""))
            .putString("device", device)
            .putString("token", token)
            .apply();
    }

    void savePairing(String musicPackage, String capability) {
        p.edit().putString("musicPackage", musicPackage).putString("cap", capability).apply();
    }

    void setEnabled(boolean on) {
        p.edit().putBoolean("enabled", on).apply();
    }
}
