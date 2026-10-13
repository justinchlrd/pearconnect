package app.morphe.jam.companion;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;

import org.json.JSONArray;
import org.json.JSONObject;
import org.junit.Test;

public class MappingTest {
    /** Instantané au format du pont Jam (JamBridgeService.snapshotOnExecutor + JamClock.snapshot). */
    private static JSONObject snapshot(boolean playing) throws Exception {
        JSONArray items = new JSONArray()
            .put(new JSONObject().put("id", "11").put("videoId", "aaaaaaaaaaa").put("title", "Avant").put("artist", "X").put("current", false))
            .put(new JSONObject().put("id", "12").put("videoId", "bbbbbbbbbbb").put("title", "Club Bizarre").put("artist", "Seeko").put("current", true))
            .put(new JSONObject().put("id", "13").put("videoId", "ccccccccccc").put("title", "Après").put("artist", "Y").put("current", false));
        JSONArray autoplay = new JSONArray()
            .put(new JSONObject().put("id", "21").put("videoId", "ddddddddddd").put("current", false))
            .put(new JSONObject().put("id", "22").put("videoId", "pas-un-id").put("current", false));
        JSONObject clock = new JSONObject().put("videoId", "bbbbbbbbbbb").put("position", 128000L)
            .put("duration", 133000L).put("playing", playing);
        return new JSONObject().put("ok", true).put("revision", "g:1").put("items", items)
            .put("autoplay", autoplay).put("clock", clock);
    }

    @Test
    public void snapshotBecomesConnectState() throws Exception {
        JSONObject s = Mapping.stateFromSnapshot(snapshot(true));
        assertEquals("bbbbbbbbbbb", s.getString("video_id"));
        assertEquals("Club Bizarre", s.getString("title"));
        assertEquals("Seeko", s.getString("artist"));
        assertEquals(128.0, s.getDouble("position"), 0.001);
        assertEquals(133.0, s.getDouble("duration"), 0.001);
        assertFalse(s.getBoolean("paused"));
        assertEquals("", s.getString("playlist_id"));
        JSONArray up = s.getJSONArray("upcoming");
        assertEquals(2, up.length()); // suite de la file + suggestion valide, sans l'id invalide
        assertEquals("ccccccccccc", up.getString(0));
        assertEquals("ddddddddddd", up.getString(1));
    }

    @Test
    public void nothingLoadedGivesNull() throws Exception {
        assertNull(Mapping.stateFromSnapshot(new JSONObject().put("ok", true).put("items", new JSONArray())));
        assertNull(Mapping.stateFromSnapshot(new JSONObject().put("ok", false)));
        assertNull(Mapping.stateFromSnapshot(null));
    }

    @Test
    public void pauseAndPlayTargetCurrentItem() throws Exception {
        JSONObject r = Mapping.bridgeCommand(new JSONObject().put("type", "pause"), snapshot(true));
        assertEquals("PLAY", r.getString("op"));
        assertEquals("bbbbbbbbbbb", r.getString("videoId"));
        assertEquals("12", r.getString("item"));
        assertFalse(r.getBoolean("playing"));
        assertEquals(36, r.getString("id").length()); // UUID exigé par le pont
        assertTrue(Mapping.bridgeCommand(new JSONObject().put("type", "play"), snapshot(false)).getBoolean("playing"));
    }

    @Test
    public void seekIsClampedBelowDuration() throws Exception {
        JSONObject r = Mapping.bridgeCommand(new JSONObject().put("type", "seek").put("seconds", 500), snapshot(true));
        assertEquals("SEEK", r.getString("op"));
        assertEquals(131500L, r.getLong("position"));
        JSONObject ok = Mapping.bridgeCommand(new JSONObject().put("type", "seek").put("seconds", 61.5), snapshot(true));
        assertEquals(61500L, ok.getLong("position"));
    }

    @Test
    public void skipsAndUnknown() throws Exception {
        assertEquals("SKIP_NEXT", Mapping.bridgeCommand(new JSONObject().put("type", "next"), null).getString("op"));
        assertEquals("SKIP_PREVIOUS", Mapping.bridgeCommand(new JSONObject().put("type", "previous"), null).getString("op"));
        assertNull(Mapping.bridgeCommand(new JSONObject().put("type", "pause"), null));
        assertNull(Mapping.bridgeCommand(new JSONObject().put("type", "rm -rf"), snapshot(true)));
    }

    @Test
    public void musicUrlKeepsMixAndRejectsInjection() throws Exception {
        JSONObject st = new JSONObject().put("video_id", "bbbbbbbbbbb").put("playlist_id", "RDAMVMxyz");
        assertEquals("https://music.youtube.com/watch?v=bbbbbbbbbbb&list=RDAMVMxyz", Mapping.musicUrl(st));
        st.put("playlist_id", "x&evil=1");
        assertEquals("https://music.youtube.com/watch?v=bbbbbbbbbbb", Mapping.musicUrl(st));
        assertNull(Mapping.musicUrl(new JSONObject().put("video_id", "../../x")));
    }

    @Test
    public void resumePositionExtrapolated() throws Exception {
        JSONObject st = new JSONObject().put("position", 40.0).put("duration", 200.0).put("paused", false);
        assertEquals(43.0, Mapping.positionAt(st, 1000, 4000), 0.001);
        st.put("paused", true);
        assertEquals(40.0, Mapping.positionAt(st, 1000, 4000), 0.001);
        st.put("paused", false).put("position", 199.0);
        assertEquals(198.0, Mapping.positionAt(st, 0, 60000), 0.001);
    }

    @Test
    public void sameStateIgnoresSmallDrift() throws Exception {
        JSONObject a = Mapping.stateFromSnapshot(snapshot(true));
        // 10 s plus tard, en lecture : position +10 s attendue, donc rien à republier
        JSONObject b = new JSONObject(a.toString()).put("position", a.getDouble("position") + 10);
        assertTrue(Mapping.sameState(a, b, 3.0, 10.0));
        // seek de 30 s : à republier
        b.put("position", a.getDouble("position") + 40);
        assertFalse(Mapping.sameState(a, b, 3.0, 10.0));
        b.put("position", a.getDouble("position") + 10).put("paused", true);
        assertFalse(Mapping.sameState(a, b, 3.0, 10.0));
        assertTrue(Mapping.sameState(null, null, 3.0, 0));
        assertFalse(Mapping.sameState(a, null, 3.0, 0));
    }

    @Test
    public void envelopeAdvertisesProtocol() throws Exception {
        JSONObject e = Mapping.envelope(new JSONObject().put("op", "SNAPSHOT"));
        JSONObject p = e.getJSONObject("bridgeProtocol");
        assertEquals(1, p.getInt("version"));
        assertEquals(3, p.getJSONArray("features").length());
    }

    private static JSONObject serverSnapshot(String active, boolean online) throws Exception {
        JSONObject st = new JSONObject().put("video_id", "eeeeeeeeeee").put("title", "Control").put("artist", "JSTJR")
            .put("position", 61.5).put("duration", 146.0).put("paused", false).put("volume", 40)
            .put("queue", new JSONArray()
                .put(new JSONObject().put("video_id", "fffffffffff").put("title", "Maniac").put("artist", "Luke Noize"))
                .put(new JSONObject().put("video_id", "eeeeeeeeeee").put("title", "Control").put("artist", "JSTJR"))
                .put(new JSONObject().put("video_id", "pas valide").put("title", "x")));
        JSONObject devices = new JSONObject()
            .put("the-kernel", new JSONObject().put("kind", "pear").put("online", online).put("state", st))
            .put("telephone", new JSONObject().put("kind", "android").put("online", true).put("state", JSONObject.NULL));
        return new JSONObject().put("active", active).put("devices", devices);
    }

    @Test
    public void remoteSessionOnlyForAnotherOnlineDevice() throws Exception {
        assertEquals("the-kernel", Mapping.remoteSession(serverSnapshot("the-kernel", true), "telephone").getString("device"));
        assertNull(Mapping.remoteSession(serverSnapshot("the-kernel", false), "telephone"));
        assertNull(Mapping.remoteSession(serverSnapshot("telephone", true), "telephone"));
        assertNull(Mapping.remoteSession(new JSONObject().put("active", JSONObject.NULL), "telephone"));
        assertNull(Mapping.remoteSession(null, "telephone"));
    }

    @Test
    public void participantViewMatchesJamSchema() throws Exception {
        JSONObject remote = Mapping.remoteSession(serverSnapshot("the-kernel", true), "telephone");
        JSONObject v = Mapping.participantView(remote, "g:3", 1000L, 7L, "g");
        assertTrue(v.getBoolean("ok"));
        assertEquals("Participant", v.getJSONObject("session").getString("role"));
        assertEquals("LAN", v.getJSONObject("session").getString("transport"));
        JSONArray items = v.getJSONArray("items");
        assertEquals(3, items.length()); // en cours + 2 à venir valides
        assertTrue(items.getJSONObject(0).getBoolean("current"));
        assertFalse(items.getJSONObject(2).getBoolean("current"));
        // le même titre deux fois : identifiants distincts mais stables
        assertTrue(!items.getJSONObject(0).getString("id").equals(items.getJSONObject(2).getString("id")));
        assertEquals(items.getJSONObject(0).getString("id"), Mapping.itemId("eeeeeeeeeee", 0));
        JSONObject clock = v.getJSONObject("clock");
        assertEquals("eeeeeeeeeee", clock.getString("videoId"));
        assertEquals(61500L, clock.getLong("position"));
        assertEquals(146000L, clock.getLong("duration"));
        assertTrue(clock.getBoolean("playing"));
        assertTrue(clock.getBoolean("playbackControl"));
        assertEquals(7L, clock.getLong("sequence"));
        assertEquals(1000L, clock.getLong("receivedAt"));
    }

    @Test
    public void jamOperationsBecomeServerControls() throws Exception {
        JSONObject st = new JSONObject().put("video_id", "eeeeeeeeeee");
        assertEquals("next", Mapping.controlForJamOp(new JSONObject().put("op", "SKIP_NEXT"), st).getString("action"));
        assertEquals("previous", Mapping.controlForJamOp(new JSONObject().put("op", "SKIP_PREVIOUS"), st).getString("action"));
        JSONObject seek = Mapping.controlForJamOp(new JSONObject().put("op", "SEEK").put("videoId", "eeeeeeeeeee").put("position", 90500L), st);
        assertEquals("seek", seek.getString("action"));
        assertEquals(90.5, seek.getDouble("seconds"), 0.001);
        assertEquals("pause", Mapping.controlForJamOp(new JSONObject().put("op", "PLAY").put("videoId", "eeeeeeeeeee").put("playing", false), st).getString("action"));
        JSONObject jump = Mapping.controlForJamOp(new JSONObject().put("op", "PLAY").put("videoId", "fffffffffff").put("item", "1"), st);
        assertEquals("jump", jump.getString("action"));
        assertEquals("fffffffffff", jump.getString("video_id"));
        JSONObject next = Mapping.controlForJamOp(new JSONObject().put("op", "PLAY_NEXT").put("videoId", "fffffffffff"), st);
        assertEquals("enqueue", next.getString("action"));
        assertTrue(next.getBoolean("next"));
        assertNull(Mapping.controlForJamOp(new JSONObject().put("op", "ADD").put("videoId", "x; rm -rf"), st));
        assertNull(Mapping.controlForJamOp(new JSONObject().put("op", "HOST"), st));
    }

    @Test
    public void removeAndMoveOnThePhoneUseBridgeIdsAndRevision() throws Exception {
        // « à suivre » du téléphone : c (items, rang 0) puis d (autoplay, rang 1)
        JSONObject rm = Mapping.bridgeCommand(new JSONObject().put("type", "remove").put("video_id", "ccccccccccc").put("index", 0), snapshot(true));
        assertEquals("REMOVE", rm.getString("op"));
        assertEquals("g:1", rm.getString("revision"));
        assertEquals(0, rm.getInt("lane"));
        assertEquals("13", rm.getString("item"));
        // rang périmé : première occurrence du titre, dans la voie des suggestions
        JSONObject rm2 = Mapping.bridgeCommand(new JSONObject().put("type", "remove").put("video_id", "ddddddddddd").put("index", 7), snapshot(true));
        assertEquals(1, rm2.getInt("lane"));
        assertEquals("21", rm2.getString("item"));
        // déplacement : cible ramenée dans la voie de départ (le pont ne déplace pas entre voies)
        JSONObject mv = Mapping.bridgeCommand(new JSONObject().put("type", "move").put("video_id", "ccccccccccc").put("index", 0).put("to", 1), snapshot(true));
        assertEquals("MOVE", mv.getString("op"));
        assertEquals("13", mv.getString("item"));
        assertEquals("13", mv.getString("target"));
        assertNull(Mapping.bridgeCommand(new JSONObject().put("type", "remove").put("video_id", "zzzzzzzzzzz"), snapshot(true)));
        assertNull("le titre en cours ne se retire pas",
            Mapping.bridgeCommand(new JSONObject().put("type", "remove").put("video_id", "bbbbbbbbbbb"), snapshot(true)));
        assertNull(Mapping.bridgeCommand(new JSONObject().put("type", "move").put("video_id", "ccccccccccc").put("to", 0), null));
    }

    @Test
    public void participantRemoveAndMoveBecomeServerControls() throws Exception {
        JSONObject st = serverSnapshot("the-kernel", true).getJSONObject("devices").getJSONObject("the-kernel").getJSONObject("state");
        JSONArray rows = Mapping.participantItems(st); // Control (en cours), Maniac, Control
        String maniac = rows.getJSONObject(1).getString("id");
        String control2 = rows.getJSONObject(2).getString("id");
        JSONObject rm = Mapping.controlForJamOp(new JSONObject().put("op", "REMOVE").put("item", control2).put("lane", 0), st);
        assertEquals("remove", rm.getString("action"));
        assertEquals("eeeeeeeeeee", rm.getString("video_id"));
        assertEquals(1, rm.getInt("index"));
        JSONObject mv = Mapping.controlForJamOp(new JSONObject().put("op", "MOVE").put("item", control2).put("target", maniac), st);
        assertEquals("move", mv.getString("action"));
        assertEquals(1, mv.getInt("index"));
        assertEquals(0, mv.getInt("to"));
        assertNull("le titre en cours ne se retire pas",
            Mapping.controlForJamOp(new JSONObject().put("op", "REMOVE").put("item", rows.getJSONObject(0).getString("id")), st));
        assertNull(Mapping.controlForJamOp(new JSONObject().put("op", "MOVE").put("item", maniac).put("target", "inconnu"), st));
        assertNull(Mapping.controlForJamOp(new JSONObject().put("op", "REMOVE").put("item", maniac), null));
    }

    @Test
    public void phoneQueueCarriesTitlesForOtherDevices() throws Exception {
        JSONObject s = Mapping.stateFromSnapshot(snapshot(true));
        JSONArray q = s.getJSONArray("queue");
        assertEquals("ccccccccccc", q.getJSONObject(0).getString("video_id"));
        assertEquals("Après", q.getJSONObject(0).getString("title"));
        assertEquals(2, q.length());
    }

    @Test
    public void jumpAndEnqueueOnThePhone() throws Exception {
        JSONObject jump = Mapping.bridgeCommand(new JSONObject().put("type", "jump").put("video_id", "ccccccccccc"), snapshot(true));
        assertEquals("PLAY", jump.getString("op"));
        assertEquals("13", jump.getString("item"));
        assertFalse(jump.has("playing"));
        assertNull(Mapping.bridgeCommand(new JSONObject().put("type", "jump").put("video_id", "zzzzzzzzzzz"), snapshot(true)));
        JSONObject add = Mapping.bridgeCommand(new JSONObject().put("type", "enqueue").put("video_id", "zzzzzzzzzzz").put("next", true), snapshot(true));
        assertEquals("PLAY_NEXT", add.getString("op"));
    }

    @Test
    public void mixContextFromPatchedBridge() throws Exception {
        JSONObject snap = snapshot(true);
        snap.getJSONObject("clock").put("playlistId", "RDAMVMbbbbbbbbbbb");
        assertEquals("RDAMVMbbbbbbbbbbb", Mapping.stateFromSnapshot(snap).getString("playlist_id"));
        snap.getJSONObject("clock").put("playlistId", "RD&list=evil");
        assertEquals("", Mapping.stateFromSnapshot(snap).getString("playlist_id"));
    }
}
