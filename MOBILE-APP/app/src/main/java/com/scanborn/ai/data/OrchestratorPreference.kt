package com.scanborn.ai.data

import android.content.Context
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map

/**
 * The orchestrator the app uploads scans to, set in Settings. Replaces the old capture app's
 * build-time `BuildConfig.ORCHESTRATOR_URL` (Phase 5 plan, decision 10).
 */
class OrchestratorPreference(private val context: Context) {

    companion object {
        val URL_KEY = stringPreferencesKey("orchestrator_url")
        val VERIFIED_KEY = stringPreferencesKey("orchestrator_verified_url")
    }

    val url: Flow<String> = context.dataStore.data.map { it[URL_KEY] ?: "" }

    /** The URL that last answered Test connection. Scanning needs it to equal [url]. */
    val verifiedUrl: Flow<String> = context.dataStore.data.map { it[VERIFIED_KEY] ?: "" }

    suspend fun setUrl(url: String) {
        context.dataStore.edit {
            it[URL_KEY] = url
            if (it[VERIFIED_KEY] != url) it.remove(VERIFIED_KEY)
        }
    }

    suspend fun markVerified(url: String) {
        context.dataStore.edit { it[VERIFIED_KEY] = url }
    }
}
