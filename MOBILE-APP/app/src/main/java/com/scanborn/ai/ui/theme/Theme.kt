package com.scanborn.ai.ui.theme

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable

private val Dark = darkColorScheme(
    primary          = Blue500,
    onPrimary        = TextPrimary,
    background       = DarkBg,
    onBackground     = TextPrimary,
    surface          = DarkSurface,
    onSurface        = TextPrimary,
    surfaceVariant   = DarkSurfaceElevated,
    onSurfaceVariant = TextSecondary,
    outline          = DarkBorder,
    outlineVariant   = DarkBorder,
    secondary        = Blue400,
    onSecondary      = DarkBg,
    error            = ErrorRed,
    onError          = TextPrimary
)

private val Light = lightColorScheme(
    primary          = Blue500,
    onPrimary        = LightSurface,
    background       = LightBg,
    onBackground     = TextPrimaryLight,
    surface          = LightSurface,
    onSurface        = TextPrimaryLight,
    surfaceVariant   = LightSurfaceElevated,
    onSurfaceVariant = TextSecondaryLight,
    outline          = LightBorder,
    outlineVariant   = LightBorder,
    secondary        = Blue400,
    onSecondary      = LightSurface,
    error            = ErrorRed,
    onError          = LightSurface
)

@Composable
fun ScanBornTheme(darkTheme: Boolean = true, content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = if (darkTheme) Dark else Light,
        typography = Typography,
        content = content
    )
}
