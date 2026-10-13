// Interface exposée par YouTube Music patché (patch Morphe « Jam queue sharing »).
// Le nom de paquet et la signature doivent rester identiques à ceux du patch.
package app.morphe.jam.ipc;

interface IJamBridge {
    String call(String capability, String request);
}
