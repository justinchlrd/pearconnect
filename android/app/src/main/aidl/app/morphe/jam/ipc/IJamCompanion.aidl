// Interface que YouTube Music patché appelle sur l'appli compagnon.
package app.morphe.jam.ipc;

interface IJamCompanion {
    String call(String capability, String request);
}
