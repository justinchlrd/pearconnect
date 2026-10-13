package app.morphe.jam.companion;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.io.UnsupportedEncodingException;
import java.net.HttpURLConnection;
import java.net.URL;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

/** Client HTTP du serveur Connect (voir server/connect_server.py). */
final class ConnectClient {
    static final class ConnectException extends Exception {
        final int status;

        ConnectException(String message, int status) {
            super(message);
            this.status = status;
        }
    }

    private final String base;
    private final String token;
    private final String device;

    ConnectClient(String base, String token, String device) {
        this.base = base.replaceAll("/+$", "");
        this.token = token;
        this.device = device;
    }

    private String node() {
        try {
            // surcharge (String, String) : la variante Charset n'existe qu'à partir d'Android 13
            return URLEncoder.encode(device, "UTF-8").replace("+", "%20");
        } catch (UnsupportedEncodingException e) {
            throw new IllegalStateException(e);
        }
    }

    private JSONObject req(String method, String path, JSONObject body, int timeoutMs)
            throws ConnectException {
        HttpURLConnection c = null;
        try {
            c = (HttpURLConnection) new URL(base + path).openConnection();
            c.setRequestMethod(method);
            c.setConnectTimeout(8000);
            c.setReadTimeout(timeoutMs);
            c.setUseCaches(false);
            c.setRequestProperty("Authorization", "Bearer " + token);
            byte[] data = null;
            if (body != null) {
                data = body.toString().getBytes(StandardCharsets.UTF_8);
                c.setDoOutput(true);
                c.setRequestProperty("Content-Type", "application/json");
                c.setFixedLengthStreamingMode(data.length);
            } else if (!"GET".equals(method)) {
                c.setDoOutput(true);
                c.setFixedLengthStreamingMode(0);
            }
            if (c.getDoOutput()) {
                try (OutputStream out = c.getOutputStream()) {
                    if (data != null) out.write(data);
                }
            }
            int status = c.getResponseCode();
            String text = read(status >= 400 ? c.getErrorStream() : c.getInputStream());
            if (status == 401) throw new ConnectException("jeton refusé par le serveur", status);
            if (status >= 400) {
                String detail = "";
                try {
                    detail = new JSONObject(text).optString("error", "");
                } catch (JSONException ignored) {
                    // corps non JSON : on garde le code seul
                }
                throw new ConnectException("HTTP " + status + (detail.isEmpty() ? "" : " : " + detail), status);
            }
            return text.isEmpty() ? new JSONObject() : new JSONObject(text);
        } catch (IOException | JSONException e) {
            throw new ConnectException("serveur injoignable : " + e.getMessage(), 0);
        } finally {
            if (c != null) c.disconnect();
        }
    }

    private static String read(InputStream in) throws IOException {
        if (in == null) return "";
        try (InputStream s = in) {
            ByteArrayOutputStream out = new ByteArrayOutputStream();
            byte[] buf = new byte[8192];
            int n;
            while ((n = s.read(buf)) > 0) {
                out.write(buf, 0, n);
                if (out.size() > 1 << 20) throw new IOException("réponse trop grande");
            }
            return out.toString(StandardCharsets.UTF_8.name());
        }
    }

    void putState(JSONObject state) throws ConnectException {
        try {
            req("PUT", "/v1/devices/" + node() + "/state",
                new JSONObject().put("kind", "android").put("state", state == null ? JSONObject.NULL : state),
                15000);
        } catch (JSONException e) {
            throw new ConnectException(e.getMessage(), 0);
        }
    }

    JSONArray takeCommands(int waitSeconds) throws ConnectException {
        JSONObject r = req("POST", "/v1/devices/" + node() + "/commands/take?wait=" + waitSeconds, null,
            (waitSeconds + 15) * 1000);
        JSONArray cmds = r.optJSONArray("commands");
        return cmds != null ? cmds : new JSONArray();
    }

    JSONObject snapshot() throws ConnectException {
        return req("GET", "/v1/snapshot", null, 15000);
    }

    /** Demande au serveur de faire reprendre ici la session en cours ; renvoie l'état transféré. */
    JSONObject transferHere() throws ConnectException {
        try {
            JSONObject r = req("POST", "/v1/transfer", new JSONObject().put("target", device), 15000);
            return r.optJSONObject("state");
        } catch (JSONException e) {
            throw new ConnectException(e.getMessage(), 0);
        }
    }

    /** Pilotage de l'appareil actif (POST /v1/control). */
    void control(JSONObject body) throws ConnectException {
        req("POST", "/v1/control", body, 15000);
    }

    /** Transfère la session en cours vers `target`. */
    void transfer(String target) throws ConnectException {
        try {
            req("POST", "/v1/transfer", new JSONObject().put("target", target), 15000);
        } catch (JSONException e) {
            throw new ConnectException(e.getMessage(), 0);
        }
    }

    boolean healthy() {
        try {
            return req("GET", "/healthz", null, 8000).optBoolean("ok");
        } catch (ConnectException e) {
            return false;
        }
    }
}
