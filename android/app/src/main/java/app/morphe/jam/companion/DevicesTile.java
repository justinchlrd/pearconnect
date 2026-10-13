package app.morphe.jam.companion;

import android.app.PendingIntent;
import android.content.Intent;
import android.os.Build;
import android.service.quicksettings.TileService;

/** Tuile des réglages rapides « Appareils » : ouvre le sélecteur d'appareils Pear Connect. */
public final class DevicesTile extends TileService {
    @Override
    @SuppressWarnings("deprecation")
    @android.annotation.SuppressLint("StartActivityAndCollapseDeprecated") // branche < Android 14 uniquement
    public void onClick() {
        Intent i = new Intent(this, DevicesActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        if (Build.VERSION.SDK_INT >= 34) {
            startActivityAndCollapse(PendingIntent.getActivity(this, 20, i,
                PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT));
        } else {
            startActivityAndCollapse(i);
        }
    }
}
