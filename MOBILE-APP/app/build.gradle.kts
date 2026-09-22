plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
    alias(libs.plugins.kotlin.compose)
    alias(libs.plugins.ksp)
}

android {
    namespace = "com.scanborn.ai"
    compileSdk = 36

    defaultConfig {
        applicationId = "com.scanborn.ai"
        minSdk = 24
        targetSdk = 36
        versionCode = 1
        versionName = "1.0"

        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"

        ndk {
            abiFilters += "arm64-v8a"
        }

        externalNativeBuild {
            cmake {
                cppFlags += "-std=c++17"
                arguments += listOf(
                    "-DANDROID_STL=c++_shared",
                    "-DANDROID_ARM_NEON=TRUE"
                )
            }
        }
    }

    ndkVersion = "28.2.13676358"

    // ── Tell Gradle where CMakeLists.txt lives ───────────────────────────────────
    externalNativeBuild {
        cmake {
            path = file("src/main/cpp/CMakeLists.txt")
            version = "3.22.1"
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro"
            )
        }
        debug {
            // Keep debug symbols for native crash debugging
            isJniDebuggable = true
        }
    }

    compileOptions {
        isCoreLibraryDesugaringEnabled = true
        sourceCompatibility = JavaVersion.VERSION_11
        targetCompatibility = JavaVersion.VERSION_11
    }

    kotlinOptions {
        jvmTarget = "11"
    }

    buildFeatures {
        compose = true
    }

    // ── Asset packaging: allow large files (the GGUF model is ~1GB) ─────────────
    // By default Android compresses assets. We must disable compression for
    // large binary files — compressed assets can't be memory-mapped efficiently.
    androidResources {
        noCompress += listOf("gguf", "bin", "model")
    }

    // ── Packaging: include native .so files, exclude duplicates ─────────────────
    packaging {
        resources {
            excludes += "/META-INF/{AL2.0,LGPL2.1}"
        }
        jniLibs {
            // Keep debug symbols in debug builds for crash analysis
            keepDebugSymbols += "**/*.so"
        }
    }
}

dependencies {
    implementation("androidx.webkit:webkit:1.12.1")
    implementation(libs.androidx.core.ktx)
    coreLibraryDesugaring(libs.desugar.jdk.libs)
    implementation(libs.androidx.lifecycle.runtime.ktx)
    implementation(libs.androidx.activity.compose)
    implementation(platform(libs.androidx.compose.bom))
    implementation(libs.androidx.ui)
    implementation(libs.androidx.ui.graphics)
    implementation(libs.androidx.ui.tooling.preview)
    implementation(libs.androidx.material3)
    implementation(libs.androidx.navigation.compose)
    implementation(libs.androidx.datastore.preferences)
    implementation(libs.androidx.lifecycle.viewmodel.compose)
    implementation(libs.androidx.material.icons.extended)
    implementation(libs.androidx.appcompat)
    implementation(libs.mlkit.text.recognition)
    implementation(libs.androidx.room.runtime)
    implementation(libs.androidx.room.ktx)
    ksp(libs.androidx.room.compiler)
    // Scan a space (Phase 5): ARCore depth capture and the upload to the orchestrator.
    implementation(libs.arcore)
    implementation(libs.okhttp)
    testImplementation(libs.junit)
    androidTestImplementation(libs.androidx.junit)
    androidTestImplementation(libs.androidx.espresso.core)
    androidTestImplementation(platform(libs.androidx.compose.bom))
    androidTestImplementation(libs.androidx.ui.test.junit4)
    debugImplementation(libs.androidx.ui.tooling)
    debugImplementation(libs.androidx.ui.test.manifest)
}

// Generated from the same source as the dashboard; no CDN or model weights required.
val bundleSharedViewer by tasks.registering(Exec::class) {
    workingDir(rootProject.projectDir.resolve("../dashboard"))
    commandLine(if (System.getProperty("os.name").startsWith("Windows")) "npm.cmd" else "npm",
        "run", "build:viewer")
    inputs.dir(rootProject.projectDir.resolve("../dashboard/shared-viewer"))
    inputs.file(rootProject.projectDir.resolve("../dashboard/viewer.html"))
    inputs.file(rootProject.projectDir.resolve("../dashboard/viewer.config.mjs"))
    outputs.dir(layout.buildDirectory.dir("generated/viewerAssets"))
}
android.sourceSets.getByName("main").assets.srcDir(layout.buildDirectory.dir("generated/viewerAssets"))
tasks.named("preBuild").configure { dependsOn(bundleSharedViewer) }

val checkSourceCompleteness by tasks.registering {
    doLast {
        val root = rootProject.projectDir.parentFile
        val source = fileTree("src") { include("**/*.kt", "**/*.java", "**/*.xml") }
        val process = ProcessBuilder("git", "check-ignore", "--no-index", "--stdin")
            .directory(root).redirectError(ProcessBuilder.Redirect.INHERIT).start()
        process.outputStream.bufferedWriter().use { writer ->
            source.forEach { writer.appendLine(it.relativeTo(root).invariantSeparatorsPath) }
        }
        val ignored = process.inputStream.bufferedReader().readText()
        val code = process.waitFor()
        check(code == 1 && ignored.isBlank()) { "Application sources are ignored or Git check failed: $ignored" }
        listOf("ThemePreference.kt", "OrchestratorPreference.kt", "library/LibraryRepository.kt",
            "library/LibraryDao.kt", "library/LibraryEntry.kt", "library/LibraryDatabase.kt").forEach {
            check(file("src/main/java/com/scanborn/ai/data/$it").exists()) { "Missing required source: $it" }
        }
    }
}
tasks.named("preBuild").configure { dependsOn(checkSourceCompleteness) }
