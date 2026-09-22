package com.scanborn.ai

import android.content.ComponentCallbacks2
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.viewModels
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.core.view.WindowCompat
import androidx.lifecycle.lifecycleScope
import com.scanborn.ai.ai.repository.AIRepository
import com.scanborn.ai.ui.navigation.AppNavigation
import com.scanborn.ai.ui.theme.ScanBornTheme
import com.scanborn.ai.viewmodel.ThemeViewModel
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch

class MainActivity : ComponentActivity() {
    private val themeViewModel: ThemeViewModel by viewModels()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        // Allow content to draw behind system bars
        WindowCompat.setDecorFitsSystemWindows(window, false)
        enableEdgeToEdge()
        setContent {
            val isDarkTheme by themeViewModel.isDarkTheme.collectAsState()
            ScanBornTheme(darkTheme = isDarkTheme) {
                AppNavigation(
                    isDarkTheme = isDarkTheme,
                    onToggleTheme = themeViewModel::toggleTheme
                )
            }
        }
    }

    /**
     * A loaded model holds ~1.15 GiB Android cannot reclaim (docs/hardware/s24fe.md), which makes
     * the app an early target for the background killer. Give it back once the app is in the
     * background or memory runs low; the next AI request loads it again.
     */
    override fun onTrimMemory(level: Int) {
        super.onTrimMemory(level)
        if (level >= ComponentCallbacks2.TRIM_MEMORY_BACKGROUND) {
            lifecycleScope.launch(Dispatchers.IO) {
                AIRepository.getInstance(applicationContext).release()
            }
        }
    }
}
