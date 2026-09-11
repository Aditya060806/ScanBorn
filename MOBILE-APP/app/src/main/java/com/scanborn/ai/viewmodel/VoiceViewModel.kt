package com.scanborn.ai.viewmodel

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.scanborn.ai.ai.voice.SpeechController
import kotlinx.coroutines.channels.BufferOverflow
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.launch
import java.util.Locale

/**
 * Owns the speech engines for as long as the voice UI can reach them.
 *
 * Previously the SpeechRecognizer lived in an AppNavigation `remember {}` with a
 * DisposableEffect for teardown, which tied a microphone and a TTS engine to the lifetime of a
 * composable. A ViewModel is the right owner: it survives configuration changes without
 * re-creating the engines, and onCleared is a teardown hook that actually corresponds to the
 * screen going away for good.
 *
 * Completed transcripts are published as a [SharedFlow] rather than a callback so the
 * navigation layer can collect them in a LaunchedEffect and route them wherever it likes.
 */
class VoiceViewModel(app: Application) : AndroidViewModel(app) {

    private val controller = SpeechController(app.applicationContext)

    val state: StateFlow<SpeechController.VoiceUiState> = controller.state

    // extraBufferCapacity so a transcript emitted before anyone is collecting is not dropped
    // on the floor; DROP_OLDEST because only the most recent utterance is ever interesting.
    private val _transcripts = MutableSharedFlow<String>(
        replay = 0,
        extraBufferCapacity = 4,
        onBufferOverflow = BufferOverflow.DROP_OLDEST,
    )
    val transcripts: SharedFlow<String> = _transcripts.asSharedFlow()

    init {
        controller.onTranscript = { text ->
            viewModelScope.launch { _transcripts.emit(text) }
        }
    }

    fun startListening(locale: Locale = Locale.getDefault()) = controller.startListening(locale)

    fun stopListening() = controller.stopListening()

    /** Read a reply aloud. Safe to call before the TTS engine has initialised. */
    fun speak(text: String, locale: Locale = Locale.getDefault()) = controller.speak(text, locale)

    fun stopSpeaking() = controller.stopSpeaking()

    /** Cancel whichever direction is active — what the voice screen's stop button means. */
    fun stopAll() {
        controller.stopListening()
        controller.stopSpeaking()
    }

    override fun onCleared() {
        controller.release()
        super.onCleared()
    }
}
