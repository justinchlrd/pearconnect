/*
 * Pear Connect : contexte de lecture (mix, playlist, album) exposé au compagnon.
 *
 * See the included NOTICE file for GPLv3 Section 7 terms that apply to this code.
 */

package app.morphe.extension.music.patches.jam;

/**
 * Dernier couple (titre, contexte) demandé au lecteur. Le pont Jam l'ajoute à son horloge
 * pour que le compagnon puisse rouvrir le titre DANS son mix sur un autre appareil.
 */
public final class JamContext {

    private static volatile String videoId = "";
    private static volatile String playlistId = "";

    private JamContext() {
    }

    /** Injected into the player parameter builder (video id, playlist id, playlist index). */
    @SuppressWarnings("unused")
    public static void newPlayerResponse(String video, String playlist, int playlistIndex) {
        if (video == null || video.isEmpty()) return;
        videoId = video;
        playlistId = playlist == null ? "" : playlist;
    }

    /** Contexte du titre en cours, ou chaîne vide s'il est inconnu ou ne correspond pas. */
    static String playlistFor(String video) {
        String id = playlistId;
        return video != null && video.equals(videoId) && id.matches("[A-Za-z0-9_-]{2,128}") ? id : "";
    }
}
