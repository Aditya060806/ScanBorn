package com.scanborn.ai.viewmodel

import android.app.Application
import android.net.Uri
import android.util.Log
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.scanborn.ai.ai.repository.AIRepository
import com.scanborn.ai.ai.state.AIInferenceState
import com.scanborn.ai.data.library.EntryType
import com.scanborn.ai.data.library.LibraryRepository
import com.scanborn.ai.ocr.AiTextProcessor
import com.scanborn.ai.ocr.OcrTextExtractor
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

class QuizViewModel(app: Application) : AndroidViewModel(app) {

    companion object {
        private const val TAG = "QuizViewModel"
        // Quiz prompt is short so we can allow slightly more input text
        // while still staying under 1000 total tokens.
        // Prompt prefix ≈ 35 tokens + overhead 134 = 169 tokens fixed.
        // 800 chars / 4 = 200 tokens for text → total ≈ 369 tokens. Well under 1000.
        private const val QUIZ_PROMPT =
            "Generate exactly 5 multiple choice questions from the text below. " +
            "For each question: write the question, then 4 options labeled A B C D, " +
            "then state the correct answer. Be concise.\n\nText:"
    }

    private val repository  = AIRepository.getInstance(app)
    private val extractor    = OcrTextExtractor()
    private val libraryRepo  = LibraryRepository.getInstance(app)

    private val _showSavedBanner = MutableStateFlow(false)
    val showSavedBanner: StateFlow<Boolean> = _showSavedBanner.asStateFlow()

    val aiState: StateFlow<AIInferenceState> = repository.aiState

    private val _uiState   = MutableStateFlow<QuizUiState>(QuizUiState.Idle)
    val uiState: StateFlow<QuizUiState> = _uiState.asStateFlow()

    private val _quizText  = MutableStateFlow("")
    val quizText: StateFlow<String> = _quizText.asStateFlow()

    private val _sourceText = MutableStateFlow("")
    val sourceText: StateFlow<String> = _sourceText.asStateFlow()

    private var job: Job? = null
    private var userStopped = false

    init { viewModelScope.launch(Dispatchers.IO) { repository.initialize() } }

    /** Generate a quiz from manually pasted or pre-extracted text. */
    fun generateFromText(text: String) {
        if (text.isBlank()) return
        startGeneration(text)
    }

    /** Generate a quiz from an image via OCR. */
    fun generateFromImage(uri: Uri) {
        if (_uiState.value is QuizUiState.Extracting ||
            _uiState.value is QuizUiState.Generating) return
        job?.cancel()
        _quizText.value  = ""
        _sourceText.value = ""
        userStopped       = false

        job = viewModelScope.launch(Dispatchers.IO) {
            _uiState.value = QuizUiState.Extracting
            extractor.extract(getApplication(), uri).fold(
                onSuccess = { (text, _) -> startGeneration(text) },
                onFailure = { e ->
                    Log.e(TAG, "OCR failed", e)
                    _uiState.value = QuizUiState.Error(e.message ?: "Failed to read image")
                }
            )
        }
    }

    private fun startGeneration(text: String) {
        val safe = if (text.length > AiTextProcessor.MAX_INPUT_CHARS)
            text.take(AiTextProcessor.MAX_INPUT_CHARS) else text
        _sourceText.value = safe
        _quizText.value   = ""
        userStopped       = false

        // If called from generateFromImage the job is already running — launch nested
        val launch: suspend () -> Unit = {
            _uiState.value = QuizUiState.Generating
            val prompt = "$QUIZ_PROMPT\n$safe"
            Log.i(TAG, "Quiz prompt: ${prompt.length} chars, est ~${prompt.length / 4 + 134} tokens")
            AiTextProcessor.stream(
                repository  = repository,
                prompt      = prompt,
                outputFlow  = _quizText as MutableStateFlow<String>,
                userStopped = { userStopped },
                scope       = viewModelScope,
                onDone      = {
                    _uiState.value = QuizUiState.Done
                    viewModelScope.launch(Dispatchers.IO) { autoSave() }
                },
                onError     = { msg -> _uiState.value = QuizUiState.Error(msg) }
            )
        }

        if (job?.isActive == true) {
            // Already inside a coroutine (called from generateFromImage's launch block)
            viewModelScope.launch(Dispatchers.IO) { launch() }
        } else {
            job = viewModelScope.launch(Dispatchers.IO) { launch() }
        }
    }

    private suspend fun autoSave() {
        val text = _quizText.value
        if (text.isBlank()) return
        libraryRepo.save(EntryType.QUIZ, text)
        _showSavedBanner.value = true
        viewModelScope.launch {
            kotlinx.coroutines.delay(2_500)
            _showSavedBanner.value = false
        }
    }

    fun stop() {
        userStopped = true
        repository.stop()
        job?.cancel()
        if (_uiState.value is QuizUiState.Generating) _uiState.value = QuizUiState.Done
    }

    fun reset() {
        stop()
        _quizText.value   = ""
        _sourceText.value = ""
        _uiState.value    = QuizUiState.Idle
    }

    override fun onCleared() {
        super.onCleared()
        extractor.close()
        repository.stop()
        // Do not call repository.unload() — shared instance
    }
}

sealed class QuizUiState {
    object Idle       : QuizUiState()
    object Extracting : QuizUiState()
    object Generating : QuizUiState()
    object Done       : QuizUiState()
    data class Error(val message: String) : QuizUiState()
}
