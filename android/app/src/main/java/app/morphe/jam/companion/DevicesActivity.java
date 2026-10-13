package app.morphe.jam.companion;

import android.app.Activity;
import android.content.Intent;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.os.Bundle;
import android.view.Gravity;
import android.view.View;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;

import java.util.ArrayList;
import java.util.Collections;
import java.util.Iterator;
import java.util.List;

import org.json.JSONObject;

/**
 * Sélecteur d'appareils, façon Spotify Connect : ouvert depuis la notification, la tuile des
 * réglages rapides ou l'appli. Toucher un appareil y transfère la lecture ; toucher ce
 * téléphone la reprend ici (YouTube Music s'ouvre sur le titre, dans son mix si connu).
 */
public final class DevicesActivity extends Activity {
    private static final int ACCENT = Color.rgb(30, 215, 96);

    private Prefs prefs;
    private LinearLayout list;
    private TextView subtitle;

    @Override
    protected void onCreate(Bundle saved) {
        super.onCreate(saved);
        prefs = new Prefs(this);
        setTitle("Appareils");
        ScrollView scroll = new ScrollView(this);
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setPadding(dp(20), dp(8), dp(20), dp(16));
        scroll.addView(root);
        subtitle = new TextView(this);
        subtitle.setTextSize(14);
        subtitle.setPadding(0, 0, 0, dp(8));
        root.addView(subtitle);
        list = new LinearLayout(this);
        list.setOrientation(LinearLayout.VERTICAL);
        root.addView(list);
        setContentView(scroll);
        if (!prefs.configured()) {
            subtitle.setText("Pear Connect n'est pas configuré : ouvre l'appli.");
            return;
        }
        load();
    }

    private int dp(int v) {
        return Math.round(v * getResources().getDisplayMetrics().density);
    }

    private ConnectClient client() {
        return new ConnectClient(prefs.server(), prefs.token(), prefs.device());
    }

    private void load() {
        list.removeAllViews();
        list.addView(new ProgressBar(this));
        subtitle.setText("Recherche des appareils…");
        new Thread(() -> {
            try {
                JSONObject snap = client().snapshot();
                runOnUiThread(() -> render(snap));
            } catch (ConnectClient.ConnectException e) {
                runOnUiThread(() -> {
                    list.removeAllViews();
                    subtitle.setText("Serveur injoignable : " + e.getMessage());
                });
            }
        }, "pearconnect-appareils").start();
    }

    private void render(JSONObject snap) {
        list.removeAllViews();
        String me = prefs.device();
        String active = snap.isNull("active") ? "" : snap.optString("active", "");
        JSONObject devices = snap.optJSONObject("devices");
        JSONObject activeDev = devices != null ? devices.optJSONObject(active) : null;
        JSONObject activeState = activeDev != null && activeDev.optBoolean("online") ? activeDev.optJSONObject("state") : null;
        subtitle.setText(activeState == null ? "Aucune lecture en cours"
            : (active.equals(me) ? "Lecture sur ce téléphone" : "Lecture sur " + active)
                + " · " + activeState.optString("title"));
        if (devices == null) return;
        List<String> names = new ArrayList<>();
        for (Iterator<String> it = devices.keys(); it.hasNext(); ) names.add(it.next());
        Collections.sort(names);
        names.remove(me);
        names.add(0, me); // ce téléphone en premier
        for (String name : names) {
            JSONObject d = devices.optJSONObject(name);
            if (d == null || "web".equals(d.optString("kind"))) continue;
            boolean online = d.optBoolean("online") || name.equals(me);
            JSONObject st = d.optJSONObject("state");
            boolean isActive = name.equals(active) && st != null && d.optBoolean("online");
            String status = isActive ? (st.optBoolean("paused") ? "En pause · " : "En lecture · ") + st.optString("title")
                : name.equals(me) ? "Ce téléphone" : online ? "Disponible" : "Hors ligne";
            list.addView(row(name.equals(me) ? name + " (ce téléphone)" : name, status,
                "android".equals(d.optString("kind")) ? "📱" : "💻", isActive, online,
                isActive ? null : v -> transfer(name, name.equals(me))));
        }
    }

    private View row(String title, String status, String icon, boolean active, boolean online, View.OnClickListener onClick) {
        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.HORIZONTAL);
        row.setGravity(Gravity.CENTER_VERTICAL);
        row.setPadding(dp(12), dp(12), dp(12), dp(12));
        GradientDrawable bg = new GradientDrawable();
        bg.setCornerRadius(dp(10));
        bg.setColor(active ? Color.argb(40, 30, 215, 96) : Color.argb(18, 128, 128, 128));
        row.setBackground(bg);
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(-1, -2);
        lp.bottomMargin = dp(8);
        row.setLayoutParams(lp);
        TextView ic = new TextView(this);
        ic.setText(icon);
        ic.setTextSize(24);
        ic.setPadding(0, 0, dp(12), 0);
        row.addView(ic);
        LinearLayout texts = new LinearLayout(this);
        texts.setOrientation(LinearLayout.VERTICAL);
        TextView t = new TextView(this);
        t.setText(title);
        t.setTextSize(16);
        t.setTypeface(Typeface.DEFAULT_BOLD);
        TextView s = new TextView(this);
        s.setText(status);
        s.setTextSize(13);
        if (active) {
            t.setTextColor(ACCENT);
            s.setTextColor(ACCENT);
        }
        texts.addView(t);
        texts.addView(s);
        row.addView(texts);
        row.setAlpha(online ? 1f : 0.45f);
        if (onClick != null && online) {
            row.setClickable(true);
            row.setOnClickListener(onClick);
            row.setContentDescription("Transférer la lecture vers " + title);
        }
        return row;
    }

    private void transfer(String target, boolean here) {
        if (here) {
            // geste de l'utilisateur : Android autorise l'ouverture de YouTube Music depuis ici
            startActivity(new Intent(this, ResumeActivity.class).setAction(ResumeActivity.ACTION_TRANSFER_HERE));
            finish();
            return;
        }
        subtitle.setText("Transfert vers " + target + "…");
        new Thread(() -> {
            String error = null;
            try {
                client().transfer(target);
            } catch (ConnectClient.ConnectException e) {
                error = e.getMessage();
            }
            final String err = error;
            runOnUiThread(() -> {
                if (err != null) {
                    Toast.makeText(this, "Transfert impossible : " + err, Toast.LENGTH_LONG).show();
                    load();
                } else {
                    Toast.makeText(this, "Lecture transférée vers " + target, Toast.LENGTH_SHORT).show();
                    finish();
                }
            });
        }, "pearconnect-transfert").start();
    }
}
