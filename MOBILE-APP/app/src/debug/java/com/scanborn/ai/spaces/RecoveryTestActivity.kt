package com.scanborn.ai.spaces

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.createSavedStateHandle
import androidx.lifecycle.viewmodel.CreationExtras
import com.scanborn.ai.ui.screens.SpacesScreen
import kotlinx.coroutines.flow.flowOf
import java.io.File

/** Non-exported debug-only host: no model initialization, camera or external server defaults. */
class RecoveryTestActivity : ComponentActivity() {
    lateinit var spaces: SpacesViewModel
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val token = requireNotNull(intent.getStringExtra("test_root"))
        require(token.matches(Regex("[a-f0-9-]{36}")))
        val url = requireNotNull(intent.getStringExtra("test_server"))
        require(url.startsWith("http://127.0.0.1:"))
        spaces = ViewModelProvider(this, object : ViewModelProvider.Factory {
            override fun <T : ViewModel> create(modelClass: Class<T>, extras: CreationExtras): T {
                @Suppress("UNCHECKED_CAST")
                return SpacesViewModel(application, extras.createSavedStateHandle(),
                    File(cacheDir, "screen-recovery-$token"), flowOf(url)) as T
            }
        })[SpacesViewModel::class.java]
        setContent { androidx.compose.material3.MaterialTheme { SpacesScreen(onScan = {}, vm = spaces) } }
    }
}
