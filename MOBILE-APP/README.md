# ScanBorn — AI Command Center

Premium AI assistant Android app with a futuristic dark dashboard interface inspired by scientific data visualization.

## Features

✅ **5 Complete Screens**
- Splash Screen — Animated logo with glow effects
- Dashboard — AI command center with stats, pulsing orb visualization, and quick actions
- Chat — Conversational UI with AI/user message bubbles
- Tools — Modular AI toolkit with expandable tool cards
- Settings — Theme toggle, permissions, app info

✅ **Dark/Light Theme System**
- Fully functional theme switching
- Persistent preference via DataStore
- Deep brown/black dark palette with amber/orange glow accents
- Material 3 design system

✅ **Modern Architecture**
- MVVM pattern
- Jetpack Compose UI
- Compose Navigation
- StateFlow + ViewModel
- DataStore Preferences

✅ **Animations**
- Splash fade-in with pulsing glow
- Smooth screen transitions
- Pulsing orb visualizations
- Live indicators
- Button ripple effects

✅ **Future-Ready**
- Architecture supports offline LLM integration (llama.cpp)
- Voice assistant placeholder
- Local AI model configuration UI
- Expandable tools system

## Tech Stack

- **Language:** Kotlin
- **UI:** Jetpack Compose
- **Navigation:** Compose Navigation
- **State:** StateFlow, ViewModel
- **Storage:** DataStore Preferences
- **Design:** Material 3
- **Min SDK:** 24 (Android 7.0)
- **Target SDK:** 35

## Project Structure

```
com.scanborn.ai/
├── MainActivity.kt
├── data/
│   └── ThemePreference.kt          # DataStore wrapper
├── viewmodel/
│   └── ThemeViewModel.kt           # Theme state management
├── ui/
│   ├── theme/
│   │   ├── Color.kt                # Dark brown/amber palette
│   │   ├── Theme.kt                # Material 3 color schemes
│   │   └── Type.kt                 # Typography
│   ├── components/
│   │   └── Components.kt           # Reusable UI components
│   ├── navigation/
│   │   └── AppNavigation.kt        # NavHost + bottom nav
│   └── screens/
│       ├── SplashScreen.kt
│       ├── DashboardScreen.kt
│       ├── ChatScreen.kt
│       ├── ToolsScreen.kt
│       └── SettingsScreen.kt
```

## Design Aesthetic

Inspired by dark scientific dashboards with:
- Deep brown/black backgrounds (#0D0A08)
- Amber/orange glow accents (#E8A020, #FF6B1A)
- Circular data visualizations
- Glowing orb effects
- Minimal but powerful UI
- Professional tech aesthetic

## Build & Run

1. Open project in Android Studio
2. Sync Gradle
3. Run on device/emulator (API 24+)

## Theme Toggle

Settings screen includes a functional dark/light mode toggle that:
- Switches instantly
- Persists across app restarts
- Uses DataStore for storage

## Future Integrations

The architecture is designed to support:
- Offline LLM (llama.cpp)
- Voice recognition
- Text-to-speech
- Local memory database
- Command system

---

**Version:** 1.0.0  
**Build:** Production Foundation  
**Engine:** ScanBorn-X1 Neural Core
