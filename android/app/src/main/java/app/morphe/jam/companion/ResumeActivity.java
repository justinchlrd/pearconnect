package app.morphe.jam.companion;

import android.app.Activity;
import android.content.ActivityNotFoundException;
import android.os.Bundle;
import android.widget.Toast;

import org.json.JSONObject;

/**
 * Trampoline invisible, ouvert par un geste de l'utilisateur (notification) : Android n'autorise
 * l'ouverture de YouTube Music depuis l'arrière-plan qu'à ce prix.
 *  - ACTION_TRANSFER_HERE : demande au serveur de faire reprendre ici, puis ouvre le titre ;
 *  - ACTION_OPEN : ouvre directement le titre d'une reprise reçue.
 * Le recalage de la position est fait ensuite par l'agent.
 */
public final class ResumeActivity extends Activity {
    static final String ACTION_TRANSFER_HERE = "app.pearconnect.companion.TRANSFER_HERE";
    static final String ACTION_OPEN = "app.pearconnect.companion.OPEN";
    static final String EXTRA_URL = "url";
    static final String EXTRA_VIDEO = "video";

    @Override
    protected void onCreate(Bundle saved) {
        super.onCreate(saved);
        JamService.cancelResumeNotification(this);
        Prefs prefs = new Prefs(this);
        String action = getIntent().getAction();
        if (ACTION_OPEN.equals(action)) {
            open(getIntent().getStringExtra(EXTRA_URL), getIntent().getStringExtra(EXTRA_VIDEO), prefs);
            finish();
            return;
        }
        if (!ACTION_TRANSFER_HERE.equals(action) || !prefs.configured()) {
            finish();
            return;
        }
        new Thread(() -> {
            String error = null;
            JSONObject state = null;
            try {
                state = new ConnectClient(prefs.server(), prefs.token(), prefs.device()).transferHere();
            } catch (ConnectClient.ConnectException e) {
                error = e.getMessage();
            }
            final String err = error;
            final JSONObject st = state;
            runOnUiThread(() -> {
                if (err != null || st == null) {
                    Toast.makeText(this, "Transfert impossible : " + (err != null ? err : "aucune session"),
                        Toast.LENGTH_LONG).show();
                } else {
                    open(Mapping.musicUrl(st), st.optString("video_id"), prefs);
                }
                finish();
            });
        }, "pearconnect-transfert").start();
    }

    private void open(String url, String video, Prefs prefs) {
        if (url == null) return;
        try {
            Agent.markLaunched(video == null ? "" : video);
            startActivity(Agent.musicIntent(url, prefs.musicPackage()));
        } catch (ActivityNotFoundException e) {
            Toast.makeText(this, "YouTube Music introuvable", Toast.LENGTH_LONG).show();
        }
    }
}
