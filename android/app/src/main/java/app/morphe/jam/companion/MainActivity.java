package app.morphe.jam.companion;

import android.Manifest;
import android.app.Activity;
import android.app.AlertDialog;
import android.content.ComponentName;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.graphics.Typeface;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.PowerManager;
import android.provider.Settings;
import android.text.InputType;
import android.view.Gravity;
import android.view.View;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;

import java.util.regex.Pattern;

/**
 * Écran de réglages et d'état. Reçoit aussi l'appairage lancé depuis YouTube Music patché
 * (action app.morphe.jam.PAIR avec le jeton local « cap »).
 */
public final class MainActivity extends Activity {
    static final String ACTION_PAIR = "app.morphe.jam.PAIR";
    private static final Pattern CAP = Pattern.compile("^[0-9a-f]{64}$");
    private static final Pattern DEVICE = Pattern.compile("^[A-Za-z0-9_.-]{1,64}$");

    private Prefs prefs;
    private TextView status;
    private EditText server;
    private EditText device;
    private EditText token;
    private final Handler handler = new Handler(Looper.getMainLooper());
    private final Runnable refresh = new Runnable() {
        @Override
        public void run() {
            renderStatus();
            handler.postDelayed(this, 1500);
        }
    };

    @Override
    protected void onCreate(Bundle saved) {
        super.onCreate(saved);
        prefs = new Prefs(this);
        if (ACTION_PAIR.equals(getIntent().getAction())) {
            handlePair();
            return;
        }
        buildUi();
        if (Build.VERSION.SDK_INT >= 33
            && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[] {Manifest.permission.POST_NOTIFICATIONS}, 1);
        }
    }

    @Override
    protected void onResume() {
        super.onResume();
        if (status != null) handler.post(refresh);
    }

    @Override
    protected void onPause() {
        handler.removeCallbacks(refresh);
        super.onPause();
    }

    // -- appairage demandé par YouTube Music -------------------------------------------------
    private void handlePair() {
        String cap = getIntent().getStringExtra("cap");
        String caller = getCallingPackage();
        if (cap == null || !CAP.matcher(cap).matches() || caller == null) {
            Toast.makeText(this, "Demande d'appairage invalide", Toast.LENGTH_LONG).show();
            setResult(RESULT_CANCELED);
            finish();
            return;
        }
        boolean hasBridge;
        try {
            MusicBridge.findBridge(this, caller);
            hasBridge = true;
        } catch (MusicBridge.BridgeException e) {
            hasBridge = false;
        }
        String label = caller;
        try {
            label = getPackageManager().getApplicationLabel(getPackageManager().getApplicationInfo(caller, 0))
                + " (" + caller + ")";
        } catch (PackageManager.NameNotFoundException ignored) {
            // libellé introuvable : on affiche le nom de paquet
        }
        String message = "Autoriser Pear Connect à lire et piloter la lecture de :\n\n" + label
            + (hasBridge ? "" : "\n\n⚠ Le pont Jam n'a pas été trouvé dans cette appli.");
        new AlertDialog.Builder(this)
            .setTitle("Appairer avec YouTube Music ?")
            .setMessage(message)
            .setCancelable(false)
            .setPositiveButton("Appairer", (d, w) -> {
                prefs.savePairing(caller, cap);
                setResult(RESULT_OK);
                Toast.makeText(this, "Appairé avec " + caller, Toast.LENGTH_SHORT).show();
                if (prefs.enabled() && prefs.configured()) JamService.start(this);
                finish();
            })
            .setNegativeButton("Refuser", (d, w) -> {
                setResult(RESULT_CANCELED);
                finish();
            })
            .show();
    }

    // -- interface -----------------------------------------------------------------------------
    private int dp(int v) {
        return Math.round(v * getResources().getDisplayMetrics().density);
    }

    private TextView text(LinearLayout root, String s, int sizeSp, boolean bold) {
        TextView t = new TextView(this);
        t.setText(s);
        t.setTextSize(sizeSp);
        if (bold) t.setTypeface(Typeface.DEFAULT_BOLD);
        t.setPadding(0, dp(8), 0, dp(4));
        root.addView(t);
        return t;
    }

    private EditText field(LinearLayout root, String label, String value, boolean secret) {
        text(root, label, 14, true);
        EditText e = new EditText(this);
        e.setText(value);
        e.setSingleLine(true);
        e.setInputType(secret
            ? InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD
            : InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        root.addView(e);
        return e;
    }

    private Button button(LinearLayout root, String label, View.OnClickListener onClick) {
        Button b = new Button(this);
        b.setText(label);
        b.setAllCaps(false);
        b.setOnClickListener(onClick);
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(-1, -2);
        lp.topMargin = dp(6);
        root.addView(b, lp);
        return b;
    }

    private void buildUi() {
        ScrollView scroll = new ScrollView(this);
        scroll.setFitsSystemWindows(true);
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setPadding(dp(20), dp(16), dp(20), dp(24));
        scroll.addView(root);

        TextView title = text(root, "Pear Connect", 26, true);
        title.setGravity(Gravity.START);
        status = text(root, "", 15, false);

        button(root, "Appareils : choisir où jouer", v -> startActivity(new Intent(this, DevicesActivity.class)));
        text(root, "Serveur", 20, true);
        server = field(root, "Adresse du serveur Connect", prefs.server(), false);
        device = field(root, "Nom de cet appareil", prefs.device(), false);
        token = field(root, "Jeton de l'appareil", prefs.token(), true);
        button(root, "Enregistrer et démarrer", v -> saveAndStart());
        button(root, "Arrêter", v -> {
            prefs.setEnabled(false);
            JamService.stop(this);
        });

        text(root, "YouTube Music (Morphe)", 20, true);
        text(root, "1. Réglages Morphe de YouTube Music : active « Activer le partage de la file d'attente Jam », "
            + "puis redémarre YouTube Music.\n"
            + "2. Même endroit, « Paquet Jam Layer » doit valoir " + getPackageName() + " (valeur par défaut).\n"
            + "3. Dans le lecteur, ouvre « Écouter ensemble » → « Configurer Jam Layer », puis accepte l'appairage ici.",
            14, false);
        android.widget.CheckBox remote = new android.widget.CheckBox(this);
        remote.setText("Afficher dans YouTube Music ce qui joue sur mes autres appareils (barre « Écoute "
            + "ensemble » : file, progression, lecture/pause, suivant, « Lire ici »)");
        remote.setChecked(prefs.remoteView());
        remote.setOnCheckedChangeListener((v, on) -> prefs.setRemoteView(on));
        root.addView(remote);

        text(root, "Autorisations recommandées", 20, true);
        button(root, "Ouvrir YouTube Music automatiquement lors d'un transfert", v ->
            startActivity(new Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION,
                Uri.parse("package:" + getPackageName()))));
        button(root, "Ne pas restreindre la batterie (connexion permanente)", v -> requestBattery());
        button(root, "Ouvrir la télécommande web", v ->
            startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(prefs.server()))));
        button(root, "Rechercher une mise à jour (version " + BuildConfig.VERSION_NAME + ")", v -> checkUpdate());
        setContentView(scroll);
    }

    /** Compare la version de l'appli à versions.json du serveur et propose l'APK s'il est plus récent. */
    private void checkUpdate() {
        if (!JamService.agentRunning()) {
            Toast.makeText(this, "Démarre d'abord l'agent (Enregistrer et démarrer)", Toast.LENGTH_SHORT).show();
            return;
        }
        Toast.makeText(this, "Vérification…", Toast.LENGTH_SHORT).show();
        new Thread(() -> {
            org.json.JSONObject update = JamService.checkUpdateNow();
            Agent.Status s = JamService.currentStatus();
            runOnUiThread(() -> {
                if (update == null) {
                    String server = s != null ? s.server : "";
                    Toast.makeText(this, "connecté".equals(server)
                        ? "Pear Connect " + BuildConfig.VERSION_NAME + " est à jour"
                        : "Vérification impossible : serveur " + server, Toast.LENGTH_LONG).show();
                    return;
                }
                String name = update.optString("version_name");
                String url = update.optString("url");
                new AlertDialog.Builder(this)
                    .setTitle("Pear Connect " + name + " disponible")
                    .setMessage("Installée : " + BuildConfig.VERSION_NAME + ".\nL'APK s'installe par-dessus (même signature), "
                        + "les réglages sont conservés.")
                    .setPositiveButton("Télécharger", (d, w) ->
                        startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(url))))
                    .setNegativeButton("Plus tard", null)
                    .show();
            });
        }, "pearconnect-maj").start();
    }

    @android.annotation.SuppressLint("BatteryLife") // appli personnelle, hors Play Store
    private void requestBattery() {
        PowerManager pm = getSystemService(PowerManager.class);
        if (pm.isIgnoringBatteryOptimizations(getPackageName())) {
            Toast.makeText(this, "Déjà autorisé", Toast.LENGTH_SHORT).show();
            return;
        }
        startActivity(new Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS,
            Uri.parse("package:" + getPackageName())));
    }

    private void saveAndStart() {
        String s = server.getText().toString().trim();
        String d = device.getText().toString().trim();
        String t = token.getText().toString().trim();
        if (!s.startsWith("http://") && !s.startsWith("https://")) {
            server.setError("Adresse en http:// ou https://");
            return;
        }
        if (!DEVICE.matcher(d).matches()) {
            device.setError("Lettres, chiffres, . _ - uniquement");
            return;
        }
        if (t.length() < 16) {
            token.setError("Jeton manquant");
            return;
        }
        prefs.saveConnect(s, d, t);
        prefs.setEnabled(true);
        JamService.start(this);
        Toast.makeText(this, "Démarré", Toast.LENGTH_SHORT).show();
    }

    private void renderStatus() {
        StringBuilder b = new StringBuilder();
        b.append("Version ").append(BuildConfig.VERSION_NAME);
        Agent.Status st = JamService.currentStatus();
        if (st != null && !st.updateName.isEmpty()) {
            b.append(" — mise à jour ").append(st.updateName).append(" disponible");
        }
        b.append('\n');
        b.append(prefs.paired()
            ? "✔ Appairé avec " + prefs.musicPackage()
            : "✘ Pas encore appairé avec YouTube Music (voir plus bas)");
        Agent.Status s = JamService.currentStatus();
        if (!JamService.agentRunning() || s == null) {
            b.append("\n✘ Agent arrêté");
        } else {
            b.append("\nServeur : ").append(s.server);
            b.append("\nYouTube Music : ").append(s.bridge);
            if (!s.local.isEmpty()) b.append("\nIci : ").append(s.local);
            if (s.hasSession) {
                b.append("\nSession : ").append(s.activeIsMe ? "ce téléphone" : s.activeDevice)
                    .append(" — ").append(s.activeTitle);
            }
        }
        if (!Settings.canDrawOverlays(this)) {
            b.append("\nℹ Sans l'autorisation « par-dessus les autres applis », une reprise demandera de toucher une notification.");
        }
        status.setText(b.toString());
    }
}
