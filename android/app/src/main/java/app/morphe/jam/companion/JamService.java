package app.morphe.jam.companion;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.net.Uri;
import android.os.Binder;
import android.os.Build;
import android.os.IBinder;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;

import org.json.JSONException;
import org.json.JSONObject;

import app.morphe.jam.ipc.IJamCompanion;

/**
 * Deux rôles :
 *  1. service compagnon que YouTube Music patché lie (IJamCompanion). Pear Connect n'utilise pas
 *     les sessions « Jam » entre amis : il répond « Idle » pour que l'interface Jam reste sage ;
 *  2. service de premier plan qui fait tourner l'agent Connect.
 */
public final class JamService extends Service implements Agent.Listener {
    static final String ACTION_START = "app.pearconnect.companion.START";
    static final String ACTION_STOP = "app.pearconnect.companion.STOP";
    private static final String CHANNEL = "connect";
    private static final String CHANNEL_RESUME = "resume";
    private static final String CHANNEL_UPDATE = "update";
    private static final int NOTIF_ID = 1;
    private static final int NOTIF_RESUME = 2;
    private static final int NOTIF_UPDATE = 3;
    private String announcedUpdate = "";

    private static volatile JamService instance;

    private Prefs prefs;
    private MusicBridge bridge;
    private Agent agent;
    private String lastNotification = "";

    static Agent.Status currentStatus() {
        JamService s = instance;
        return s != null && s.agent != null ? s.agent.status : null;
    }

    /** Vérification manuelle des mises à jour (hors du fil principal) ; null si l'agent ne tourne pas ou rien de neuf. */
    static JSONObject checkUpdateNow() {
        JamService s = instance;
        return s != null && s.agent != null ? s.agent.checkUpdateNow() : null;
    }

    static boolean agentRunning() {
        JamService s = instance;
        return s != null && s.agent != null && s.agent.running();
    }

    // -- IJamCompanion : appels de YouTube Music ----------------------------------------------
    private final IJamCompanion.Stub companion = new IJamCompanion.Stub() {
        @Override
        public String call(String capability, String request) {
            try {
                verifyCaller(capability);
                JSONObject r = new JSONObject(request == null ? "{}" : request);
                return Mapping.envelope(answer(r)).toString();
            } catch (SecurityException e) {
                throw e; // transmis à l'appelant par le Binder
            } catch (JSONException e) {
                return "{\"ok\":false,\"error\":\"requête invalide\"}";
            }
        }
    };

    private void verifyCaller(String capability) {
        String expected = prefs.capability();
        String[] pkgs = getPackageManager().getPackagesForUid(Binder.getCallingUid());
        boolean pkgOk = pkgs != null && pkgs.length == 1 && pkgs[0].equals(prefs.musicPackage());
        boolean capOk = expected.length() >= 32 && capability != null
            && MessageDigest.isEqual(expected.getBytes(StandardCharsets.UTF_8),
                                     capability.getBytes(StandardCharsets.UTF_8));
        if (!pkgOk || !capOk) throw new SecurityException("Appelant non appairé");
    }

    /**
     * Pear Connect se sert du mode « participant » du patch Jam pour afficher dans YouTube Music
     * ce qui joue sur un autre appareil (file, barre de progression, commandes renvoyées ici).
     */
    private JSONObject answer(JSONObject request) throws JSONException {
        String op = request.optString("op");
        if ("HELLO".equals(op) || "PING".equals(op)) return new JSONObject().put("ok", true);
        if (agent == null || !prefs.configured()) {
            return "VIEW".equals(op) || "END".equals(op) ? Mapping.idleView()
                : new JSONObject().put("ok", false).put("error", "Pear Connect n'est pas configuré");
        }
        return agent.jamOperation(request);
    }

    // -- cycle de vie --------------------------------------------------------------------------
    @Override
    public void onCreate() {
        super.onCreate();
        instance = this;
        prefs = new Prefs(this);
        bridge = new MusicBridge(this, prefs);
        agent = new Agent(this, prefs, bridge, this);
        createChannels();
    }

    @Override
    public IBinder onBind(Intent intent) {
        return companion;
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        // Toujours passer au premier plan : YouTube Music peut nous démarrer via startForegroundService.
        goForeground(buildNotification());
        String action = intent != null ? intent.getAction() : null;
        if (ACTION_STOP.equals(action) || !prefs.enabled() || !prefs.configured()) {
            agent.stop();
            stopForeground(STOP_FOREGROUND_REMOVE);
            stopSelf();
            return START_NOT_STICKY;
        }
        agent.start();
        return START_STICKY;
    }

    @Override
    public void onDestroy() {
        agent.stop();
        bridge.unbind();
        instance = null;
        super.onDestroy();
    }

    static void start(Context c) {
        c.startForegroundService(new Intent(c, JamService.class).setAction(ACTION_START));
    }

    static void stop(Context c) {
        c.startService(new Intent(c, JamService.class).setAction(ACTION_STOP));
    }

    private void goForeground(Notification n) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            startForeground(NOTIF_ID, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE);
        } else {
            startForeground(NOTIF_ID, n);
        }
    }

    // -- notifications --------------------------------------------------------------------------
    private void createChannels() {
        NotificationManager nm = getSystemService(NotificationManager.class);
        NotificationChannel main = new NotificationChannel(CHANNEL, "Pear Connect", NotificationManager.IMPORTANCE_LOW);
        main.setDescription("Session d'écoute et transfert entre appareils");
        main.setShowBadge(false);
        NotificationChannel resume = new NotificationChannel(CHANNEL_RESUME, "Reprise de la lecture",
            NotificationManager.IMPORTANCE_HIGH);
        resume.setDescription("Toucher pour reprendre ici une écoute transférée");
        NotificationChannel update = new NotificationChannel(CHANNEL_UPDATE, "Mises à jour de Pear Connect",
            NotificationManager.IMPORTANCE_DEFAULT);
        update.setDescription("Une nouvelle version de l'appli est disponible sur le serveur Connect");
        nm.createNotificationChannel(main);
        nm.createNotificationChannel(resume);
        nm.createNotificationChannel(update);
    }

    /** Une notification par version proposée ; toucher ouvre l'APK (installation par-dessus, même signature). */
    private void announceUpdate(Agent.Status s) {
        if (s.updateName.isEmpty() || s.updateUrl.isEmpty() || s.updateName.equals(announcedUpdate)) return;
        announcedUpdate = s.updateName;
        Intent open = new Intent(Intent.ACTION_VIEW, Uri.parse(s.updateUrl));
        Notification n = new Notification.Builder(this, CHANNEL_UPDATE)
            .setSmallIcon(R.drawable.ic_stat)
            .setContentTitle("Pear Connect " + s.updateName + " disponible")
            .setContentText("Installée : " + BuildConfig.VERSION_NAME + ". Toucher pour télécharger l'APK.")
            .setAutoCancel(true)
            .setCategory(Notification.CATEGORY_RECOMMENDATION)
            .setContentIntent(activityIntent(open, 15))
            .build();
        getSystemService(NotificationManager.class).notify(NOTIF_UPDATE, n);
    }

    private PendingIntent activityIntent(Intent i, int code) {
        return PendingIntent.getActivity(this, code, i,
            PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
    }

    private Notification buildNotification() {
        Agent.Status s = agent != null ? agent.status : new Agent.Status();
        String title;
        String text;
        if (s.hasSession && s.activeIsMe) {
            title = "En lecture ici";
            text = s.activeTitle;
        } else if (s.hasSession && !s.activeDevice.isEmpty()) {
            title = "En lecture sur " + s.activeDevice;
            text = s.activeTitle;
        } else {
            title = "Pear Connect";
            text = "Serveur : " + s.server + " · YouTube Music : " + s.bridge;
        }
        Notification.Builder b = new Notification.Builder(this, CHANNEL)
            .setSmallIcon(R.drawable.ic_stat)
            .setContentTitle(title)
            .setContentText(text)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .setCategory(Notification.CATEGORY_SERVICE)
            .setContentIntent(activityIntent(new Intent(this, MainActivity.class), 10));
        if (s.hasSession && !s.activeIsMe) {
            b.addAction(new Notification.Action.Builder(null, "Écouter ici",
                activityIntent(new Intent(this, ResumeActivity.class).setAction(ResumeActivity.ACTION_TRANSFER_HERE), 11)
            ).build());
        }
        b.addAction(new Notification.Action.Builder(null, "Appareils",
            activityIntent(new Intent(this, DevicesActivity.class), 14)).build());
        return b.build();
    }

    @Override
    public void onStatus(Agent.Status status) {
        announceUpdate(status);
        String key = status.server + status.bridge + status.activeDevice + status.activeTitle
            + status.activeIsMe + status.hasSession;
        if (key.equals(lastNotification)) return;
        lastNotification = key;
        getSystemService(NotificationManager.class).notify(NOTIF_ID, buildNotification());
    }

    @Override
    public void onResumeNeedsTap(JSONObject state, String url) {
        Intent open = new Intent(this, ResumeActivity.class)
            .setAction(ResumeActivity.ACTION_OPEN)
            .putExtra(ResumeActivity.EXTRA_URL, url)
            .putExtra(ResumeActivity.EXTRA_VIDEO, state.optString("video_id"));
        Notification n = new Notification.Builder(this, CHANNEL_RESUME)
            .setSmallIcon(R.drawable.ic_stat)
            .setContentTitle("Reprendre ici")
            .setContentText(state.optString("artist") + " — " + state.optString("title"))
            .setAutoCancel(true)
            .setCategory(Notification.CATEGORY_TRANSPORT)
            .setContentIntent(activityIntent(open, 13))
            .build();
        getSystemService(NotificationManager.class).notify(NOTIF_RESUME, n);
    }

    static void cancelResumeNotification(Context c) {
        c.getSystemService(NotificationManager.class).cancel(NOTIF_RESUME);
    }
}
