package app.morphe.jam.companion;

import android.content.Context;
import android.content.Intent;
import android.media.AudioManager;
import android.net.Uri;
import android.os.SystemClock;
import android.provider.Settings;
import android.util.Log;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

/**
 * Agent Connect du téléphone : publie l'état de YouTube Music (Morphe) sur le serveur,
 * exécute les commandes reçues et gère la reprise d'une session venue d'un autre appareil.
 */
final class Agent {
    interface Listener {
        /** Appelé (fil quelconque) quand l'état affiché doit être rafraîchi. */
        void onStatus(Status status);

        /** Une reprise nécessite que l'utilisateur ouvre YouTube Music (restriction Android). */
        void onResumeNeedsTap(JSONObject state, String url);
    }

    /** Ce qu'affichent la notification et l'écran principal. */
    static final class Status {
        volatile String server = "…";
        volatile String bridge = "…";
        volatile String local = "";
        volatile String activeDevice = "";
        volatile String activeTitle = "";
        volatile boolean activeIsMe;
        volatile boolean hasSession;
        /** Mise à jour de l'appli disponible : nom de version et lien de l'APK (vides sinon). */
        volatile String updateName = "";
        volatile String updateUrl = "";
        volatile long updateCheckedAt;
    }

    private static final String TAG = "PearConnect";
    private static final long PUBLISH_EVERY_MS = 2000;
    private static final long HEARTBEAT_MS = 15000;
    private static final long SNAPSHOT_EVERY_MS = 2000;
    private static final long RESUME_TIMEOUT_MS = 45000;
    private static final long UPDATE_CHECK_EVERY_MS = 24L * 3600 * 1000;
    private static final int LONG_POLL_S = 20;

    /** Reprise lancée par ResumeActivity (geste utilisateur) : évite d'ouvrir deux fois le même titre. */
    static volatile String launchedVideo = "";
    static volatile long launchedAt;

    final Status status = new Status();
    private final Context context;
    private final Prefs prefs;
    private final MusicBridge bridge;
    private final Listener listener;
    private volatile boolean running;
    private Thread publisher;
    private Thread commands;

    private volatile JSONObject published;
    private volatile long publishedAt;
    private long serverSnapshotAt;
    private JSONObject lastSnapshot;

    // vue « Lecture sur … » (mode participant du patch Jam)
    private JSONObject serverSnap;
    private long serverSnapReceived;
    private long clockSequence;
    private String viewSignature = "";
    private long viewRevision;
    private final String generation = "pearconnect-" + Long.toHexString(System.nanoTime());
    private String dismissedDevice = "";

    // reprise en attente : on attend que YouTube Music ait chargé le titre pour recaler la position
    private volatile JSONObject pendingResume;
    private volatile long pendingSince;

    Agent(Context context, Prefs prefs, MusicBridge bridge, Listener listener) {
        this.context = context.getApplicationContext();
        this.prefs = prefs;
        this.bridge = bridge;
        this.listener = listener;
    }

    private ConnectClient client() {
        return new ConnectClient(prefs.server(), prefs.token(), prefs.device());
    }

    synchronized void start() {
        if (running) return;
        running = true;
        publisher = new Thread(this::publishLoop, "pearconnect-etat");
        commands = new Thread(this::commandLoop, "pearconnect-commandes");
        publisher.start();
        commands.start();
    }

    synchronized void stop() {
        running = false;
        if (publisher != null) publisher.interrupt();
        if (commands != null) commands.interrupt();
        publisher = null;
        commands = null;
    }

    boolean running() {
        return running;
    }

    // -- publication de l'état -------------------------------------------------------------
    private void publishLoop() {
        long backoff = PUBLISH_EVERY_MS;
        while (running) {
            try {
                tick();
                backoff = PUBLISH_EVERY_MS;
            } catch (ConnectClient.ConnectException e) {
                status.server = e.getMessage();
                backoff = Math.min(backoff * 2, 30000);
                listener.onStatus(status);
            } catch (Exception e) {
                Log.w(TAG, "tour de publication en échec", e);
            }
            try {
                Thread.sleep(backoff);
            } catch (InterruptedException e) {
                return;
            }
        }
    }

    private void tick() throws ConnectClient.ConnectException, JSONException {
        ConnectClient client = client();
        JSONObject snapshot = null;
        JSONObject state = null;
        try {
            snapshot = bridge.snapshot();
            state = Mapping.stateFromSnapshot(snapshot);
            status.bridge = "connecté";
        } catch (MusicBridge.BridgeException e) {
            status.bridge = e.getMessage(); // YouTube Music fermé, pas de lecture, casting…
        }
        synchronized (this) {
            lastSnapshot = snapshot;
        }
        if (state != null) {
            Integer volume = mediaVolume();
            if (volume != null) state.put("volume", volume);
        }
        status.local = state == null ? "" :
            state.optString("artist") + " — " + state.optString("title")
                + (state.optBoolean("paused") ? " (en pause)" : "");

        completePendingResume(snapshot);

        long now = SystemClock.elapsedRealtime();
        double elapsed = (now - publishedAt) / 1000.0;
        if (!Mapping.sameState(published, state, 3.0, elapsed) || now - publishedAt >= HEARTBEAT_MS) {
            client.putState(state);
            published = state;
            publishedAt = now;
            status.server = "connecté";
        }
        if (now - serverSnapshotAt >= SNAPSHOT_EVERY_MS) {
            serverSnapshotAt = now;
            JSONObject snap = client.snapshot();
            synchronized (this) {
                serverSnap = snap;
                serverSnapReceived = SystemClock.elapsedRealtime();
                clockSequence++;
            }
            readSession(snap);
        }
        if (now - status.updateCheckedAt >= UPDATE_CHECK_EVERY_MS) checkUpdate(client);
        listener.onStatus(status);
    }

    /** Compare la version de l'appli à versions.json du serveur (une fois par jour, ou à la demande). */
    JSONObject checkUpdate(ConnectClient client) {
        status.updateCheckedAt = SystemClock.elapsedRealtime();
        try {
            JSONObject update = Mapping.updateInfo(client.versions(), BuildConfig.VERSION_CODE);
            status.updateName = update != null ? update.optString("version_name") : "";
            status.updateUrl = update != null ? update.optString("url") : "";
            return update;
        } catch (ConnectClient.ConnectException e) {
            Log.w(TAG, "vérification de mise à jour impossible : " + e.getMessage());
            return null;
        }
    }

    /** Vérification manuelle (bouton de l'écran principal) ; hors du fil principal. */
    JSONObject checkUpdateNow() {
        JSONObject update = checkUpdate(client());
        listener.onStatus(status);
        return update;
    }

    private void readSession(JSONObject snap) {
        String active = snap.optString("active", "");
        JSONObject session = snap.optJSONObject("session");
        JSONObject st = session != null ? session.optJSONObject("state") : null;
        status.hasSession = st != null;
        status.activeDevice = snap.isNull("active") ? "" : active;
        status.activeIsMe = prefs.device().equals(active);
        status.activeTitle = st == null ? "" : st.optString("artist") + " — " + st.optString("title");
    }

    // -- commandes ------------------------------------------------------------------------
    private void commandLoop() {
        long backoff = 2000;
        while (running) {
            try {
                JSONArray cmds = client().takeCommands(LONG_POLL_S);
                backoff = 2000;
                for (int i = 0; i < cmds.length(); i++) {
                    JSONObject cmd = cmds.optJSONObject(i);
                    if (cmd != null) handle(cmd);
                }
                if (cmds.length() > 0) publishedAt = 0; // republier vite le nouvel état
            } catch (ConnectClient.ConnectException e) {
                status.server = e.getMessage();
                listener.onStatus(status);
                try {
                    Thread.sleep(backoff);
                } catch (InterruptedException ie) {
                    return;
                }
                backoff = Math.min(backoff * 2, 30000);
            }
        }
    }

    void handle(JSONObject cmd) {
        try {
            if ("volume".equals(cmd.optString("type"))) {
                setMediaVolume(cmd.optInt("level"));
                return;
            }
            if ("resume".equals(cmd.optString("type"))) {
                JSONObject state = cmd.optJSONObject("state");
                if (state != null) resume(state, SystemClock.elapsedRealtime());
                return;
            }
            String type = cmd.optString("type");
            boolean queueEdit = "remove".equals(type) || "move".equals(type);
            JSONObject snapshot;
            synchronized (this) {
                snapshot = lastSnapshot;
            }
            // édition de la file : le pont exige la révision courante, on relit l'instantané
            if (snapshot == null || queueEdit) snapshot = bridge.snapshot();
            JSONObject request = Mapping.bridgeCommand(cmd, snapshot);
            if (request == null) {
                Log.w(TAG, "commande ignorée (rien en lecture ?) : " + type);
                return;
            }
            try {
                bridge.call(request);
            } catch (MusicBridge.BridgeException e) {
                // la file a bougé entre l'instantané et l'édition : une seconde tentative suffit
                if (!queueEdit || e.getMessage() == null || !e.getMessage().contains("Queue changed")) throw e;
                request = Mapping.bridgeCommand(cmd, bridge.snapshot());
                if (request != null) bridge.call(request);
            }
        } catch (MusicBridge.BridgeException | JSONException e) {
            Log.w(TAG, "commande " + cmd.optString("type") + " en échec : " + e.getMessage());
        }
    }

    /**
     * Reprend `state` sur le téléphone : ouvre YouTube Music sur le titre (dans son mix
     * quand on le connaît), puis recale la position une fois le titre chargé.
     */
    void resume(JSONObject state, long receivedAt) {
        String url = Mapping.musicUrl(state);
        if (url == null) return;
        try {
            pendingResume = new JSONObject(state.toString()).put("_receivedAt", receivedAt);
        } catch (JSONException e) {
            return;
        }
        pendingSince = receivedAt;
        String video = state.optString("video_id");
        boolean alreadyLaunched = video.equals(launchedVideo) && receivedAt - launchedAt < 20000;
        if (alreadyLaunched || video.equals(Mapping.clockVideo(lastSnapshotSafe()))) return;
        if (Settings.canDrawOverlays(context)) {
            // Autorisation « afficher par-dessus » : Android permet d'ouvrir YouTube Music depuis le service.
            context.startActivity(musicIntent(url, prefs.musicPackage()));
            markLaunched(video);
        } else {
            listener.onResumeNeedsTap(state, url);
        }
    }

    // -- volume du téléphone (flux média) -------------------------------------------------
    private Integer mediaVolume() {
        AudioManager am = context.getSystemService(AudioManager.class);
        if (am == null) return null;
        int max = am.getStreamMaxVolume(AudioManager.STREAM_MUSIC);
        return max <= 0 ? null : Math.round(am.getStreamVolume(AudioManager.STREAM_MUSIC) * 100f / max);
    }

    private void setMediaVolume(int level) {
        AudioManager am = context.getSystemService(AudioManager.class);
        if (am == null) return;
        int max = am.getStreamMaxVolume(AudioManager.STREAM_MUSIC);
        int index = Math.round(Math.max(0, Math.min(100, level)) * max / 100f);
        am.setStreamVolume(AudioManager.STREAM_MUSIC, index, 0);
    }

    // -- YouTube Music : vue « Lecture sur … » ----------------------------------------------
    /** Réponse à l'opération VIEW (YouTube Music l'interroge toutes les 0,4 à 3 s). */
    synchronized JSONObject jamView() throws JSONException {
        JSONObject remote = prefs.remoteView() ? Mapping.remoteSession(serverSnap, prefs.device()) : null;
        if (remote != null && remote.getString("device").equals(dismissedDevice)) remote = null;
        if (remote == null) {
            if (serverSnap != null && !dismissedDevice.equals(serverSnap.optString("active"))) dismissedDevice = "";
            return Mapping.idleView();
        }
        JSONArray items = Mapping.participantItems(remote.getJSONObject("state"));
        String signature = items.toString();
        if (!signature.equals(viewSignature)) {
            viewSignature = signature;
            viewRevision++;
        }
        return Mapping.participantView(remote, generation + ":" + viewRevision, serverSnapReceived,
            clockSequence, generation);
    }

    /** Opérations de YouTube Music en mode participant : pilotage de l'appareil qui joue. */
    JSONObject jamOperation(JSONObject request) throws JSONException {
        String op = request.optString("op");
        if ("VIEW".equals(op)) return jamView();
        if ("STATE".equals(op)) {
            String role = jamView().getJSONObject("session").optString("role", "Idle");
            return new JSONObject().put("ok", true).put("role", role);
        }
        if ("END".equals(op)) {
            // « Quitter et lire ici » : YouTube Music va jouer en local ; le serveur mettra
            // l'autre appareil en pause dès que le téléphone publiera sa lecture.
            synchronized (this) {
                JSONObject remote = Mapping.remoteSession(serverSnap, prefs.device());
                if (remote != null) dismissedDevice = remote.getString("device");
            }
            return Mapping.idleView();
        }
        JSONObject remoteState;
        synchronized (this) {
            JSONObject remote = Mapping.remoteSession(serverSnap, prefs.device());
            remoteState = remote != null ? remote.getJSONObject("state") : null;
        }
        JSONObject control = Mapping.controlForJamOp(request, remoteState);
        if (control == null) {
            return new JSONObject().put("ok", false)
                .put("error", "Pear Connect ne gère pas les sessions Jam entre amis");
        }
        try {
            client().control(control);
        } catch (ConnectClient.ConnectException e) {
            return new JSONObject().put("ok", false).put("error", "Pear Connect : " + e.getMessage());
        }
        serverSnapshotAt = 0; // relire l'état du serveur au prochain tour
        return jamView().put("dispatched", true);
    }

    static Intent musicIntent(String url, String musicPackage) {
        Intent i = new Intent(Intent.ACTION_VIEW, Uri.parse(url)).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        if (musicPackage != null && !musicPackage.isEmpty()) i.setPackage(musicPackage);
        return i;
    }

    static void markLaunched(String video) {
        launchedVideo = video;
        launchedAt = SystemClock.elapsedRealtime();
    }

    private synchronized JSONObject lastSnapshotSafe() {
        return lastSnapshot;
    }

    private void completePendingResume(JSONObject snapshot) {
        JSONObject target = pendingResume;
        if (target == null) return;
        long now = SystemClock.elapsedRealtime();
        if (now - pendingSince > RESUME_TIMEOUT_MS) {
            pendingResume = null;
            Log.w(TAG, "reprise abandonnée : le titre ne s'est pas chargé à temps");
            return;
        }
        String video = target.optString("video_id");
        if (snapshot == null || !video.equals(Mapping.clockVideo(snapshot))) return;
        JSONObject clock = snapshot.optJSONObject("clock");
        if (clock == null || !clock.has("position") || clock.optLong("duration") <= 0) return; // pas encore prêt
        pendingResume = null;
        try {
            double pos = Mapping.positionAt(target, target.optLong("_receivedAt"), now);
            if (pos > 2) bridge.call(Mapping.seek(video, pos, clock));
            boolean wantPaused = target.optBoolean("paused");
            if (wantPaused == clock.optBoolean("playing")) {
                JSONObject cmd = new JSONObject().put("type", wantPaused ? "pause" : "play");
                JSONObject req = Mapping.bridgeCommand(cmd, snapshot);
                if (req != null) bridge.call(req);
            }
            publishedAt = 0;
        } catch (MusicBridge.BridgeException | JSONException e) {
            Log.w(TAG, "recalage de la reprise en échec : " + e.getMessage());
        }
    }
}
