package app.morphe.jam.companion;

import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.content.ServiceConnection;
import android.content.pm.PackageInfo;
import android.content.pm.PackageManager;
import android.content.pm.ServiceInfo;
import android.os.IBinder;
import android.os.RemoteException;
import android.util.Log;

import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;

import org.json.JSONException;
import org.json.JSONObject;

import app.morphe.jam.ipc.IJamBridge;

/**
 * Accès au pont exposé par YouTube Music patché (service JamBridgeService du patch
 * Morphe « Jam queue sharing »), authentifié par le jeton local d'appairage.
 */
final class MusicBridge {
    // Nom du service du pont : app.morphe.extension.music.patches.jam.JamBridgeService (Morphe ≥ 1.47),
    // app.morphe.extension.music.jam.JamBridgeService avant. On le découvre par son suffixe.
    private static final String TAG = "PearConnect";

    static final class BridgeException extends Exception {
        BridgeException(String message) {
            super(message);
        }
    }

    private final Context context;
    private final Prefs prefs;
    private volatile IJamBridge bridge;
    private volatile CountDownLatch connected = new CountDownLatch(1);
    private boolean bound;

    private final ServiceConnection connection = new ServiceConnection() {
        @Override
        public void onServiceConnected(ComponentName name, IBinder service) {
            bridge = IJamBridge.Stub.asInterface(service);
            connected.countDown();
        }

        @Override
        public void onServiceDisconnected(ComponentName name) {
            bridge = null;
            connected = new CountDownLatch(1);
        }

        @Override
        public void onBindingDied(ComponentName name) {
            unbind();
        }

        @Override
        public void onNullBinding(ComponentName name) {
            // Le patch refuse de se lier quand Jam est désactivé dans YouTube Music.
            Log.w(TAG, "pont Jam refusé : activer « Jam queue sharing » dans les réglages Morphe");
            unbind();
        }
    };

    MusicBridge(Context context, Prefs prefs) {
        this.context = context.getApplicationContext();
        this.prefs = prefs;
    }

    /**
     * Trouve le service du pont dans YouTube Music patché, quel que soit son nom selon la version
     * des patchs (le nom a changé entre Morphe 1.46 et 1.47).
     */
    static ComponentName findBridge(Context context, String pkg) throws BridgeException {
        PackageInfo info;
        try {
            info = context.getPackageManager().getPackageInfo(pkg, PackageManager.GET_SERVICES);
        } catch (PackageManager.NameNotFoundException e) {
            throw new BridgeException(pkg + " invisible ou désinstallé (visibilité des paquets Android)");
        }
        if (info.services != null) {
            for (ServiceInfo s : info.services) {
                if (s.name.endsWith(".JamBridgeService")) {
                    if (!s.exported) throw new BridgeException("pont Jam non exporté dans " + pkg);
                    return new ComponentName(pkg, s.name);
                }
            }
        }
        throw new BridgeException("aucun service Jam dans " + pkg + " : patch « Jam queue sharing » absent ?");
    }

    private synchronized void ensureBound() throws BridgeException {
        if (bound) return;
        if (!prefs.paired()) throw new BridgeException("non appairé avec YouTube Music");
        ComponentName component = findBridge(context, prefs.musicPackage());
        try {
            bound = context.bindService(new Intent().setComponent(component), connection, Context.BIND_AUTO_CREATE);
        } catch (SecurityException e) {
            throw new BridgeException("liaison refusée par Android : " + e.getMessage());
        }
        if (!bound) throw new BridgeException("liaison impossible à " + component.getClassName());
    }

    synchronized void unbind() {
        if (bound) {
            try {
                context.unbindService(connection);
            } catch (IllegalArgumentException ignored) {
                // déjà délié
            }
        }
        bound = false;
        bridge = null;
        connected = new CountDownLatch(1);
    }

    /** Appel synchrone du pont ; à faire hors du fil principal. */
    JSONObject call(JSONObject request) throws BridgeException {
        ensureBound();
        try {
            if (!connected.await(5, TimeUnit.SECONDS)) throw new BridgeException("YouTube Music ne répond pas");
            IJamBridge b = bridge;
            if (b == null) throw new BridgeException("pont Jam déconnecté");
            String raw = b.call(prefs.capability(), Mapping.envelope(request).toString());
            JSONObject reply = new JSONObject(raw == null ? "{}" : raw);
            if (!reply.optBoolean("ok")) throw new BridgeException(reply.optString("error", "erreur du pont"));
            return reply;
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new BridgeException("interrompu");
        } catch (RemoteException | JSONException e) {
            throw new BridgeException("appel du pont impossible : " + e.getMessage());
        } catch (SecurityException e) {
            // jeton périmé (réappairage fait côté YouTube Music) ou paquet compagnon modifié
            throw new BridgeException("appairage refusé par YouTube Music : réappairer depuis Morphe");
        }
    }

    JSONObject snapshot() throws BridgeException {
        try {
            return call(new JSONObject().put("op", "SNAPSHOT"));
        } catch (JSONException e) {
            throw new BridgeException(e.getMessage());
        }
    }
}
