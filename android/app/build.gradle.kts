import java.util.Properties

plugins {
    id("com.android.application")
}

// Clé de signature stable (fichier hors dépôt) : permet de mettre à jour l'appli sans la désinstaller.
val signing = Properties().apply {
    val f = rootProject.file("signing.properties")
    if (f.exists()) f.inputStream().use { load(it) }
}

android {
    // Nom de paquet imposé : le patch Morphe « Jam queue sharing » ne rend visible à YouTube Music
    // que le compagnon « app.morphe.jam.companion » (balise <queries> du manifeste patché).
    namespace = "app.morphe.jam.companion"
    compileSdk = 35

    defaultConfig {
        applicationId = "app.morphe.jam.companion"
        minSdk = 26
        targetSdk = 35
        versionCode = 9
        versionName = "0.5.0"
    }

    buildFeatures {
        aidl = true
        buildConfig = true
    }

    signingConfigs {
        if (signing.isNotEmpty()) {
            create("pear") {
                storeFile = rootProject.file(signing.getProperty("storeFile"))
                storePassword = signing.getProperty("storePassword")
                keyAlias = signing.getProperty("keyAlias")
                keyPassword = signing.getProperty("keyPassword")
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            if (signing.isNotEmpty()) signingConfig = signingConfigs.getByName("pear")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    lint {
        abortOnError = true
        warningsAsErrors = false
        disable += setOf("SetTextI18n", "HardcodedText", "GradleDependency", "OldTargetApi")
    }
}

dependencies {
    testImplementation("junit:junit:4.13.2")
    testImplementation("org.json:json:20240303")
}
