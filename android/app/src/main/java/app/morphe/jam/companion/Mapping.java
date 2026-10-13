package app.morphe.jam.companion;

import java.util.Locale;
import java.util.UUID;
import java.util.regex.Pattern;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

/**
 * Correspondances pures (sans API Android, testables sur JVM) entre :
 *  - l'instantané du pont Jam de YouTube Music patché (SNAPSHOT : items, autoplay, clock en ms) ;
 *  - l'état de lecture du serveur Connect (video_id, position en s, paused, upcoming…) ;
 *  - les commandes du serveur (pause, play, next, previous, seek) et les requêtes du pont.
 */
final class Mapping {
    static final int QUEUE_LIMIT = 25;
    private static final Pattern VIDEO_ID = Pattern.compile("^[A-Za-z0-9_-]{11}$");
    private static final Pattern PLAYLIST_ID = Pattern.compile("^[A-Za-z0-9_-]{2,128}$");
    private static final String[] FEATURES = {"queue-revisions", "stable-item-ids", "stale-edit-rejection"};

    private Mapping() {}

    /** Enveloppe de version exigée par le pont (BridgeProtocol v1). */
    static JSONObject envelope(JSONObject request) throws JSONException {
        JSONArray features = new JSONArray();
        for (String f : FEATURES) features.put(f);
        return request.put(
            "bridgeProtocol",
            new JSONObject().put("version", 1).put("minimumVersion", 1).put("features", features)
        );
    }

    static JSONObject request(String op) throws JSONException {
        return new JSONObject().put("op", op).put("id", UUID.randomUUID().toString());
    }

    static JSONObject currentItem(JSONObject snapshot) {
        JSONArray items = snapshot.optJSONArray("items");
        if (items == null) return null;
        for (int i = 0; i < items.length(); i++) {
            JSONObject item = items.optJSONObject(i);
            if (item != null && item.optBoolean("current")) return item;
        }
        return null;
    }

    /** Instantané du pont → état Connect, ou null si rien n'est chargé. */
    static JSONObject stateFromSnapshot(JSONObject snapshot) throws JSONException {
        if (snapshot == null || !snapshot.optBoolean("ok")) return null;
        JSONObject clock = snapshot.optJSONObject("clock");
        JSONObject current = currentItem(snapshot);
        String videoId = clock != null ? clock.optString("videoId", "") : "";
        if (videoId.isEmpty() && current != null) videoId = current.optString("videoId", "");
        if (!VIDEO_ID.matcher(videoId).matches()) return null;

        JSONObject state = new JSONObject()
            .put("video_id", videoId)
            .put("title", current != null ? current.optString("title", "") : "")
            .put("artist", current != null ? current.optString("artist", "") : "")
            // contexte (mix…) : fourni par le pont avec le patch « Pear Connect » (JamContext), sinon vide
            .put("playlist_id", cleanPlaylist(clock != null ? clock.optString("playlistId", "") : ""));
        if (clock != null && clock.has("position")) {
            state.put("position", clock.optLong("position") / 1000.0)
                .put("duration", clock.optLong("duration") / 1000.0)
                .put("paused", !clock.optBoolean("playing"));
        } else {
            state.put("position", 0.0).put("duration", 0.0).put("paused", true);
        }

        // Suite de la file puis suggestions automatiques (la « suite » du mix côté YouTube Music).
        JSONArray upcoming = new JSONArray();
        JSONArray items = snapshot.optJSONArray("items");
        boolean after = false;
        if (items != null) {
            for (int i = 0; i < items.length() && upcoming.length() < QUEUE_LIMIT; i++) {
                JSONObject item = items.optJSONObject(i);
                if (item == null) continue;
                if (after) addVideo(upcoming, item);
                if (item.optBoolean("current")) after = true;
            }
        }
        JSONArray autoplay = snapshot.optJSONArray("autoplay");
        if (autoplay != null) {
            for (int i = 0; i < autoplay.length() && upcoming.length() < QUEUE_LIMIT; i++) {
                JSONObject item = autoplay.optJSONObject(i);
                if (item != null) addVideo(upcoming, item);
            }
        }
        state.put("upcoming", upcoming);
        state.put("queue", queueMeta(snapshot));
        return state;
    }

    /** Titres à venir avec titre/artiste, pour les vues « Lecture sur … » des autres appareils. */
    static JSONArray queueMeta(JSONObject snapshot) throws JSONException {
        JSONArray out = new JSONArray();
        boolean after = false;
        for (String lane : new String[] {"items", "autoplay"}) {
            JSONArray rows = snapshot.optJSONArray(lane);
            if (rows == null) continue;
            for (int i = 0; i < rows.length() && out.length() < QUEUE_LIMIT; i++) {
                JSONObject item = rows.optJSONObject(i);
                if (item == null) continue;
                boolean include = "autoplay".equals(lane) || after;
                if ("items".equals(lane) && item.optBoolean("current")) after = true;
                String v = item.optString("videoId", "");
                if (include && VIDEO_ID.matcher(v).matches()) {
                    out.put(new JSONObject().put("video_id", v)
                        .put("title", item.optString("title", ""))
                        .put("artist", item.optString("artist", "")));
                }
            }
        }
        return out;
    }

    static String cleanPlaylist(String id) {
        return id != null && PLAYLIST_ID.matcher(id).matches() ? id : "";
    }

    private static void addVideo(JSONArray out, JSONObject item) {
        String v = item.optString("videoId", "");
        if (VIDEO_ID.matcher(v).matches()) out.put(v);
    }

    /**
     * L'état `b` est-il ce que le serveur déduit déjà de `a`, publié il y a `elapsedSeconds` ?
     * (le serveur extrapole la position d'un état en lecture : inutile de la republier sans cesse)
     */
    static boolean sameState(JSONObject a, JSONObject b, double driftSeconds, double elapsedSeconds) {
        if (a == null || b == null) return a == b;
        if (!a.optString("video_id").equals(b.optString("video_id"))) return false;
        if (a.optBoolean("paused") != b.optBoolean("paused")) return false;
        if (!String.valueOf(a.optJSONArray("upcoming")).equals(String.valueOf(b.optJSONArray("upcoming"))))
            return false;
        double expected = a.optDouble("position", 0) + (a.optBoolean("paused") ? 0 : elapsedSeconds);
        return Math.abs(expected - b.optDouble("position", 0)) <= driftSeconds;
    }

    /**
     * Commande du serveur → requête du pont. Null si la commande ne se traduit pas
     * directement (resume est géré à part : il faut ouvrir YouTube Music).
     */
    static JSONObject bridgeCommand(JSONObject cmd, JSONObject snapshot) throws JSONException {
        String type = cmd.optString("type");
        JSONObject current = snapshot != null ? currentItem(snapshot) : null;
        JSONObject clock = snapshot != null ? snapshot.optJSONObject("clock") : null;
        String videoId = clock != null ? clock.optString("videoId", "") : "";
        if (videoId.isEmpty() && current != null) videoId = current.optString("videoId", "");
        switch (type) {
            case "next":
                return request("SKIP_NEXT");
            case "previous":
                return request("SKIP_PREVIOUS");
            case "pause":
            case "play":
                if (current == null || videoId.isEmpty()) return null;
                return request("PLAY")
                    .put("videoId", videoId)
                    .put("item", current.optString("id"))
                    .put("playing", "play".equals(type));
            case "seek":
                if (videoId.isEmpty()) return null;
                return seek(videoId, cmd.optDouble("seconds", 0), clock);
            case "jump": {
                String target = cmd.optString("video_id", "");
                JSONObject item = snapshot != null ? findItem(snapshot, target) : null;
                if (item == null) return null;
                return request("PLAY").put("videoId", target).put("item", item.optString("id"));
            }
            case "enqueue": {
                String target = cmd.optString("video_id", "");
                if (!VIDEO_ID.matcher(target).matches()) return null;
                return request(cmd.optBoolean("next") ? "PLAY_NEXT" : "ADD").put("videoId", target);
            }
            default:
                return null;
        }
    }

    /** Élément de la file (ou des suggestions) correspondant à ce titre, hors titre en cours. */
    static JSONObject findItem(JSONObject snapshot, String videoId) {
        for (String lane : new String[] {"items", "autoplay"}) {
            JSONArray rows = snapshot.optJSONArray(lane);
            if (rows == null) continue;
            for (int i = 0; i < rows.length(); i++) {
                JSONObject row = rows.optJSONObject(i);
                if (row != null && !row.optBoolean("current") && videoId.equals(row.optString("videoId"))) return row;
            }
        }
        return null;
    }

    static JSONObject seek(String videoId, double seconds, JSONObject clock) throws JSONException {
        long ms = Math.max(0, Math.round(seconds * 1000));
        long duration = clock != null ? clock.optLong("duration", 0) : 0;
        if (duration > 0) ms = Math.min(ms, Math.max(0, duration - 1500)); // le pont refuse position >= durée
        return request("SEEK").put("videoId", videoId).put("position", ms);
    }

    /** Lien qui ouvre le titre DANS son contexte (mix, playlist) quand on le connaît. */
    static String musicUrl(JSONObject state) {
        String v = state.optString("video_id", "");
        if (!VIDEO_ID.matcher(v).matches()) return null;
        String url = "https://music.youtube.com/watch?v=" + v;
        String list = state.optString("playlist_id", "");
        if (PLAYLIST_ID.matcher(list).matches()) url += "&list=" + list;
        return url;
    }

    /** Position cible d'une reprise, extrapolée depuis la réception de la commande. */
    static double positionAt(JSONObject state, long receivedAtMs, long nowMs) {
        double pos = state.optDouble("position", 0);
        if (!state.optBoolean("paused")) pos += Math.max(0, nowMs - receivedAtMs) / 1000.0;
        double duration = state.optDouble("duration", 0);
        if (duration > 0) pos = Math.min(pos, Math.max(0, duration - 2));
        return pos;
    }

    // -- vue « Lecture sur … » dans YouTube Music (mode participant du patch Jam) ----------------

    /**
     * Session jouée par un AUTRE appareil en ligne, ou null : {"device", "state"} tirés du
     * snapshot du serveur. C'est elle que YouTube Music affiche en mode participant.
     */
    static JSONObject remoteSession(JSONObject serverSnapshot, String me) {
        if (serverSnapshot == null) return null;
        String active = serverSnapshot.isNull("active") ? "" : serverSnapshot.optString("active", "");
        if (active.isEmpty() || active.equals(me)) return null;
        JSONObject devices = serverSnapshot.optJSONObject("devices");
        JSONObject dev = devices != null ? devices.optJSONObject(active) : null;
        if (dev == null || !dev.optBoolean("online")) return null;
        JSONObject state = dev.optJSONObject("state");
        if (state == null || !VIDEO_ID.matcher(state.optString("video_id", "")).matches()) return null;
        try {
            return new JSONObject().put("device", active).put("state", state);
        } catch (JSONException e) {
            return null;
        }
    }

    /** Identifiant d'élément stable entre deux interrogations (exigé par la fonction stable-item-ids). */
    static String itemId(String videoId, int occurrence) {
        return Long.toString(((long) (videoId.hashCode() & 0x7fffffff)) * 64 + Math.min(occurrence, 63) + 1);
    }

    /** File affichée : titre en cours puis titres à venir, au format des lignes du pont Jam. */
    static JSONArray participantItems(JSONObject state) throws JSONException {
        JSONArray rows = new JSONArray();
        java.util.Map<String, Integer> seen = new java.util.HashMap<>();
        rows.put(row(state.optString("video_id"), state.optString("title"), state.optString("artist"), true, seen));
        JSONArray queue = state.optJSONArray("queue");
        if (queue != null) {
            for (int i = 0; i < queue.length() && rows.length() < 50; i++) {
                JSONObject q = queue.optJSONObject(i);
                if (q == null || !VIDEO_ID.matcher(q.optString("video_id", "")).matches()) continue;
                rows.put(row(q.optString("video_id"), q.optString("title"), q.optString("artist"), false, seen));
            }
        }
        return rows;
    }

    private static JSONObject row(String video, String title, String artist, boolean current,
                                  java.util.Map<String, Integer> seen) throws JSONException {
        int n = seen.merge(video, 1, Integer::sum) - 1;
        return new JSONObject()
            .put("id", itemId(video, n))
            .put("videoId", video)
            .put("title", title.isEmpty() ? video : title)
            .put("artist", artist)
            .put("thumbnail", "")
            .put("current", current);
    }

    /** Horloge du titre distant (ms), extrapolée par YouTube Music à partir de receivedAt. */
    static JSONObject participantClock(JSONObject state, long receivedAt, long sequence, String generation)
            throws JSONException {
        long duration = Math.round(state.optDouble("duration", 0) * 1000);
        long position = Math.round(state.optDouble("position", 0) * 1000);
        if (duration > 0) position = Math.min(position, duration);
        return new JSONObject()
            .put("videoId", state.optString("video_id"))
            .put("position", Math.max(0, position))
            .put("duration", Math.max(duration, 1000))
            .put("speed", 1.0)
            .put("playing", !state.optBoolean("paused"))
            .put("playbackControl", true)
            .put("generation", generation)
            .put("sequence", sequence)
            .put("age", 0)
            .put("receivedAt", receivedAt);
    }

    static JSONObject idleView() throws JSONException {
        return new JSONObject().put("ok", true)
            .put("session", new JSONObject().put("role", "Idle").put("paired", true));
    }

    /** Vue complète renvoyée à l'opération VIEW de YouTube Music. */
    static JSONObject participantView(JSONObject remote, String revision, long receivedAt, long sequence,
                                      String generation) throws JSONException {
        JSONObject state = remote.getJSONObject("state");
        JSONObject session = new JSONObject()
            .put("role", "Participant")
            // « LAN » : YouTube Music affiche « connecté » (et non « reconnexion à l'hôte »)
            .put("transport", "LAN")
            .put("paired", true)
            .put("peers", 1)
            .put("allowGuestEdits", true)
            .put("host", remote.getString("device"))
            .put("message", "Lecture sur " + remote.getString("device"));
        return new JSONObject()
            .put("ok", true)
            .put("revision", revision)
            .put("items", participantItems(state))
            .put("autoplay", new JSONArray())
            .put("clock", participantClock(state, receivedAt, sequence, generation))
            .put("session", session);
    }

    /**
     * Opération de YouTube Music en mode participant → commande de pilotage du serveur
     * (POST /v1/control), ou null si l'opération n'est pas un pilotage.
     */
    static JSONObject controlForJamOp(JSONObject request, JSONObject remoteState) throws JSONException {
        String op = request.optString("op");
        String video = request.optString("videoId", "");
        switch (op) {
            case "SKIP_NEXT":
                return new JSONObject().put("action", "next");
            case "SKIP_PREVIOUS":
                return new JSONObject().put("action", "previous");
            case "SEEK":
                return new JSONObject().put("action", "seek")
                    .put("seconds", Math.max(0, request.optLong("position")) / 1000.0);
            case "PLAY":
                if (request.has("playing")) {
                    return new JSONObject().put("action", request.optBoolean("playing") ? "play" : "pause");
                }
                if (!VIDEO_ID.matcher(video).matches()) return null;
                if (remoteState != null && video.equals(remoteState.optString("video_id"))) {
                    return new JSONObject().put("action", "play");
                }
                return new JSONObject().put("action", "jump").put("video_id", video);
            case "ADD":
            case "PLAY_NEXT":
                if (!VIDEO_ID.matcher(video).matches()) return null;
                return new JSONObject().put("action", "enqueue").put("video_id", video)
                    .put("next", "PLAY_NEXT".equals(op));
            default:
                return null;
        }
    }

    static String clockVideo(JSONObject snapshot) {
        JSONObject clock = snapshot != null ? snapshot.optJSONObject("clock") : null;
        return clock != null ? clock.optString("videoId", "") : "";
    }

    static String fmt(double seconds) {
        long s = Math.max(0, (long) seconds);
        return String.format(Locale.ROOT, "%d:%02d", s / 60, s % 60);
    }
}
