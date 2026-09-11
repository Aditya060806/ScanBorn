package com.scanborn.ai.ai.voice

import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import android.util.Log
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import java.util.Locale

/**
 * Everything the voice screen needs: speech in, speech out, and a state anyone can observe.
 *
 * This used to be ~40 lines inlined into AppNavigation's composable body, which is why none
 * of it reached the UI. `isListening` on VoiceScreen was derived from the *inference* state,
 * so the mic looked dead from the moment it opened until the model started replying — the one
 * window where a user most needs to see that they are being heard. Recognition state and
 * inference state are different things and now have different homes.
 *
 * [state] is the single source of truth. Recognition callbacks arrive on the main thread and
 * TTS callbacks on a binder thread; MutableStateFlow tolerates both.
 */
class SpeechController(context: Context) {

    data class VoiceUiState(
        /** Mic is open. True from onReadyForSpeech until a result or an error. */
        val listening: Boolean = false,
        /** Live guess while the user is still talking. */
        val partial: String = "",
        /** Last completed transcript. */
        val heard: String = "",
        /** TTS is producing audio. */
        val speaking: Boolean = false,
        /** Human-readable failure, or null. */
        val error: String? = null,
    ) {
        /** What the transcript card should show: the live guess, else the final text. */
        val transcript: String get() = partial.ifEmpty { heard }
    }

    companion object {
        private const val TAG = "SpeechController"
        private const val UTTERANCE_ID = "scanborn-reply"

        /** Error codes mapped to something a user can act on. */
        private fun describe(code: Int): String = when (code) {
            SpeechRecognizer.ERROR_AUDIO -> "Microphone unavailable"
            SpeechRecognizer.ERROR_CLIENT -> "Recogniser was interrupted"
            SpeechRecognizer.ERROR_INSUFFICIENT_PERMISSIONS -> "Microphone permission denied"
            SpeechRecognizer.ERROR_NETWORK -> "Speech recognition needs a network"
            SpeechRecognizer.ERROR_NETWORK_TIMEOUT -> "Speech recognition timed out"
            SpeechRecognizer.ERROR_NO_MATCH -> "Didn't catch that"
            SpeechRecognizer.ERROR_RECOGNIZER_BUSY -> "Recogniser is busy"
            SpeechRecognizer.ERROR_SERVER -> "Speech service error"
            SpeechRecognizer.ERROR_SPEECH_TIMEOUT -> "Didn't hear anything"
            else -> "Speech recognition failed ($code)"
        }
    }

    private val _state = MutableStateFlow(VoiceUiState())
    val state: StateFlow<VoiceUiState> = _state.asStateFlow()

    /** Set by the owner; called once per completed utterance. */
    var onTranscript: ((String) -> Unit)? = null

    private val available = SpeechRecognizer.isRecognitionAvailable(context)

    private val recognizer: SpeechRecognizer? =
        if (available) SpeechRecognizer.createSpeechRecognizer(context) else null

    // TTS init is asynchronous, so speak() may be called before the engine is usable.
    // Queue at most the latest utterance rather than dropping it — a reply that arrives
    // during a cold start is exactly the one a user is waiting to hear.
    private var ttsReady = false
    private var pending: String? = null

    private val tts = TextToSpeech(context) { status ->
        ttsReady = status == TextToSpeech.SUCCESS
        if (!ttsReady) {
            Log.w(TAG, "TextToSpeech unavailable (status=$status)")
            return@TextToSpeech
        }
        pending?.let { pending = null; speak(it) }
    }

    init {
        if (!available) {
            _state.value = _state.value.copy(error = "Speech recognition not available")
        }
        recognizer?.setRecognitionListener(listener)
        tts.setOnUtteranceProgressListener(object : UtteranceProgressListener() {
            override fun onStart(utteranceId: String?) {
                _state.value = _state.value.copy(speaking = true)
            }

            override fun onDone(utteranceId: String?) {
                _state.value = _state.value.copy(speaking = false)
            }

            @Deprecated("Required by the abstract class; the int overload is deprecated.")
            override fun onError(utteranceId: String?) {
                _state.value = _state.value.copy(speaking = false)
            }
        })
    }

    private val listener = object : RecognitionListener {
        override fun onReadyForSpeech(params: Bundle?) {
            // The moment the mic is actually open. This is what the UI needs in order to
            // stop claiming "Tap mic to speak" while already recording.
            _state.value = _state.value.copy(listening = true, partial = "", error = null)
        }

        override fun onBeginningOfSpeech() {}
        override fun onRmsChanged(rmsdB: Float) {}
        override fun onBufferReceived(buffer: ByteArray?) {}

        override fun onEndOfSpeech() {
            // Audio has stopped but results have not arrived. Keep `listening` true so the
            // waveform does not flicker off in the gap before onResults.
        }

        override fun onError(error: Int) {
            _state.value = _state.value.copy(
                listening = false, partial = "", error = describe(error))
            Log.w(TAG, "recognition error: ${describe(error)}")
        }

        override fun onResults(results: Bundle?) {
            val text = results
                ?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)
                ?.firstOrNull()
                .orEmpty()
                .trim()
            _state.value = _state.value.copy(listening = false, partial = "", heard = text)
            if (text.isNotEmpty()) onTranscript?.invoke(text)
        }

        override fun onPartialResults(partialResults: Bundle?) {
            // EXTRA_PARTIAL_RESULTS was already being requested while this override was
            // empty, so the phone was computing live transcripts and throwing them away.
            val text = partialResults
                ?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)
                ?.firstOrNull()
                .orEmpty()
            if (text.isNotEmpty()) _state.value = _state.value.copy(partial = text)
        }

        override fun onEvent(eventType: Int, params: Bundle?) {}
    }

    /**
     * Open the mic.
     *
     * @param locale language to recognise. Defaults to the device's, which is what makes
     *        Hindi and Tamil work without a setting — the recogniser follows the phone.
     */
    fun startListening(locale: Locale = Locale.getDefault()) {
        val sr = recognizer ?: return
        val intent = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
            putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL,
                RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
            putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true)
            putExtra(RecognizerIntent.EXTRA_LANGUAGE, locale.toLanguageTag())
        }
        _state.value = _state.value.copy(partial = "", heard = "", error = null)
        runCatching { sr.startListening(intent) }.onFailure {
            Log.e(TAG, "startListening failed", it)
            _state.value = _state.value.copy(listening = false, error = "Could not open the mic")
        }
    }

    fun stopListening() {
        recognizer?.let { runCatching { it.cancel() } }
        _state.value = _state.value.copy(listening = false, partial = "")
    }

    /** Speak [text]. Queues if the engine has not finished initialising. */
    fun speak(text: String, locale: Locale = Locale.getDefault()) {
        if (text.isBlank()) return
        if (!ttsReady) { pending = text; return }
        // setLanguage per utterance: a Hindi question deserves a Hindi answer, and the
        // language can change between turns. Called as a method rather than via the
        // `language` property, because the property form also binds the deprecated getter.
        val supported = tts.isLanguageAvailable(locale) >= TextToSpeech.LANG_AVAILABLE
        tts.setLanguage(if (supported) locale else Locale.US)
        tts.speak(text, TextToSpeech.QUEUE_FLUSH, null, UTTERANCE_ID)
    }

    fun stopSpeaking() {
        runCatching { tts.stop() }
        _state.value = _state.value.copy(speaking = false)
    }

    /** Release both engines. Must be called from the owner's teardown. */
    fun release() {
        onTranscript = null
        runCatching { recognizer?.destroy() }
        runCatching { tts.stop(); tts.shutdown() }
        _state.value = VoiceUiState()
    }
}
