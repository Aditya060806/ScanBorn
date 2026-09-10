package com.scanborn.ai.viewmodel

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.scanborn.ai.data.ThemePreference
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch

class ThemeViewModel(app: Application) : AndroidViewModel(app) {
    private val pref = ThemePreference(app)

    val isDarkTheme = pref.isDarkTheme.stateIn(
        viewModelScope,
        SharingStarted.Eagerly,
        true
    )

    fun toggleTheme() {
        viewModelScope.launch {
            pref.setDarkTheme(!isDarkTheme.value)
        }
    }
}
